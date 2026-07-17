"""Model discovery + a small persistent registry of user-added models.

Runnable model aliases come from three places, merged:
  1. the endpoint's /v1/models  (what llama-swap will serve right now)
  2. the llama-swap config.yaml  (all configured aliases, incl. ones not yet
     loaded — e.g. the 40B)
  3. ~/benchmarks/models.json    (names the user added in the TUI)

GGUF files in ~/models are surfaced separately as "on disk but not wired into
llama-swap" so the user knows what exists without pretending they're runnable.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config

MODELS_FILE = config.ROOT / "models.json"
MODELS_DIR = Path(config.__file__).resolve()  # placeholder, replaced below
MODELS_DIR = Path.home() / "models"
SWAP_CONFIG = Path.home() / "llama-swap" / "config.yaml"


def _read_custom() -> list[str]:
    if MODELS_FILE.exists():
        try:
            return list(dict.fromkeys(json.loads(MODELS_FILE.read_text())))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def add_custom(name: str) -> bool:
    """Persist a user-typed model name. Returns True if newly added."""
    name = name.strip()
    if not name:
        return False
    cur = _read_custom()
    if name in cur:
        return False
    cur.append(name)
    MODELS_FILE.write_text(json.dumps(cur, indent=2))
    return True


def swap_config_models() -> list[str]:
    """Parse the top-level alias keys under `models:` in the llama-swap config."""
    if not SWAP_CONFIG.exists():
        return []
    out, in_models = [], False
    for line in SWAP_CONFIG.read_text().splitlines():
        if re.match(r"^models:\s*$", line):
            in_models = True
            continue
        if in_models:
            if re.match(r"^\S", line):        # dedent to column 0 => section end
                break
            m = re.match(r"^  ([A-Za-z0-9._\-]+):\s*$", line)
            if m:
                out.append(m.group(1))
    return out


def gguf_on_disk() -> list[str]:
    """GGUF files under ~/models (first shard only), as basenames."""
    if not MODELS_DIR.exists():
        return []
    seen = []
    for p in sorted(MODELS_DIR.rglob("*.gguf")):
        # collapse multi-part shards to one entry
        base = re.sub(r"-\d{5}-of-\d{5}", "", p.name)
        if base not in seen:
            seen.append(base)
    return seen


def discover(client) -> dict:
    """Return runnable + informational model lists with provenance.

    runnable: ordered union of served + configured + custom (dedup).
    served:   subset currently advertised by the endpoint (● marker in UI).
    """
    served = client.models() if client else []
    configured = swap_config_models()
    custom = _read_custom()
    runnable, seen = [], set()
    for name in [*served, *configured, *custom]:
        if name not in seen:
            seen.add(name)
            runnable.append(name)
    return {
        "runnable": runnable,
        "served": served,
        "configured": configured,
        "custom": custom,
        "gguf": gguf_on_disk(),
    }
