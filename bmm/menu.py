"""Arrow-key cursor menus (no external deps; stdlib termios).

Keys:  ↑/↓ (or k/j) move · Enter select · Space toggle (multi) · a = all/none
       Backspace / Esc / q = go back

Falls back to numbered `input()` when stdin/stdout isn't a TTY, so the headless
CLI and piped/automated use keep working.
"""
from __future__ import annotations

import os
import re
import select as _select
import shutil
import sys
from typing import Callable, Iterable

# --- tiny ANSI palette ------------------------------------------------------
_R = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def _is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (ValueError, AttributeError):
        return False


# --- raw key reader ---------------------------------------------------------
def _read_key() -> str:
    """Return a normalized key token: up/down/left/right/enter/space/back/quit
    or the raw character for anything else.

    Uses os.read on the raw fd (not sys.stdin.read) — the buffered text stream
    is unreliable under termios raw mode.
    """
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        b = os.read(fd, 1)
        if b == b"\x1b":  # ESC — maybe an arrow/escape sequence
            r, _, _ = _select.select([fd], [], [], 0.03)
            if not r:
                return "back"          # lone ESC
            seq = os.read(fd, 2)       # e.g. b"[A"
            if seq[:1] == b"[":
                return {b"A": "up", b"B": "down", b"C": "right",
                        b"D": "left"}.get(seq[1:2], "other")
            return "back"
        if b in (b"\r", b"\n"):
            return "enter"
        if b == b" ":
            return "space"
        if b in (b"\x7f", b"\x08"):
            return "back"              # Backspace / Delete
        if b == b"\x03":
            return "quit"              # Ctrl-C
        try:
            return b.decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            return "other"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _term_width() -> int:
    return max(40, shutil.get_terminal_size((80, 24)).columns)


def _clip(s: str, width: int) -> str:
    """Truncate on visible width, preserving ANSI (rough but safe)."""
    if len(strip_ansi(s)) <= width:
        return s
    out, vis = [], 0
    i = 0
    while i < len(s) and vis < width - 1:
        m = _ANSI.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        out.append(s[i])
        vis += 1
        i += 1
    return "".join(out) + "…" + _R


# --- rendering --------------------------------------------------------------
# Redraw strategy: on every repaint move the cursor to the top line of the
# PREVIOUS render, clear from there to the end of the screen (\x1b[0J), then
# print the current lines. Invariant: after a render the cursor sits on the last
# line (no trailing newline), so `prev-1` cursor-ups return to the first line.
# This avoids the off-by-one drift that a trailing newline + fixed cursor-up
# count causes (the "staircase" reprint).
def _render(title: str, lines: list[str], footer: str) -> None:
    w = _term_width() - 1
    body: list[str] = []
    if title:
        body.append(f"{_BOLD}{_CYAN}{title}{_R}")
    body.extend(lines)
    if footer:
        body.append(f"{_DIM}{footer}{_R}")

    out = []
    prev = _render.height
    if prev:
        out.append(f"\x1b[{prev - 1}A")     # up to first line of previous menu
    out.append("\r\x1b[0J")                 # col 0, clear to end of screen
    out.append("\n\r".join("\x1b[2K" + _clip(ln, w) for ln in body))
    _render.height = len(body)
    sys.stdout.write("".join(out))
    sys.stdout.flush()


_render.height = 0  # type: ignore[attr-defined]


def _reset_render() -> None:
    _render.height = 0  # type: ignore[attr-defined]


def _cursor(active: bool) -> str:
    return f"{_BOLD}{_CYAN}❯ {_R}" if active else "  "


# --- public: single select --------------------------------------------------
def select(title: str, options: list, label: Callable[[object], str] = str,
           start: int = 0, footer: str = "↑/↓ move · Enter select · Esc/⌫ back"):
    """Return (index, item) or (None, None) if the user backs out."""
    if not options:
        return None, None
    if not _is_tty():
        return _fallback_select(title, options, label)

    idx = max(0, min(start, len(options) - 1))
    _reset_render()
    sys.stdout.write("\x1b[?25l")  # hide cursor
    try:
        while True:
            lines = [f"{_cursor(i == idx)}{label(o)}" for i, o in enumerate(options)]
            _render(title, lines, footer)
            k = _read_key()
            if k in ("up", "k"):
                idx = (idx - 1) % len(options)
            elif k in ("down", "j"):
                idx = (idx + 1) % len(options)
            elif k == "enter":
                return idx, options[idx]
            elif k in ("back", "quit", "q"):
                return None, None
    finally:
        sys.stdout.write("\x1b[?25h\r\n")  # show cursor, move below menu
        sys.stdout.flush()
        _reset_render()


# --- public: multi select ---------------------------------------------------
def multiselect(title: str, options: list, label: Callable[[object], str] = str,
                preselected: Iterable[int] = (), on_new: Callable[[], object] | None = None,
                footer: str | None = None):
    """Return a list of chosen items, or None if the user backs out.

    on_new: optional callback invoked when 'n' is pressed; if it returns a new
    item, that item is appended and pre-checked (used to add a model by name).
    """
    if not options and on_new is None:
        return []
    if footer is None:
        footer = ("↑/↓ move · Space toggle · a all/none · Enter confirm · Esc/⌫ back"
                  + (" · n new" if on_new else ""))
    if not _is_tty():
        return _fallback_multiselect(title, options, label, on_new)

    options = list(options)
    checked = set(preselected)
    idx = 0
    _reset_render()
    sys.stdout.write("\x1b[?25l")
    try:
        while True:
            lines = []
            for i, o in enumerate(options):
                box = f"{_GREEN}[x]{_R}" if i in checked else "[ ]"
                lines.append(f"{_cursor(i == idx)}{box} {label(o)}")
            if not options:
                lines.append(f"{_DIM}(none — press n to add){_R}")
            _render(title, lines, footer)
            k = _read_key()
            if k in ("up", "k"):
                idx = (idx - 1) % max(1, len(options))
            elif k in ("down", "j"):
                idx = (idx + 1) % max(1, len(options))
            elif k == "space" and options:
                checked ^= {idx}
            elif k == "a":
                checked = set() if len(checked) == len(options) else set(range(len(options)))
            elif k == "n" and on_new:
                sys.stdout.write("\x1b[?25h\r\n")
                item = on_new()
                sys.stdout.write("\x1b[?25l")
                _reset_render()      # prompt printed below; redraw menu fresh
                if item is not None:
                    options.append(item)
                    checked.add(len(options) - 1)
                    idx = len(options) - 1
            elif k == "enter":
                return [options[i] for i in range(len(options)) if i in checked]
            elif k in ("back", "quit", "q"):
                return None
    finally:
        sys.stdout.write("\x1b[?25h\r\n")
        sys.stdout.flush()
        _reset_render()


# --- non-TTY fallbacks ------------------------------------------------------
def _fallback_select(title, options, label):
    print(title)
    for i, o in enumerate(options, 1):
        print(f"  {i}. {strip_ansi(label(o))}")
    try:
        raw = input("select # (blank=back): ").strip()
    except EOFError:
        return None, None
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return int(raw) - 1, options[int(raw) - 1]
    return None, None


def _fallback_multiselect(title, options, label, on_new):
    print(title)
    for i, o in enumerate(options, 1):
        print(f"  {i}. {strip_ansi(label(o))}")
    try:
        raw = input("pick #s comma-sep (blank=back): ").strip()
    except EOFError:
        return None
    if not raw:
        return None
    out = []
    for tok in raw.replace(" ", ",").split(","):
        if tok.isdigit() and 1 <= int(tok) <= len(options):
            out.append(options[int(tok) - 1])
    return out
