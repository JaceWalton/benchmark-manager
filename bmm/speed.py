"""Per-model generation speed: measure it, cache it, estimate task time.

Time-per-task at ~5 tok/s is dominated by generation, so a benchmark's wall-clock
is roughly out_tokens / (model's tok/s). We measure each model's real tok/s once
(a short timed generation, using the endpoint's usage counters) and cache it in
`speeds.json`, so the catalogue can show "how long each task will take" per model.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import config

MEASURE_PROMPT = (
    "Write a single detailed paragraph (about 150 words) explaining why unit "
    "testing matters in software engineering. Do not use lists."
)


def load_speeds() -> dict:
    """Return {model: {"tok_s": float, "ts": iso, "n": int}} from speeds.json."""
    if config.SPEEDS_FILE.exists():
        try:
            return json.loads(config.SPEEDS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_speed(model: str, tok_s: float, n_tokens: int) -> None:
    data = load_speeds()
    data[model] = {
        "tok_s": round(tok_s, 2),
        "n": n_tokens,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        config.SPEEDS_FILE.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


def tok_s_for(model: str, speeds: dict | None = None) -> float | None:
    """Measured tok/s for a model, or None if we haven't measured it."""
    speeds = load_speeds() if speeds is None else speeds
    v = speeds.get(model)
    return v.get("tok_s") if v else None


def measure(client, model: str, max_tokens: int = 256, emit=None) -> dict:
    """Time one generation and derive tok/s from the endpoint's usage counters.

    Returns {"model", "tok_s", "n", "latency_s"} on success or {"error": ...}.
    Falls back to a char/4 token estimate if the server omits usage.
    """
    def _say(s):
        if emit:
            emit(s)

    _say(f"[speed] measuring {model} (up to {max_tokens} tokens)…")
    try:
        res = client.chat(
            model,
            [{"role": "user", "content": MEASURE_PROMPT}],
            temperature=0.0, max_tokens=max_tokens,
        )
    except Exception as e:  # noqa: BLE001
        _say(f"[speed] {model}: ERROR {e}")
        return {"model": model, "error": str(e)}

    n = int((res.usage or {}).get("completion_tokens") or 0)
    if not n:  # server didn't report usage; approximate from text length
        n = max(1, len(res.text) // 4)
    dt = res.latency_s or 0.0
    if dt <= 0:
        return {"model": model, "error": "no latency measured"}
    tok_s = n / dt
    save_speed(model, tok_s, n)
    _say(f"[speed] {model}: {tok_s:.1f} tok/s  ({n} tokens in {dt:.0f}s)")
    return {"model": model, "tok_s": tok_s, "n": n, "latency_s": dt}


def est_task_seconds(bench, tok_s: float | None) -> float | None:
    """Rough seconds for one task = generation budget / tok_s (generation-bound)."""
    if not tok_s or tok_s <= 0:
        return None
    return bench.out_tokens / tok_s


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 90:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f}m"
    return f"{minutes / 60:.1f}h"
