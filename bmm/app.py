"""Benchmark Manager (bmm) — interactive TUI + headless CLI.

Interactive:   bmm
Headless:      bmm models | list | run --model M --bench a,b --limit N
               bmm results | compare | selftest
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from . import config, menu, models, registry, results, runner, speed
from .endpoint import Client

console = Console()
config.load_dotenv()

# Subtle alternating row background so long tables are easier to scan.
ZEBRA = ["", "on grey15"]

TIER_DESC = {
    1: "fast / no docker",
    2: "agentic (some need docker)",
    3: "heavy infra / keys",
}


# --- small formatting helpers ----------------------------------------------
def _ftok(n: int) -> str:
    """Compact token count: 800 -> '800', 8192 -> '8k', 1500 -> '1.5k'."""
    if not n:
        return "—"
    if n >= 1000:
        return f"{n/1000:.0f}k" if n % 1000 == 0 else f"{n/1000:.1f}k"
    return str(n)


def _fmt_dur(secs: int) -> str:
    if not secs:
        return "off"
    if secs % 3600 == 0:
        return f"{secs // 3600}h"
    if secs % 60 == 0:
        return f"{secs // 60}m"
    return f"{secs}s"


def _est_minutes(bench: registry.Benchmark, n_tasks: int, tok_s: float | None = None) -> float:
    """Rough wall-clock estimate: generation dominates at `tok_s` tok/s."""
    rate = tok_s if tok_s and tok_s > 0 else config.TOK_PER_SEC
    return (n_tasks * bench.out_tokens) / max(rate, 0.1) / 60.0


def _short_model(m: str, width: int = 14) -> str:
    return m if len(m) <= width else m[: width - 1] + "…"


def _model_tok_s(model: str, speeds: dict | None = None) -> float | None:
    return speed.tok_s_for(model, speeds)


def _speed_cols(models_pref: list[str] | None = None, cap: int = 4):
    """Per-model (header, tok_s) columns for the catalogue's time-per-task view.

    Uses measured speeds from speeds.json; if none are measured yet, falls back
    to a single column at the default rate so the column always exists.
    """
    speeds = speed.load_speeds()
    order = list(models_pref or []) + [m for m in speeds if m not in (models_pref or [])]
    cols = []
    for m in order:
        ts = speeds.get(m, {}).get("tok_s")
        if ts:
            cols.append((f"{_short_model(m)}\n{ts:.1f} t/s", ts))
        if len(cols) >= cap:
            break
    if not cols:
        cols = [(f"est\n@{config.TOK_PER_SEC:g} t/s", config.TOK_PER_SEC)]
    return cols


def _rightstamp(body: Text) -> Text:
    """Return `body` with an HH:MM:SS timestamp pinned to the right margin."""
    ts = datetime.now().strftime("%H:%M:%S")
    pad = console.width - len(body.plain) - len(ts) - 1
    out = body.copy()
    if pad < 1:                       # line too long to right-align; append inline
        out.append(f"  {ts}", style="dim")
    else:
        out.append(" " * pad)
        out.append(ts, style="dim")
    return out


def _stamped_emit(s: str) -> None:
    """Print a streamed line with a right-aligned timestamp (one line, cropped)."""
    console.print(_rightstamp(colorize(s)), no_wrap=True, crop=True)


# --- rendering helpers ------------------------------------------------------
def status_text(b: registry.Benchmark) -> Text:
    s = b.status()
    if s == "READY":
        return Text("READY", style="bold green")
    if s == "NEEDS-INSTALL":
        return Text("install", style="yellow")
    return Text(s.replace("NEEDS:", "need "), style="red")


def score_cell(row: dict | None) -> Text:
    if not row:
        return Text("—", style="dim")
    if row.get("status") == "error":
        return Text("err", style="red")
    sc = row.get("score")
    if sc is None:
        return Text("n/a", style="dim")
    style = "green" if sc >= 0.66 else "yellow" if sc >= 0.33 else "red"
    return Text(f"{sc*100:.0f}%", style=style)


def exec_text(b: registry.Benchmark) -> Text:
    if b.exec == "agent":
        return Text("agent", style="bold magenta")
    return Text("endpoint", style="green")


def catalogue_table(speed_cols=None) -> Table:
    # Index column matches registry order so numeric selection in the run screen
    # lines up. speed_cols: optional list of (header, tok_s) — per-model "time per
    # task" columns (out_tokens / tok_s). Defaults to measured speeds (_speed_cols).
    if speed_cols is None:
        speed_cols = _speed_cols()
    t = Table(title="Benchmark catalogue", header_style="bold cyan", expand=True,
              row_styles=ZEBRA, caption="ctx/in/out = approx tokens (context · "
              "prompt/task · generation/task) · full = whole task set · t/task = est "
              "time for one task at that model's tok/s")
    t.add_column("#", justify="right", style="dim")
    t.add_column("key", style="bold")
    t.add_column("name")
    t.add_column("cat")
    t.add_column("tier", justify="center")
    t.add_column("ctx", justify="right", style="dim")
    t.add_column("in", justify="right", style="dim")
    t.add_column("out", justify="right", style="dim")
    t.add_column("full", justify="right", style="dim")
    for header, _ts in speed_cols:
        t.add_column(header, justify="right", style="cyan")
    t.add_column("run", justify="center")
    t.add_column("status")
    for i, b in enumerate(registry.BENCHMARKS, 1):
        row = [str(i), b.key, b.name, b.category, str(b.tier),
               _ftok(b.ctx_needed), _ftok(b.in_tokens), _ftok(b.out_tokens),
               str(b.max_tasks or "—")]
        for _header, ts in speed_cols:
            row.append(speed.fmt_duration(speed.est_task_seconds(b, ts)))
        row += [exec_text(b), status_text(b)]
        t.add_row(*row)
    return t


def results_table(rows: list[dict], limit: int = 25) -> Table:
    t = Table(title=f"Recent results (newest {min(limit, len(rows))} of {len(rows)})",
              header_style="bold cyan", expand=True, row_styles=ZEBRA)
    t.add_column("#", justify="right", style="dim")
    t.add_column("benchmark", style="bold")
    t.add_column("model")
    t.add_column("score", justify="right")
    t.add_column("n", justify="right")
    t.add_column("status")
    t.add_column("when (UTC)")
    t.add_column("secs", justify="right")
    for i, r in enumerate(rows[:limit], 1):
        st = r.get("status", "?")
        st_style = {"completed": "green", "error": "red",
                    "partial": "yellow", "timeout": "yellow"}.get(st, "white")
        n = f"{r.get('n_pass','?')}/{r.get('n_total','?')}"
        t.add_row(str(i), r.get("benchmark", "?"), r.get("model", "?"),
                  score_cell(r), n, Text(st, style=st_style),
                  (r.get("started", "") or "").replace("T", " ").rstrip("Z"),
                  str(int(r.get("duration_s", 0) or 0)))
    return t


def matrix_table() -> Table:
    models, benches, cell = results.latest_matrix()
    t = Table(title="Model x Benchmark (latest run each, headline score)",
              header_style="bold cyan", expand=True, row_styles=ZEBRA)
    t.add_column("benchmark", style="bold")
    for m in models:
        t.add_column(m, justify="center")
    if not models:
        t.add_row("(no results yet)")
        return t
    for b in benches:
        cells = [score_cell(cell.get((m, b))) for m in models]
        t.add_row(b, *cells)
    return t


def colorize(line: str) -> Text:
    low = line.lower()
    if "pass" in low or "refused" in low or " ok" in low:
        return Text(line, style="green")
    if "fail" in low or "error" in low or "complied" in low:
        return Text(line, style="red")
    if line.startswith("["):
        return Text(line, style="cyan")
    return Text(line, style="dim")


# --- selection helpers ------------------------------------------------------
def resolve_benches(expr: str) -> list[registry.Benchmark]:
    expr = expr.strip().lower()
    all_b = registry.BENCHMARKS
    if expr in ("all",):
        return list(all_b)
    if expr in ("ready",):
        return [b for b in all_b if b.status() == "READY"]
    if expr in ("builtin", "builtins"):
        return [b for b in all_b if b.source == "builtin"]
    if expr.startswith("tier"):
        try:
            tn = int(expr[4:])
            return [b for b in all_b if b.tier == tn]
        except ValueError:
            return []
    picked: list[registry.Benchmark] = []
    for tok in expr.replace(" ", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok.isdigit():
            idx = int(tok) - 1
            if 0 <= idx < len(all_b):
                picked.append(all_b[idx])
        elif tok in registry.BY_KEY:
            picked.append(registry.BY_KEY[tok])
    # de-dup preserve order
    seen, out = set(), []
    for b in picked:
        if b.key not in seen:
            seen.add(b.key)
            out.append(b)
    return out


# --- run orchestration ------------------------------------------------------
def do_run(model_list: list[str], benches: list[registry.Benchmark],
           limit, quiet: bool = False, timeout: int = 0,
           skip: set | None = None) -> list[results.RunResult]:
    """Run each (model × benchmark) pair.

    `limit` is an int or "max" (full task set, resolved per benchmark). `skip`
    is a set of (model, bench_key) pairs to leave out (the re-run guard). Each
    benchmark starts with a right-aligned timestamp and a run-time estimate, so
    a long, quiet run never looks stalled.
    """
    skip = skip or set()
    plan = [(m, b) for m in model_list for b in benches if (m, b.key) not in skip]
    if not plan:
        console.print("[yellow]Nothing to run — every selected pair was skipped.[/]")
        return []
    done = []
    total = len(plan)
    for n, (model, b) in enumerate(plan, 1):
        eff = b.resolve_limit(limit)
        tok_s = _model_tok_s(model)
        rate = tok_s or config.TOK_PER_SEC
        rate_tag = "measured" if tok_s else "est"
        console.rule(style="cyan")
        console.print(_rightstamp(Text(f"({n}/{total}) {b.key} · {model}",
                                       style="bold cyan")))
        console.print(Text(
            f"  {eff} tasks · ctx~{_ftok(b.ctx_needed)} · "
            f"in~{_ftok(b.in_tokens)}/out~{_ftok(b.out_tokens)} per task · "
            f"~{speed.fmt_duration(speed.est_task_seconds(b, rate))}/task · "
            f"~{_est_minutes(b, eff, tok_s):.0f} min total at {rate:g} tok/s "
            f"({rate_tag}) · timeout {_fmt_dur(timeout)}", style="dim"))
        emit = (lambda s: None) if quiet else _stamped_emit
        rr = runner.run(b, model, {"limit": eff, "timeout": timeout}, emit)
        badge = {"completed": "green", "error": "red", "partial": "yellow",
                 "timeout": "yellow"}.get(rr.status, "white")
        console.print(Panel(
            f"score [b]{rr.pct}[/] ({rr.score_label})  ·  "
            f"{rr.n_pass}/{rr.n_total} passed  ·  {rr.duration_s:.0f}s  ·  "
            f"[{badge}]{rr.status}[/]"
            + (f"\n[dim]{rr.error}[/]" if rr.error else ""),
            title=f"{b.name}", border_style=badge))
        done.append(rr)
    return done


# --- interactive screens ----------------------------------------------------
def home_panel(client: Client, disco: dict) -> Panel:
    ready = sum(1 for b in registry.BENCHMARKS if b.status() == "READY")
    n_res = len(results.load_all())
    served, runnable = disco["served"], disco["runnable"]
    # ● = currently advertised by the endpoint; ○ = configured/custom, loads on use
    shown = ", ".join(f"{'●' if m in served else '○'} {m}" for m in runnable[:6])
    more = f"  (+{len(runnable) - 6} more)" if len(runnable) > 6 else ""
    body = (
        f"[cyan]endpoint[/]  {config.ENDPOINT}   "
        f"[{'green' if served else 'red'}]"
        f"{'online: ' + str(len(served)) + ' served' if served else 'OFFLINE'}[/]\n"
        f"[cyan]models  [/]  {shown or '(none — is llama-swap up?)'}{more}\n"
        f"[cyan]benches [/]  {len(registry.BENCHMARKS)} total · "
        f"[green]{ready} READY[/] · {n_res} runs recorded\n"
        f"[cyan]results [/]  {config.RESULTS_DIR}"
    )
    return Panel(body, title="[bold]Benchmark Manager (bmm)[/]",
                 subtitle="● served   ○ configured/custom (loads on use)",
                 border_style="cyan")


def models_table(disco: dict) -> Table:
    t = Table(title="Models", header_style="bold cyan", row_styles=ZEBRA)
    t.add_column("#", justify="right", style="dim")
    t.add_column("model", style="bold")
    t.add_column("source")
    served = set(disco["served"])
    cfg = set(disco["configured"])
    for i, m in enumerate(disco["runnable"], 1):
        if m in served:
            src = Text("● served", style="green")
        elif m in cfg:
            src = Text("○ configured", style="yellow")
        else:
            src = Text("○ custom", style="cyan")
        t.add_row(str(i), m, src)
    return t


# --- ANSI label helpers for the arrow menus (menu.py renders raw strings) ----
_A = {"reset": "\x1b[0m", "bold": "\x1b[1m", "dim": "\x1b[2m", "green": "\x1b[32m",
      "yellow": "\x1b[33m", "red": "\x1b[31m", "magenta": "\x1b[35m", "cyan": "\x1b[36m"}


def _pc(text: str, width: int, *codes: str) -> str:
    return "".join(_A[c] for c in codes) + text.ljust(width) + _A["reset"]


def _model_label(m: str, served: set, cfg: set) -> str:
    if m in served:
        tag = _pc("● served", 13, "green")
    elif m in cfg:
        tag = _pc("○ configured", 13, "yellow")
    else:
        tag = _pc("○ custom", 13, "cyan")
    return f"{m.ljust(42)} {tag}"


def _bench_label(b: registry.Benchmark) -> str:
    ex = _pc("agent", 8, "magenta", "bold") if b.exec == "agent" else _pc("endpoint", 8, "green")
    st = b.status()
    if st == "READY":
        stc = _pc("READY", 12, "green")
    elif st == "NEEDS-INSTALL":
        stc = _pc("install", 12, "yellow")
    else:
        stc = _pc(st.replace("NEEDS:", "need "), 12, "red")
    tok = _pc(f"c{_ftok(b.ctx_needed)} i{_ftok(b.in_tokens)} "
              f"o{_ftok(b.out_tokens)} ×{b.max_tasks or '?'}", 22, "dim")
    return f"{b.key.ljust(15)} {ex} {stc} {tok} {b.name}"


def _add_model_prompt() -> str | None:
    name = Prompt.ask("  new model name (blank = cancel)", default="").strip()
    if not name:
        return None
    models.add_custom(name)
    console.print(f"  [green]added[/] '{name}' (saved to models.json)")
    return name


def _pick_limit(benches: list[registry.Benchmark]):
    """Return an int, the string 'max', or None if the user backs out."""
    biggest = max((b.max_tasks for b in benches), default=0)
    opts = [
        ("default", f"Default — {config.DEFAULT_LIMIT} tasks / benchmark"),
        ("max", f"Max — full task set per benchmark (up to {biggest or '?'})"),
        ("custom", "Custom number…"),
    ]
    _, ch = menu.select("Tasks per benchmark", opts, label=lambda o: o[1])
    if ch is None:
        return None
    if ch[0] == "default":
        return config.DEFAULT_LIMIT
    if ch[0] == "max":
        return "max"
    return IntPrompt.ask("  tasks per benchmark", default=config.DEFAULT_LIMIT)


def _pick_timeout():
    """Return timeout seconds (0 = off) or None if the user backs out."""
    opts = [
        ("0", "No timeout (the stall-guard is always on)"),
        ("1800", "30 minutes per benchmark"),
        ("3600", "1 hour per benchmark"),
        ("custom", "Custom seconds…"),
    ]
    _, ch = menu.select("Per-benchmark timeout", opts, label=lambda o: o[1])
    if ch is None:
        return None
    if ch[0] == "custom":
        return IntPrompt.ask("  timeout seconds (0 = off)", default=0)
    return int(ch[0])


def _rerun_skip(model_list: list[str], benches: list[registry.Benchmark]) -> set:
    """Warn about already-completed (model, bench) pairs; return the set to skip.

    Results are never overwritten — a re-run always writes a new timestamped
    file — so this is purely about not wasting hours re-running what you have.
    """
    dupes = [(m, b, results.already_done(m, b.key))
             for m in model_list for b in benches]
    dupes = [(m, b, prev) for (m, b, prev) in dupes if prev]
    if not dupes:
        return set()
    lines = []
    for m, b, prev in dupes:
        sc = prev.get("score")
        scs = "—" if sc is None else f"{sc*100:.0f}%"
        when = (prev.get("started", "") or "").replace("T", " ").rstrip("Z")
        lines.append(f"[b]{m}[/] × [b]{b.key}[/] — last {scs} on {when}")
    console.print(Panel(
        "\n".join(lines),
        title="[yellow]Already run before[/]",
        subtitle="results are never overwritten — a re-run adds a new timestamped file",
        border_style="yellow"))
    if Confirm.ask("Re-run these already-done pairs too?", default=False):
        return set()
    return {(m, b.key) for (m, b, _prev) in dupes}


def screen_run(client: Client, disco: dict):
    served, cfg = set(disco["served"]), set(disco["configured"])
    model_opts = list(disco["runnable"])
    if disco["gguf"]:
        unconf = [g for g in disco["gguf"]
                  if not any(g.lower().startswith(r.lower()[:8]) for r in model_opts)]
        if unconf:
            console.print(f"[dim]on disk but not served by llama-swap: "
                          f"{', '.join(unconf[:4])}{'…' if len(unconf) > 4 else ''} "
                          f"(press 'n' to benchmark one anyway)[/]")

    while True:  # ---- model step ----
        sel_models = menu.multiselect(
            "Select model(s)   ● served  ○ configured/custom",
            model_opts, label=lambda m: _model_label(m, served, cfg),
            on_new=_add_model_prompt)
        if sel_models is None:
            return                                  # Esc/⌫ → back to home
        if not sel_models:
            console.print("[yellow]Space to toggle at least one model (Esc to go back).[/]")
            continue

        while True:  # ---- benchmark step ----
            console.print(f"[dim]run column:[/] [green]endpoint[/] = bmm runs it by "
                          f"pointing at the model · [magenta]agent[/] = needs your "
                          f"openclaw agent to drive it")
            # preselect the built-ins as a sensible default
            pre = [i for i, b in enumerate(registry.BENCHMARKS) if b.source == "builtin"]
            benches = menu.multiselect(
                "Select benchmark(s) to run",
                registry.BENCHMARKS, label=_bench_label, preselected=pre)
            if benches is None:
                break                                # back to model step
            if not benches:
                console.print("[yellow]Space to toggle at least one benchmark.[/]")
                continue

            agents = [b for b in benches if b.exec == "agent"]
            if agents:
                console.print(Panel(
                    "\n".join(f"[magenta]{b.key}[/] — {b.driver}" for b in agents),
                    title="[yellow]⚠ agent-driven — an agent drives the model, not the "
                          "raw endpoint[/]",
                    subtitle="bundled agent → bmm runs it · 'openclaw must…' → "
                             "see README 'Agent benchmarks'",
                    border_style="yellow"))
            not_ready = [b for b in benches if b.status() != "READY"]
            if not_ready:
                console.print("[yellow]not ready:[/] "
                              + ", ".join(f"{b.key}({b.status()})" for b in not_ready))

            limit = _pick_limit(benches)
            if limit is None:
                continue                             # ⌫ → re-open benchmark picker
            timeout = _pick_timeout()
            if timeout is None:
                continue
            skip = _rerun_skip(sel_models, benches)
            n_pairs = len(sel_models) * len(benches) - len(skip)
            if n_pairs <= 0:
                console.print("[yellow]All selected pairs skipped — nothing to run.[/]")
                continue
            lim_txt = "max" if limit == "max" else str(limit)
            if not Confirm.ask(f"Run {n_pairs} pair(s) · limit {lim_txt} · "
                               f"timeout {_fmt_dur(timeout)}?", default=True):
                continue                             # re-open benchmark picker
            do_run(sel_models, benches, limit, timeout=timeout, skip=skip)
            console.print("\n[green]All runs complete.[/] "
                          "[dim](results & compare are on the home menu)[/]")
            return


def screen_results():
    rows = results.load_all()
    if not rows:
        console.print("[yellow]No results yet — run some benchmarks first.[/]")
        return
    console.print(results_table(rows))
    top = rows[:25]

    def rlabel(r):
        sc = r.get("score")
        scs = "—" if sc is None else f"{sc*100:.0f}%"
        st = r.get("status", "?")
        stc = {"completed": "green", "error": "red", "partial": "yellow",
               "timeout": "yellow"}.get(st, "cyan")
        return (f"{str(r.get('benchmark','?')).ljust(14)} "
                f"{str(r.get('model','?')).ljust(20)} "
                f"{_pc(scs, 6, 'bold')} {_pc(st, 9, stc)}")

    _, row = menu.select("Pick a run for detail  (Esc/⌫ back)", top, label=rlabel)
    if row:
        _detail(row)
        screen_results()


def _detail(row: dict):
    full = results.load_run(row.get("file", "")) or row
    import json as _json
    metrics = full.get("metrics", {})
    # keep detail readable: drop the big per-item arrays from the headline view
    compact = {k: v for k, v in metrics.items()
               if not isinstance(v, list)}
    console.print(Panel(
        f"[cyan]benchmark[/] {full.get('benchmark')}\n"
        f"[cyan]model    [/] {full.get('model')}\n"
        f"[cyan]score    [/] {full.get('score')}  ({full.get('score_label')})\n"
        f"[cyan]passed   [/] {full.get('n_pass')}/{full.get('n_total')}\n"
        f"[cyan]status   [/] {full.get('status')}  {full.get('error','')}\n"
        f"[cyan]duration [/] {full.get('duration_s')}s\n"
        f"[cyan]log      [/] {full.get('log_path','')}\n\n"
        f"[bold]metrics[/]\n{_json.dumps(compact, indent=2)}",
        title="run detail", border_style="cyan"))


def screen_compare():
    console.print(matrix_table())
    console.print("[dim]latest run per (model, benchmark). Green ≥66% · yellow ≥33% "
                  "· red below · — none yet.[/]")


def screen_catalogue():
    console.print(catalogue_table())
    if not speed.load_speeds():
        console.print("[dim]time-per-task columns use the default "
                      f"~{config.TOK_PER_SEC:g} tok/s — use [b]Measure model speed[/] "
                      "on the home menu for real per-model times.[/]")
    console.print(Panel(
        "[bold]run[/] column — how a benchmark is driven:\n"
        "  [green]endpoint[/]  bmm sends prompts to the model and grades it. "
        "Just pick a model and run.\n"
        "  [magenta]agent[/]     measures an [i]agent[/] doing multi-step tasks. "
        "bmm can't drive these by\n"
        "            pointing at the model — your [b]openclaw[/] agent (or a bundled "
        "one) must do the\n"
        "            tasks. See README → 'Two kinds of benchmarks'.\n\n"
        "[bold]status[/] — [green]READY[/]=runnable now · [yellow]install[/]=run an "
        "install script · [red]need …[/]=missing docker/node/key",
        title="Legend", border_style="cyan"))
    for tier, benches in registry.by_tier().items():
        console.print(f"\n[bold cyan]Tier {tier}[/] ({TIER_DESC.get(tier,'')}):")
        for b in benches:
            tag = ("[magenta]agent[/]" if b.exec == "agent" else "[green]endpoint[/]")
            console.print(f"  [b]{b.key}[/] ({tag}) — {b.description}")
            if b.driver:
                console.print(f"      [dim]↳ {b.driver}[/]")


def screen_run_all_max(client: Client, disco: dict):
    """One-shot sweep: every runnable model × every READY endpoint benchmark, at
    full task sets. This is the 'run all endpoint models at their max limit'
    button — big, so it summarizes the plan and defaults the confirm to No."""
    model_list = list(disco["runnable"])
    benches = [b for b in registry.BENCHMARKS
               if b.exec == "endpoint" and b.status() == "READY"]
    if not model_list or not benches:
        console.print("[yellow]Need at least one model and one READY endpoint "
                      "benchmark for this.[/]")
        return
    total_tasks = sum(b.resolve_limit("max") for b in benches) * len(model_list)
    est_h = sum(_est_minutes(b, b.resolve_limit("max"), _model_tok_s(m))
                for m in model_list for b in benches) / 60.0
    any_measured = any(_model_tok_s(m) for m in model_list)
    console.print(Panel(
        f"[b]{len(model_list)}[/] model(s) × [b]{len(benches)}[/] READY endpoint "
        f"benchmark(s) at [b]full task sets[/]\n"
        f"[cyan]models [/] {', '.join(model_list)}\n"
        f"[cyan]benches[/] {', '.join(b.key for b in benches)}\n"
        f"~{total_tasks} tasks total · very rough [b]~{est_h:.1f} h[/] "
        f"({'measured' if any_measured else f'est @{config.TOK_PER_SEC:g}'} tok/s, "
        f"generation only; each model swap reloads a GGUF)",
        title="[yellow]Run ALL endpoint models · max tasks[/]", border_style="yellow"))
    timeout = _pick_timeout()
    if timeout is None:
        return
    skip = _rerun_skip(model_list, benches)
    if len(model_list) * len(benches) - len(skip) <= 0:
        console.print("[yellow]Everything already done — nothing to run.[/]")
        return
    if not Confirm.ask("Start the full sweep?", default=False):
        return
    do_run(model_list, benches, "max", timeout=timeout, skip=skip)
    console.print("\n[green]Sweep complete.[/]")


def speed_table(speeds: dict | None = None) -> Table:
    speeds = speeds if speeds is not None else speed.load_speeds()
    t = Table(title="Measured model speed", header_style="bold cyan", row_styles=ZEBRA)
    t.add_column("model", style="bold")
    t.add_column("tok/s", justify="right")
    t.add_column("sample", justify="right", style="dim")
    t.add_column("measured (UTC)", style="dim")
    if not speeds:
        t.add_row("(none measured yet — run 'bmm speed' or the TUI 'Measure model speed')",
                  "", "", "")
        return t
    for m, v in sorted(speeds.items(), key=lambda kv: -kv[1].get("tok_s", 0)):
        t.add_row(m, f"{v.get('tok_s', 0):.1f}", f"{v.get('n', 0)} tok",
                  (v.get("ts", "") or "").replace("T", " ").rstrip("Z"))
    return t


def screen_speed(client: Client, disco: dict):
    """Measure per-model generation speed so the catalogue can show real
    time-per-task. Each measurement loads the model + times a short generation."""
    model_opts = list(disco["runnable"])
    if not model_opts:
        console.print("[yellow]No models discovered — is llama-swap up?[/]")
        return
    speeds = speed.load_speeds()

    def lbl(m):
        ts = speeds.get(m, {}).get("tok_s")
        tag = (_pc(f"{ts:.1f} t/s", 14, "green") if ts
               else _pc("not measured", 14, "yellow"))
        return f"{m.ljust(42)} {tag}"

    sel = menu.multiselect(
        "Measure speed for which model(s)?", model_opts, label=lbl,
        footer="↑/↓ move · Space toggle · a all · Enter confirm · Esc/⌫ back")
    if not sel:
        return
    console.print(f"[dim]measuring {len(sel)} model(s) — each is a model swap + a "
                  f"~256-token generation (≈1 min each at ~5 tok/s)…[/]")
    for m in sel:
        console.rule(f"[cyan]{m}[/]")
        speed.measure(client, m, emit=lambda s: console.print(colorize(s)))
    console.print()
    console.print(speed_table())
    console.print("[dim]The catalogue now shows a time-per-task column for these "
                  "models (home → Catalogue, or `bmm list`).[/]")


HOME_MENU = [
    ("run", "Run benchmarks"),
    ("runmax", "Run ALL endpoint models · max tasks  (every READY endpoint bench)"),
    ("speed", "Measure model speed(s) → per-task time estimates"),
    ("results", "View results"),
    ("compare", "Compare models"),
    ("catalogue", "Catalogue / status  (which need an agent?)"),
    ("quit", "Quit"),
]


def interactive():
    client = Client()
    while True:
        disco = models.discover(client)
        console.print()
        console.print(home_panel(client, disco))
        _, choice = menu.select(
            "", HOME_MENU, label=lambda it: it[1],
            footer="↑/↓ move · Enter select · q quit")
        tag = choice[0] if choice else "quit"
        if tag == "run":
            screen_run(client, disco)
        elif tag == "runmax":
            screen_run_all_max(client, disco)
        elif tag == "speed":
            screen_speed(client, disco)
        elif tag == "results":
            screen_results()
        elif tag == "compare":
            screen_compare()
        elif tag == "catalogue":
            screen_catalogue()
        else:
            console.print("bye.")
            return


# --- headless CLI -----------------------------------------------------------
def cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="bmm", description="Benchmark Manager")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("models", help="list models the endpoint exposes")
    sub.add_parser("list", help="list benchmarks + status")
    sub.add_parser("results", help="recent results table")
    sub.add_parser("compare", help="model x benchmark matrix")
    sub.add_parser("selftest", help="tiny end-to-end check vs the endpoint")

    ps = sub.add_parser("speed", help="measure per-model tok/s (for time-per-task estimates)")
    ps.add_argument("--model", default="all", help="model(s), comma-separated, or 'all'")
    ps.add_argument("--max-tokens", type=int, default=256)

    pr = sub.add_parser("run", help="run benchmarks headless")
    pr.add_argument("--model", required=True,
                    help="model alias(es), comma-separated, or 'all'")
    pr.add_argument("--bench", required=True,
                    help="keys/indices, or builtins|tier1|ready|all")
    pr.add_argument("--limit", default=str(config.DEFAULT_LIMIT),
                    help="tasks per benchmark: a number or 'max' (full task set)")
    pr.add_argument("--timeout", type=int, default=config.RUN_TIMEOUT_S,
                    help="per-benchmark wall-clock cap in seconds (0 = off)")
    pr.add_argument("--skip-existing", action="store_true",
                    help="skip (model, bench) pairs already completed")
    pr.add_argument("--quiet", action="store_true")

    args = p.parse_args(argv)
    if args.cmd is None:
        interactive()
        return 0

    client = Client()
    if args.cmd == "models":
        disco = models.discover(client)
        if disco["runnable"]:
            console.print(models_table(disco))
            if disco["gguf"]:
                console.print(f"[dim]gguf on disk: {', '.join(disco['gguf'])}[/]")
        else:
            console.print("[red]no models / endpoint offline[/]")
    elif args.cmd == "list":
        console.print(catalogue_table())
        if not speed.load_speeds():
            console.print("[dim]time-per-task shown at the default "
                          f"~{config.TOK_PER_SEC:g} tok/s — run [b]bmm speed[/] to "
                          "measure real per-model times.[/]")
    elif args.cmd == "results":
        console.print(results_table(results.load_all()))
    elif args.cmd == "compare":
        console.print(matrix_table())
    elif args.cmd == "speed":
        if args.model in ("all", "all-endpoint"):
            ml = models.discover(client)["runnable"]
        else:
            ml = [m.strip() for m in args.model.split(",") if m.strip()]
        if not ml:
            console.print("[red]no models to measure[/]")
            return 2
        for m in ml:
            speed.measure(client, m, max_tokens=args.max_tokens,
                          emit=lambda s: console.print(s))
        console.print(speed_table())
    elif args.cmd == "selftest":
        return _selftest(client)
    elif args.cmd == "run":
        if args.model in ("all", "all-endpoint"):
            model_list = models.discover(client)["runnable"]
        else:
            model_list = [m.strip() for m in args.model.split(",") if m.strip()]
        benches = resolve_benches(args.bench)
        if not benches:
            console.print("[red]no benchmarks matched[/]")
            return 2
        limit = "max" if str(args.limit).lower() == "max" else int(args.limit)
        skip = set()
        if args.skip_existing:
            skip = {(m, b.key) for m in model_list for b in benches
                    if results.already_done(m, b.key)}
            if skip:
                console.print(f"[dim]skipping {len(skip)} already-completed pair(s) "
                              f"(--skip-existing)[/]")
        do_run(model_list, benches, limit, quiet=args.quiet,
               timeout=args.timeout, skip=skip)
    return 0


def _selftest(client: Client) -> int:
    console.print(Panel("bmm selftest — plumbing check", border_style="cyan"))
    ms = client.models()
    console.print(f"endpoint: {config.ENDPOINT}")
    console.print(f"models  : {ms or '[red]NONE[/]'}")
    if not ms:
        console.print("[red]FAIL:[/] endpoint returned no models. Is llama-swap up?")
        return 1
    model = min(ms, key=len)  # smallest-named ≈ smallest model heuristic
    console.print(f"running tc-mini (limit 1) on [b]{model}[/] …")
    do_run([model], [registry.BY_KEY["tc-mini"]], limit=1)
    console.print("[green]selftest complete[/] — results saved.")
    return 0


def main():
    try:
        sys.exit(cli(sys.argv[1:]))
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
