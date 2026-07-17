# Benchmark Manager (`bmm`)

An interactive TUI to run your local models (served by **llama-swap** on
`:9292`) through LLM capability benchmarks — tool-calling, code, reasoning,
terminal use, web search/browser, and cybersecurity — then **save, view and
compare** the results.

```bash
bmm            # launch the TUI   (alias in ~/.bashrc → scripts/bmm-launch)
```

<sub>New shell picks up the alias automatically; in this one run `source ~/.bashrc` first.</sub>

---

## What you can do

- Pick a **model** (auto-discovered from llama-swap) and run **one or many
  benchmarks back-to-back**, with live per-item output and a **right-aligned
  timestamp** on every benchmark and task line.
- Or hit **“Run ALL endpoint models · max tasks”** to sweep every model through
  every READY endpoint benchmark at full task-set size in one go.
- Every run is saved to `results/<benchmark>/<model>__<UTC>.json`. Results are
  **never overwritten** — a re-run always writes a new timestamped file, and the
  TUI **asks before re-running** a (model, benchmark) pair you already have.
- A **heartbeat + timeouts** keep long runs honest: a quiet run prints an
  elapsed “still running…” line, a stalled harness is killed, and you can set a
  per-benchmark wall-clock cap.
- Every failed / partial / timed-out run is appended to `logs/errors.jsonl`
  (self-contained, ready to send to GitHub later).
- **View** past runs and drill into per-item detail.
- **Compare** models in a `model × benchmark` matrix.
- See at a glance which benchmarks are `READY` vs need docker / a key / an
  install, plus each one’s **context / input / output token** needs and
  full task-set size.

Navigate the TUI with the **arrow keys** (↑/↓ move, Enter select, Space toggle in
multi-select lists, **Backspace/Esc go back**, `q` quits). No numbers to type.

### Token & run-time columns

`bmm list` and the benchmark picker show sizing columns so you can tell, before
you start, whether a model fits and how long a run will take:

| column | meaning |
|---|---|
| `ctx` | context window a single task needs (tokens) — must fit the model |
| `in`  | typical prompt tokens per task |
| `out` | generation budget per task (this dominates wall-clock) |
| `full`| size of the whole task set (what **“max”** runs) |
| *per-model* | **estimated time for one task** on that model (`out ÷ its tok/s`) |

The last column is **per model**: for every model you've measured (see below)
`bmm list` adds a column headed with the model and its tok/s, showing how long a
single task takes on it. Until you measure any model, one column is shown at the
default `~5 tok/s`. All of these are **approximate** guides.

### Measuring model speed

Time-per-task needs each model's real generation speed. Measure it once:

```bash
bmm speed --model qwen3.6-27b     # or --model all  (each is a swap + short timed gen)
```

or the TUI home entry **“Measure model speed(s)”**. Each measurement times a
short generation, derives tok/s from the endpoint's usage counters, and caches it
in `speeds.json`. After that, the per-task time columns and the run-time
estimates use each model's measured speed instead of the `~5 tok/s` default.

### Tasks-per-benchmark, timeouts & the re-run guard

When you start a run the TUI asks three quick things:

1. **Tasks per benchmark** — `Default` (20), **`Max`** (the whole `full` set), or
   a custom number.
2. **Per-benchmark timeout** — `off`, 30 min, 1 h, or custom. A separate
   *stall-guard* kills a harness that emits **no output for 1 hour** (configurable
   via `BMM_IDLE_TIMEOUT`, `0`=off). It's deliberately generous: a single task
   here can generate for many minutes silently, and an over-eager 30-min guard
   used to false-kill healthy bfcl/tau2/cyberseceval runs.
3. If any selected (model, benchmark) pair **already has results**, it lists them
   and asks whether to re-run or skip — nothing is ever overwritten.

## Two kinds of benchmarks: `endpoint` vs `agent`

This is the key thing to understand, shown in the **`run`** column of `bmm list`
and the catalogue:

| | **`endpoint`** | **`agent`** |
|---|---|---|
| Who drives the model? | bmm / the harness | an **agent** (your **openclaw**, or a bundled one) |
| What you do | pick a model, run | wire an agent to the tasks (see below) |
| Examples | tc-mini, code-mini, reason-mini, sec-probe, bfcl, evalplus, tau2-bench, cyberseceval, livecodebench | terminal-bench, swe-bench, cybench, gaia, webarena, aider-polyglot |

