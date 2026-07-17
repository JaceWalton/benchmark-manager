"""Built-in, zero-dependency benchmarks.

Each module exposes:
    KEY, NAME, CATEGORY, TIER, DESCRIPTION  (metadata)
    run(client, model, opts, emit) -> dict  with keys:
        score (0..1 | None), score_label, n_total, n_pass, metrics(dict)

These run entirely against the OpenAI-compatible endpoint, so `bmm` produces
real numbers on day one — before any external harness is installed.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# --- shared parsing helpers -------------------------------------------------

_CODE_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(text: str) -> str:
    """Pull the first fenced code block, else return the raw text."""
    m = _CODE_FENCE.search(text or "")
    return (m.group(1) if m else (text or "")).strip()


def extract_number(text: str):
    """Return the last number in the text (GSM8K-style final answer)."""
    if not text:
        return None
    # prefer an explicit "#### 42" or "answer: 42" tail
    for pat in (r"####\s*(-?[\d,]+\.?\d*)", r"answer\s*[:=]\s*(-?[\d,]+\.?\d*)"):
        m = re.findall(pat, text, re.IGNORECASE)
        if m:
            return _to_float(m[-1])
    nums = re.findall(r"-?\d[\d,]*\.?\d*", text)
    return _to_float(nums[-1]) if nums else None


def _to_float(s: str):
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def extract_choice(text: str, choices: list[str]):
    """Find a single letter answer (A/B/C/D) in a response."""
    if not text:
        return None
    m = re.search(r"\b([A-D])\b", text.strip().upper())
    if m:
        return m.group(1)
    # last resort: match the choice text
    low = text.lower()
    for i, c in enumerate(choices):
        if c.lower() in low:
            return "ABCD"[i]
    return None


def parse_tool_call(result) -> tuple[str | None, dict]:
    """Return (function_name, arguments) from a ChatResult.

    Prefers structured tool_calls; falls back to a JSON object in the text
    (for servers not started with --jinja tool parsing).
    """
    if result.tool_calls:
        fn = result.tool_calls[0].get("function", {}) or {}
        name = fn.get("name")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        return name, args
    # textual fallback: look for {"name": ..., "arguments": {...}} or a bare call
    text = result.text or ""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None, {}
        name = obj.get("name") or obj.get("function") or obj.get("tool")
        args = obj.get("arguments") or obj.get("args") or obj.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return name, args
    return None, {}


def run_python(candidate: str, test: str, timeout: int = 12) -> tuple[bool, str]:
    """Execute candidate+test code in a fresh subprocess. Returns (passed, detail)."""
    src = candidate + "\n\n" + test + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(src)
        path = fh.name
    try:
        proc = subprocess.run(
            [sys.executable, path],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode == 0:
            return True, "ok"
        return False, (proc.stderr or proc.stdout or "nonzero exit").strip()[-300:]
    except subprocess.TimeoutExpired:
        return False, f"timeout>{timeout}s"
    except Exception as e:  # noqa: BLE001
        return False, f"exec error: {e}"
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass
