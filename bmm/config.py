"""Central configuration for Benchmark Manager (bmm).

Everything is filesystem-relative to ~/benchmarks so the tree is portable.
Endpoint / key can be overridden with env vars (BMM_ENDPOINT, BMM_API_KEY).
"""
from __future__ import annotations

import os
from pathlib import Path

# --- locations --------------------------------------------------------------
ROOT = Path(os.environ.get("BMM_ROOT", Path(__file__).resolve().parent.parent))
HARNESS_DIR = ROOT / "harnesses"
RESULTS_DIR = ROOT / "results"
LOG_DIR = ROOT / "logs"
ENV_FILE = ROOT / ".env"

for _d in (HARNESS_DIR, RESULTS_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- serving endpoint (llama-swap, OpenAI-compatible) -----------------------
# llama-swap listens on :9292 and hot-swaps the GGUF backing each alias.
ENDPOINT = os.environ.get("BMM_ENDPOINT", "http://127.0.0.1:9292/v1")
# llama-swap needs no auth, but harnesses insist on a non-empty key.
API_KEY = os.environ.get("BMM_API_KEY", "sk-local")

# Request defaults. Generation here is memory-bandwidth bound (~5 tok/s on the
# 40B), so timeouts are generous and default sample sizes are deliberately small.
REQUEST_TIMEOUT = int(os.environ.get("BMM_TIMEOUT", "1800"))   # seconds/request
DEFAULT_LIMIT = int(os.environ.get("BMM_DEFAULT_LIMIT", "20")) # tasks per bench
DEFAULT_TEMPERATURE = float(os.environ.get("BMM_TEMPERATURE", "0.0"))
DEFAULT_MAX_TOKENS = int(os.environ.get("BMM_MAX_TOKENS", "2048"))

# --- run liveness / timeouts ------------------------------------------------
# Generation is ~5 tok/s on this box, so "slow" is normal. These only guard
# against a genuinely *stalled* run and give the UI a heartbeat so a long,
# quiet benchmark never looks frozen.
#
# IDLE_TIMEOUT_S is a last-resort guard against a truly wedged process, NOT a
# progress cap: a single task here can generate for many minutes with no stdout,
# so it defaults to 1 hour. (It was 30 min, which false-killed slow-but-healthy
# bfcl/tau2/cyberseceval runs — see logs/errors.jsonl.) The wrappers also stream
# their own per-task output now, which resets this timer. Set 0 to disable.
HEARTBEAT_S    = int(os.environ.get("BMM_HEARTBEAT", "20"))       # print elapsed every N s of silence
IDLE_TIMEOUT_S = int(os.environ.get("BMM_IDLE_TIMEOUT", "3600"))  # kill a harness silent this long (0=off)
RUN_TIMEOUT_S  = int(os.environ.get("BMM_RUN_TIMEOUT", "0"))      # overall wall-clock cap per benchmark (0=off)
TOK_PER_SEC    = float(os.environ.get("BMM_TOK_PER_SEC", "5"))    # fallback tok/s for estimates (per-model measured in speeds.json)
SPEEDS_FILE    = ROOT / "speeds.json"                             # measured per-model tok/s cache

# Consolidated machine-readable error log (one JSON object per line) so failed
# runs can be bundled and sent to GitHub later. See results.log_error().
ERROR_LOG = LOG_DIR / "errors.jsonl"

BMM_RESULT_VERSION = 2


def load_dotenv() -> dict[str, str]:
    """Read ~/benchmarks/.env (KEY=VALUE lines) into os.environ if present.

    Used for optional secrets (search API keys, HF token, etc.). Never
    overwrites values already set in the real environment.
    """
    found: dict[str, str] = {}
    if not ENV_FILE.exists():
        return found
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        found[key] = val
        os.environ.setdefault(key, val)
    return found