- **`endpoint`** benchmarks send prompts straight to the model and grade the
  replies. bmm runs them for you — **just pick a model and go.** (tau2-bench is
  agentic internally, but it plays *both* the agent and the simulated user using
  your model, so it's still self-driven — no external agent needed.)

- **`agent`** benchmarks score an *agent* performing multi-step tasks (running
  shell commands, editing a repo, browsing, searching). Pointing at a raw model
  isn't enough — something has to **be the agent**. Two sub-cases:
  - **Bundled agent** — the harness ships a default agent that drives your model.
    bmm runs these as-is: **terminal-bench** (Terminus), **aider-polyglot**
    (Aider), **cybench** (its default). Just pick a model.
  - **Bring your own** — the harness only provides tasks + a grader; **openclaw
    must do the work** and hand back the result: **swe-bench** (predictions file),
    **gaia** / **webarena** (openclaw must have search / browser tools).

### How to run the `agent` benchmarks with openclaw

Docker + Node are installed, so these are unblocked. Per harness:

**terminal-bench / cybench (bundled agent, easiest).** Nothing extra — bmm's
wrapper runs the bundled agent pointed at your model. To swap in openclaw
instead of the default, edit `harnesses/terminal-bench/run.sh` and change
`--agent terminus` to your openclaw agent adapter (Harbor/Terminal-Bench expose
an `--agent` plug-in interface; you register openclaw as a custom agent class).

**swe-bench (bring your own).** SWE-bench only *grades*. You point **openclaw**
at each SWE-bench_Verified task (a repo + an issue), let it produce a patch, and
collect the patches into a `predictions.jsonl` (one line per instance:
`{"instance_id": ..., "model_patch": "<diff>", "model_name_or_path": "openclaw"}`).
Then:
```bash
SWEBENCH_PREDS=/path/to/predictions.jsonl bmm run --model <m> --bench swe-bench --limit 20
```
bmm's wrapper builds the per-repo Docker images and runs the real test suites.

**gaia / webarena (bring your own).** openclaw must have web-search (gaia) or
browser (webarena) tools. Run openclaw over the task set, capture its answers /
actions in the format the harness grades, and point the wrapper at that output
(see `harnesses/<key>/run.sh`, which documents the seam). gaia also needs a
search API key in `.env` (`TAVILY_API_KEY`).

> **In short:** to *tell openclaw to run a benchmark*, you either (a) register it
> as the harness's `--agent` (terminal-bench/cybench), or (b) run openclaw over
> the harness's task list yourself and feed its output back to the grader
> (swe-bench/gaia/webarena). There is no single "openclaw, run swe-bench" button —
> the benchmark supplies tasks + grading; openclaw supplies the doing.

If you tell me openclaw's exact CLI, I can wire it into the relevant wrappers so
it's one command.

## Models

The model list is discovered from three places and merged: the endpoint's
`/v1/models` (● served), the **llama-swap config** (○ configured — e.g. the 40B
that isn't loaded yet), and models you add yourself. In the run screen, arrow to
a model and Space to select it; press **`n` to add a new model by name** (saved
to `models.json` for next time). GGUF files sitting in `~/models` that aren't
wired into llama-swap are shown as a hint. `bmm models` lists everything.

## The four built-ins (work immediately, no docker/keys)

| key | category | what it measures |
|-----|----------|------------------|
| `tc-mini` | tool-calling | function selection + argument accuracy (mini-BFCL) |
| `code-mini` | code | function synthesis, graded by executing hidden tests (pass@1) |
| `reason-mini` | reasoning | GSM8K-style multi-step arithmetic |
| `sec-probe` | cybersecurity | defensive-security MCQ accuracy **+** malicious-request refusal rate |

These are deliberately small (10–15 items) so they finish in minutes at local
speeds. `sec-probe`'s `refusal_rate` is the dimension where your abliterated
models will stand out (expect ~0 refusals).

## The external harnesses (the "real" leaderboards)

Installed and wired via `harnesses/<key>/run.sh`. Status shown by `bmm list`:

| key | tier | category | needs |
|-----|------|----------|-------|
| `bfcl` | 1 | tool-calling | — (READY; no `--limit`, runs the whole category) |
| `evalplus` | 1 | code | — (READY, but 164 problems = slow) |
| `aider-polyglot` | 1 | code | docker |
| `livecodebench` | 1 | code | dedicated install (see below); heavy/impractical |
| `tau2-bench` | 2 | tool-calling | — (READY) |
| `terminal-bench` | 2 | terminal | docker |
| `swe-bench` | 2 | code | docker + a predictions file |
| `cyberseceval` | 3 | cybersecurity | — (READY) |
| `cybench` | 3 | cybersecurity | docker |
| `gaia` | 3 | web-search | search API key |
| `webarena` | 3 | browser | docker + node |

`cyberseceval` defaults to the MITRE benchmark (`CSE_BENCHMARK=mitre`), which
runs an expansion + judge LLM per prompt — slow locally, so always start with a
tiny `--limit`. Set `CSE_BENCHMARK=instruct` (or `autocomplete`) for a
single-pass, faster variant.

**`bfcl` note:** BFCL has no first-class `--limit` — it runs the whole
`--test-category` (default `simple_python` = ~400 prompts). At ~5 tok/s that is
hours, so set `BFCL_CATEGORY` to a smaller category or Ctrl-C once you have
enough signal. bmm streams its progress bar live and the heartbeat proves it
isn't stalled. (bmm auto-registers your local alias and points BFCL at
llama-swap; it needs `soundfile`, installed by the tier-1 installer.)

See [SETUP.md](SETUP.md) for the (short) list of things that need you.

## Installing LiveCodeBench

LiveCodeBench is the one benchmark that isn't installed by default, because it's
heavy and awkward:

- its `pyproject` **pins `torch` + `vllm`** (multi-GB, and unused for API
  generation), so we don't `pip install -e .`;
- one dep (`pyext`) **won't build on Python 3.12**, so it needs a 3.10 venv;
- its OpenAI runner points at `api.openai.com` and it validates `--model`
  against a built-in list — both handled by a small shim;
- **at ~5 tok/s a full code-generation pass is on the order of days** (hundreds
  of problems × ~2k output tokens, and there's no clean `--limit`).

A dedicated script handles the first three:

```bash
bash scripts/install-livecodebench.sh    # Python-3.10 venv, API-only deps, shim + wrapper
bmm run --model <m> --bench livecodebench # then run it (expect a very long run)
```

Bottom line: it's wired up, but for everyday code signal on this box prefer
`evalplus` or the `code-mini` built-in. (The old
`BMM_INSTALL_HEAVY=1 bash install/install-tier1.sh` path still exists but pulls
the full torch/vllm stack.)

## Headless CLI (for scripting / cron)

```bash
bmm models                                   # list served models
bmm list                                     # benchmarks + status (token + time/task columns)
bmm speed --model all                        # measure per-model tok/s (fills time/task columns)
bmm run --model qwen3.6-27b --bench builtins --limit 5
bmm run --model all --bench tc-mini,reason-mini --limit 10
bmm run --model all --bench ready --limit max            # full task sets, every model
bmm run --model qwen3.6-27b --bench bfcl --timeout 3600  # 1h per-benchmark cap
bmm run --model all --bench builtins --skip-existing     # skip pairs already done
bmm results                                  # recent runs table
bmm compare                                  # model × benchmark matrix
bmm selftest                                 # 1-call end-to-end check
```
`--bench` accepts: keys (`tc-mini`), indices (`1,3`), or `builtins` / `tier1` /
`ready` / `all`. `--model all` runs every served model (each swap costs a load).
`--limit` takes a number or **`max`** (each benchmark's full task set).
`--timeout N` caps each benchmark at N seconds (0 = off). A separate stall-guard
kills a harness silent for 1 h (`BMM_IDLE_TIMEOUT`, 0=off). `--skip-existing`
skips (model, benchmark) pairs that already have a completed result. Failures
land in `logs/errors.jsonl`.

## Install / re-install harnesses

```bash
bash install/install-all.sh                  # all tiers (best-effort, idempotent)
bash install/install-tier1.sh                # just tier 1 (installs/fixes BFCL)
bash scripts/install-livecodebench.sh        # LiveCodeBench (API-only, no torch)
BMM_INSTALL_HEAVY=1 bash install/install-tier1.sh   # + full LiveCodeBench (torch/vllm)
```

## Hardware reality

Generation is ~5 tok/s single-stream on this box, so full external suites take
hours–days. Use small `--limit`s and lean on the built-ins for fast, repeatable
signal. llama-swap loads the GGUF on first request after a model change (tens of
seconds). Throughput/memory tuning lives separately in `~/models/bench-model.sh`.

## Layout

```
~/benchmarks/
├── bmm/            the TUI + core (rich + stdlib only; no heavy deps)
│   ├── app.py        TUI + headless CLI
│   ├── endpoint.py   OpenAI-compatible client (urllib)
│   ├── registry.py   benchmark catalogue + status
│   ├── runner.py     run builtin/harness, save result
│   ├── results.py    storage / index / comparison
│   └── builtins/     the 4 zero-dependency benchmarks
├── harnesses/<key>/  cloned repo + .venv + run.sh wrapper
├── results/<key>/    one JSON per run  + index.jsonl
├── speeds.json       measured per-model tok/s (fills the time-per-task columns)
├── logs/             per-run .log files  + errors.jsonl (failures, for GitHub)
├── install/          install-tier{1,2,3}.sh, install-all.sh
├── scripts/          bmm-launch, RUN-ME-sudo.sh, install-livecodebench.sh
├── SETUP.md          what needs a human
└── .env.example      optional keys (search / HF / GitHub)
```

## Add your own benchmark

- **Built-in:** drop a module in `bmm/builtins/` exposing `KEY, NAME, CATEGORY,
  TIER, DESCRIPTION` and `run(client, model, opts, emit) -> {score, ...}`, then
  register it in `bmm/registry.py`.
- **External:** add a `Benchmark(source="harness", …)` entry and an install
  function that writes `harnesses/<key>/run.sh`. The wrapper just needs to end
  by printing `BMM_RESULT={json}`.
