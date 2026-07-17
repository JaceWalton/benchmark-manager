"""Execute a benchmark and persist a RunResult.

Handles both built-ins (in-process callable) and harness wrappers (subprocess
that emits a final BMM_RESULT= line). `emit` streams progress to the caller
(the TUI panel or the headless console).

Liveness & safety:
  * harness stdout is read on a background thread and split on BOTH '\n' and
    '\r', so tqdm-style progress bars surface live instead of looking frozen;
  * if a run goes quiet, a heartbeat line is emitted every config.HEARTBEAT_S;
  * a harness silent for config.IDLE_TIMEOUT_S is treated as stalled and killed;
  * an optional overall wall-clock cap (opts['timeout'] or config.RUN_TIMEOUT_S)
    stops a run — hard-killing a harness, or cooperatively stopping a built-in
    via a deadline it checks between tasks;
  * every error / partial / timeout is appended to config.ERROR_LOG.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from typing import Callable

from . import config, results
from .endpoint import Client
from .registry import Benchmark

Emit = Callable[[str], None]

_SPLIT = re.compile(r"(\r|\n)")


def _logfile(bench_key: str, model: str) -> str:
    safe_m = model.replace("/", "_")
    p = config.LOG_DIR / f"{bench_key}__{safe_m}__{results.utc_stamp()}.log"
    return str(p)


def _timeout_s(opts: dict) -> int:
    """Overall wall-clock cap for this run (0 = off)."""
    t = opts.get("timeout")
    if t is None:
        t = config.RUN_TIMEOUT_S
    try:
        return max(0, int(t))
    except (TypeError, ValueError):
        return 0


def run(bench: Benchmark, model: str, opts: dict, emit: Emit) -> results.RunResult:
    if bench.source == "builtin":
        return _run_builtin(bench, model, opts, emit)
    return _run_harness(bench, model, opts, emit)


def _mk_emit(emit: Emit, log: str) -> Emit:
    """Display the clean line; write a timestamped copy to the run's log."""
    def _emit(line: str):
        emit(line)
        ts = time.strftime("%H:%M:%S")
        try:
            with open(log, "a") as fh:
                fh.write(f"[{ts}] {line}\n")
        except OSError:
            pass
    return _emit


def _finish(rr, _emit, t0):
    rr.duration_s = time.time() - t0
    rr.finished = results.utc_stamp()
    _emit(f"[{rr.benchmark}] done in {rr.duration_s:.0f}s  "
          f"score={rr.pct}  status={rr.status}")
    if rr.status in ("error", "partial", "timeout"):
        results.log_error(rr, results.read_log_tail(rr.log_path))
    rr.save()
    return rr


# --- built-in ---------------------------------------------------------------
def _run_builtin(bench: Benchmark, model: str, opts: dict, emit: Emit):
    rr = results.new_run(bench.key, model, harness="builtin",
                         category=bench.category, tier=bench.tier, opts=opts)
    log = _logfile(bench.key, model)
    rr.log_path = log
    base_emit = _mk_emit(emit, log)
    client = Client()
    t0 = time.time()

    # Heartbeat: a single task here is one blocking LLM call that can take
    # minutes at ~5 tok/s. A background ticker proves it isn't stalled if the
    # built-in hasn't emitted anything for a while.
    last = [time.time()]

    def _emit(line: str):
        last[0] = time.time()
        base_emit(line)

    stop = threading.Event()

    def _heartbeat():
        while not stop.wait(1.0):
            quiet = time.time() - last[0]
            if quiet >= config.HEARTBEAT_S:
                _emit(f"  … still working ({int(time.time() - t0)}s elapsed, "
                      f"{int(quiet)}s since last output)")

    # Cooperative timeout: pass a deadline the built-in checks between tasks.
    timeout_s = _timeout_s(opts)
    run_opts = dict(opts)
    run_opts["deadline"] = (t0 + timeout_s) if timeout_s else None

    _emit(f"[{bench.key}] {bench.name} on {model} (limit={opts.get('limit')}"
          f"{f', timeout {timeout_s}s' if timeout_s else ''})")
    threading.Thread(target=_heartbeat, daemon=True).start()
    try:
        out = bench.run_fn(client, model, run_opts, _emit)
        rr.score = out.get("score")
        rr.score_label = out.get("score_label", "score")
        rr.n_total = out.get("n_total", 0)
        rr.n_pass = out.get("n_pass", 0)
        rr.metrics = out.get("metrics", {})
        # Only call it a timeout if we actually cut tasks short (attempted fewer
        # than requested). Running slightly over budget on the last task is fine.
        requested = int(opts.get("limit") or 0)
        cap = bench.max_tasks or requested
        requested = min(requested, cap) if requested else cap
        if timeout_s and requested and rr.n_total < requested:
            rr.status = "timeout"
            rr.error = (f"stopped at time budget ({timeout_s}s) after "
                        f"{rr.n_total}/{requested} tasks — partial result")
        else:
            rr.status = "completed"
    except Exception as e:  # noqa: BLE001
        rr.status = "error"
        rr.error = str(e)
        _emit(f"ERROR: {e}")
    finally:
        stop.set()
    return _finish(rr, _emit, t0)


