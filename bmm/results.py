"""Result storage, indexing and comparison.

One JSON file per run at:
    results/<benchmark>/<model>__<UTCstamp>.json
plus a rolled-up results/index.jsonl for fast listing/comparison.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

INDEX = config.RESULTS_DIR / "index.jsonl"


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "unknown"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class RunResult:
    benchmark: str                      # registry key
    model: str
    harness: str = "builtin"            # builtin | bfcl | evalplus | ...
    category: str = ""
    tier: int = 0
    endpoint: str = config.ENDPOINT
    started: str = ""
    finished: str = ""
    duration_s: float = 0.0
    status: str = "completed"           # completed | error | partial | timeout
    score: float | None = None          # normalized headline, 0..1 (None if n/a)
    score_label: str = "score"
    n_total: int = 0
    n_pass: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    opts: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    log_path: str = ""
    version: int = config.BMM_RESULT_VERSION

    # -- convenience ---------------------------------------------------------
    @property
    def pct(self) -> str:
        if self.score is None:
            return "—"
        return f"{self.score * 100:.1f}%"

    def path(self) -> Path:
        d = config.RESULTS_DIR / _slug(self.benchmark)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{_slug(self.model)}__{self.started or utc_stamp()}.json"

    def save(self) -> Path:
        p = self.path()
        p.write_text(json.dumps(asdict(self), indent=2))
        _append_index(self)
        return p


def _append_index(r: RunResult) -> None:
    row = {
        "benchmark": r.benchmark, "model": r.model, "harness": r.harness,
        "category": r.category, "tier": r.tier, "status": r.status,
        "score": r.score, "score_label": r.score_label,
        "n_total": r.n_total, "n_pass": r.n_pass,
        "started": r.started, "duration_s": round(r.duration_s, 1),
        "file": str(r.path().relative_to(config.RESULTS_DIR)),
    }
    with INDEX.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def load_all() -> list[dict]:
    """Return every recorded run, newest first. Rebuilds from files if needed."""
    rows: list[dict] = []
    if INDEX.exists():
        for line in INDEX.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if not rows:  # fall back to scanning result files
        for p in config.RESULTS_DIR.glob("*/*.json"):
            try:
                d = json.loads(p.read_text())
                d["file"] = str(p.relative_to(config.RESULTS_DIR))
                rows.append(d)
            except (json.JSONDecodeError, OSError):
                pass
    rows.sort(key=lambda r: r.get("started", ""), reverse=True)
    return rows


def load_run(rel_file: str) -> dict | None:
    p = config.RESULTS_DIR / rel_file
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def latest_matrix() -> tuple[list[str], list[str], dict[tuple[str, str], dict]]:
    """Build a (models, benchmarks, cell) matrix using each pair's newest run.

    cell maps (model, benchmark) -> the newest index row for that pair.
    """
    rows = load_all()  # newest first
    cell: dict[tuple[str, str], dict] = {}
    models: list[str] = []
    benches: list[str] = []
    for r in rows:
        key = (r.get("model", "?"), r.get("benchmark", "?"))
        if key not in cell:                # first hit = newest = keep
            cell[key] = r
        if r.get("model") not in models:
            models.append(r.get("model"))
        if r.get("benchmark") not in benches:
            benches.append(r.get("benchmark"))
    return sorted(models), sorted(benches), cell


def new_run(benchmark: str, model: str, **kw) -> RunResult:
    return RunResult(
        benchmark=benchmark, model=model,
        started=utc_stamp(), endpoint=config.ENDPOINT, **kw,
    )


# --- "already run?" guard ---------------------------------------------------
def previous_runs(model: str, benchmark: str, only_completed: bool = True) -> list[dict]:
    """Prior recorded runs for this exact (model, benchmark), newest first.

    Used to warn before re-running. We never overwrite: each run is a new file
    stamped with its own start time, so a re-run always adds a fresh result.
    """
    out = []
    for r in load_all():  # already newest-first
        if r.get("model") == model and r.get("benchmark") == benchmark:
            if only_completed and r.get("status") != "completed":
                continue
            out.append(r)
    return out


def already_done(model: str, benchmark: str) -> dict | None:
    """The newest completed run for (model, benchmark), or None."""
    runs = previous_runs(model, benchmark, only_completed=True)
    return runs[0] if runs else None


# --- consolidated error log (for later GitHub upload) -----------------------
def log_error(rr: "RunResult", log_tail: str = "") -> None:
    """Append one JSON line describing a failed/partial/timed-out run.

    Kept separate from per-run logs so all failures across every model/benchmark
    can be bundled and sent upstream in one file (config.ERROR_LOG). Includes a
    short log tail so each record is self-contained.
    """
    rec = {
        "ts": utc_stamp(),
        "benchmark": rr.benchmark,
        "model": rr.model,
        "harness": rr.harness,
        "status": rr.status,
        "error": rr.error,
        "duration_s": round(rr.duration_s, 1),
        "opts": rr.opts,
        "endpoint": rr.endpoint,
        "log_path": rr.log_path,
        "log_tail": (log_tail or "")[-4000:],
    }
    try:
        with config.ERROR_LOG.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def read_log_tail(path: str, n_lines: int = 40) -> str:
    """Best-effort tail of a run's log file (for error records / UI)."""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
        return "\n".join(lines[-n_lines:])
    except OSError:
        return ""
