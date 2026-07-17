# bmm — setup & the bits that need *you*

Almost everything is already done and needs no action. This file lists only the
steps that require a human (sudo, API keys) and the hardware caveats.

## TL;DR — start using it now

```bash
bmm            # interactive TUI  (alias added to ~/.bashrc)
```
If `bmm` isn't found yet, either open a new shell or run:
```bash
source ~/.bashrc      # or: ~/benchmarks/scripts/bmm-launch
```

The **four built-in benchmarks work immediately** against llama-swap on
`:9292` — tool-calling, code-gen, reasoning, and a security probe. No docker,
no keys, no extra installs.

## Hardware reality (read this before running big suites)

Generation on this box is ~5 tok/s single-stream (see `~/models/results`).
Full external suites assume fast API models and will take **hours to days** at
this speed. So:

- Use the **built-ins** for quick, repeatable capability signal (they're
  deliberately small — 10–15 items each).
- For external harnesses, always pass a small `--limit` and expect long runs.
- llama-swap hot-swaps models per request; the first call after a model change
  pays a load penalty (tens of seconds) while the GGUF is mapped.

## Step 1 — (optional) Docker + Node, for agentic benchmarks

These benchmarks execute code/agents in sandboxes and **cannot run without
docker**: Terminal-Bench, SWE-bench, Cybench, WebArena, Aider-polyglot.
Their code is already installed; they just need the runtime. This is the only
sudo step:

```bash
bash ~/benchmarks/scripts/RUN-ME-sudo.sh
```
Log out/in afterwards (docker group), then `bmm list` — those benchmarks flip
from `NEEDS:docker` to `READY`.

## Step 2 — (optional) API keys, for web-search benchmarks

GAIA and any search-agent benchmark need a web-search key. Copy the template
and fill in one provider:

```bash
cp ~/benchmarks/.env.example ~/benchmarks/.env
$EDITOR ~/benchmarks/.env      # set TAVILY_API_KEY= (or SERPER/BING)
```
`bmm` auto-loads `~/benchmarks/.env`. Nothing else reads your keys.

## Step 3 — nothing

That's it. Run `bmm`, pick a model, pick benchmarks, compare results.

## Notes on external harness wrappers

Each external benchmark is wired through `harnesses/<key>/run.sh`. Those
wrappers target a specific tool version and are marked where the CLI is
version-sensitive — if a harness updates its flags, edit that one wrapper; the
TUI and everything else is unaffected. A wrapper always ends by printing
`BMM_RESULT={...}` which is the only thing bmm reads back.