# --- harness ----------------------------------------------------------------
def _reader(fd: int, q: "queue.Queue") -> None:
    """Read a pipe fd, emitting (kind, text) segments split on \\n and \\r.

    kind is 'progress' for a carriage-return-terminated chunk (tqdm bars) and
    'line' for a newline-terminated one. A trailing (None, None) marks EOF.
    """
    buf = ""
    try:
        while True:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            buf += data.decode("utf-8", "replace")
            parts = _SPLIT.split(buf)
            buf = parts.pop()               # incomplete tail stays buffered
            it = iter(parts)
            for text, sep in zip(it, it):
                q.put(("progress" if sep == "\r" else "line", text))
    finally:
        if buf:
            q.put(("line", buf))
        q.put((None, None))


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _run_harness(bench: Benchmark, model: str, opts: dict, emit: Emit):
    rr = results.new_run(bench.key, model, harness=bench.key,
                         category=bench.category, tier=bench.tier, opts=opts)
    log = _logfile(bench.key, model)
    rr.log_path = log
    _emit = _mk_emit(emit, log)
    t0 = time.time()

    if not bench.installed():
        rr.status = "error"
        rr.error = "not installed — run install/install-tier%d.sh" % bench.tier
        _emit(rr.error)
        return _finish(rr, _emit, t0)

    miss = bench.missing_requirements()
    if miss:
        rr.status = "error"
        rr.error = "missing requirements: " + ", ".join(miss)
        _emit(rr.error + " — see SETUP.md")
        return _finish(rr, _emit, t0)

    env = dict(os.environ)
    env.update({
        "BMM_MODEL": model,
        "BMM_ENDPOINT": config.ENDPOINT,
        "BMM_API_KEY": config.API_KEY,
        "BMM_LIMIT": str(opts.get("limit") or config.DEFAULT_LIMIT),
        "PYTHONUNBUFFERED": "1",
    })
    timeout_s = _timeout_s(opts)
    overall_deadline = (t0 + timeout_s) if timeout_s else None
    idle_s = config.IDLE_TIMEOUT_S

    _emit(f"[{bench.key}] launching {bench.wrapper}"
          + (f"  (timeout {timeout_s}s)" if timeout_s else ""))

    payload = None
    killed = None
    try:
        proc = subprocess.Popen(
            ["bash", str(bench.wrapper)],
            cwd=str(bench.harness_dir), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
    except Exception as e:  # noqa: BLE001
        rr.status = "error"
        rr.error = str(e)
        _emit(f"ERROR: {e}")
        return _finish(rr, _emit, t0)

    q: "queue.Queue" = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stdout.fileno(), q),
                     daemon=True).start()

    last_output = time.time()
    last_progress = 0.0
    while True:
        try:
            kind, text = q.get(timeout=config.HEARTBEAT_S)
        except queue.Empty:
            now = time.time()
            idle = now - last_output
            elapsed = now - t0
            # An explicit overall cap is the user's instruction — honor it first.
            if overall_deadline and now >= overall_deadline:
                killed = "timeout"
                _emit(f"  ✖ exceeded {timeout_s}s wall-clock — killing.")
                _kill(proc)
                break
            if idle_s and idle >= idle_s:
                killed = "stalled"
                _emit(f"  ✖ no output for {int(idle)}s — assuming stalled, killing.")
                _kill(proc)
                break
            _emit(f"  … still running ({int(elapsed)}s elapsed, "
                  f"{int(idle)}s since last output)")
            continue

        if kind is None:                     # EOF
            break
        now = time.time()
        last_output = now
        if overall_deadline and now >= overall_deadline:
            killed = "timeout"
            _emit(f"  ✖ exceeded {timeout_s}s wall-clock — killing.")
            _kill(proc)
            break

        line = (text or "").rstrip()
        if not line:
            continue
        if line.startswith("BMM_RESULT="):
            try:
                payload = json.loads(line[len("BMM_RESULT="):])
            except json.JSONDecodeError:
                _emit("WARN: could not parse BMM_RESULT line")
        elif kind == "progress":
            # tqdm bars update many times/sec — throttle so they don't flood.
            if now - last_progress >= 3:
                _emit(line)
                last_progress = now
        else:
            _emit(line)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill(proc)

    if killed == "timeout":
        rr.status = "timeout"
        rr.error = f"exceeded overall timeout ({timeout_s}s) — killed"
    elif killed == "stalled":
        rr.status = "timeout"
        rr.error = f"no output for {idle_s}s — assumed stalled, killed"
    elif payload:
        rr.score = payload.get("score")
        rr.score_label = payload.get("score_label", "score")
        rr.n_total = payload.get("n_total", 0)
        rr.n_pass = payload.get("n_pass", 0)
        rr.metrics = payload.get("metrics", {})
        rr.status = payload.get("status", "completed")
    else:
        rr.status = "partial"
        rr.error = "harness finished without emitting BMM_RESULT= (see log)"
        _emit(rr.error)

    return _finish(rr, _emit, t0)
