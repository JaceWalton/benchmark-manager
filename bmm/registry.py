"""Benchmark registry: built-ins + external harness adapters.

A built-in runs a Python callable against the endpoint. A harness benchmark
shells out to  harnesses/<key>/run.sh  which the install scripts create; that
wrapper runs the real tool and prints a final line:

    BMM_RESULT={"score":0.42,"n_total":50,"n_pass":21,"metrics":{...}}

so the TUI never has to parse each harness's bespoke output format.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Callable

from . import config
from .builtins import (
    humaneval_mini,
    reasoning_mini,
    security_probe,
    toolcalling,
)


@dataclass
class Benchmark:
    key: str
    name: str
    category: str
    tier: int
    source: str                       # "builtin" | "harness"
    description: str = ""
    run_fn: Callable | None = None    # builtins only
    requires: list[str] = field(default_factory=list)  # docker | node | key:X
    repo: str = ""                    # for harness install docs
    notes: str = ""
    # How the benchmark is driven:
    #   "endpoint" — bmm/the harness sends prompts to the model and grades it.
    #                Just pick a model; nothing else to do.
    #   "agent"    — the benchmark measures an *agent* doing multi-step tasks, so
    #                something must drive the model as an agent (your openclaw
    #                agent, or a bundled default). See `driver` for specifics.
    exec: str = "endpoint"
    driver: str = ""                  # one-line "how it's driven" for the UI/README

    # Approximate sizing, to help budget a run at ~5 tok/s and to check the model
    # has enough context. All rough, per-task figures (the harnesses vary):
    ctx_needed: int = 4096            # context window a single task needs (tokens)
    in_tokens: int = 500              # typical prompt tokens per task
    out_tokens: int = 1024            # generation budget per task (tokens)
    max_tasks: int = 0                # full task-set size (0=unknown); the "max" limit

    # -- status --------------------------------------------------------------
    @property
    def harness_dir(self):
        return config.HARNESS_DIR / self.key

    @property
    def wrapper(self):
        return self.harness_dir / "run.sh"

    def installed(self) -> bool:
        if self.source == "builtin":
            return True
        return self.wrapper.exists()

    def missing_requirements(self) -> list[str]:
        miss = []
        for req in self.requires:
            if req == "docker" and not shutil.which("docker"):
                miss.append("docker")
            elif req == "node" and not shutil.which("node"):
                miss.append("node")
            elif req.startswith("key:") and not os.environ.get(req[4:]):
                miss.append(req)
        return miss

    def status(self) -> str:
        """READY | NEEDS-INSTALL | NEEDS:docker,key:X"""
        if not self.installed():
            return "NEEDS-INSTALL"
        miss = self.missing_requirements()
        if miss:
            return "NEEDS:" + ",".join(miss)
        return "READY"

    def resolve_limit(self, limit) -> int:
        """Turn a requested limit into a concrete task count.

        `"max"` (or None) => the full task set (`max_tasks`), falling back to the
        global default when a harness's size is unknown.
        """
        if limit is None or str(limit).lower() == "max":
            return self.max_tasks or config.DEFAULT_LIMIT
        return int(limit)


# --- the catalogue ----------------------------------------------------------
BENCHMARKS: list[Benchmark] = [
    # ---- built-ins (always READY, run vs the endpoint) ----
    Benchmark(toolcalling.KEY, toolcalling.NAME, toolcalling.CATEGORY, 1,
              "builtin", toolcalling.DESCRIPTION, toolcalling.run,
              ctx_needed=4096, in_tokens=800, out_tokens=512,
              max_tasks=len(toolcalling.CASES)),
    Benchmark(humaneval_mini.KEY, humaneval_mini.NAME, humaneval_mini.CATEGORY, 1,
              "builtin", humaneval_mini.DESCRIPTION, humaneval_mini.run,
              ctx_needed=4096, in_tokens=300, out_tokens=1024,
              max_tasks=len(humaneval_mini.PROBLEMS)),
    Benchmark(reasoning_mini.KEY, reasoning_mini.NAME, reasoning_mini.CATEGORY, 1,
              "builtin", reasoning_mini.DESCRIPTION, reasoning_mini.run,
              ctx_needed=4096, in_tokens=200, out_tokens=1024,
              max_tasks=len(reasoning_mini.PROBLEMS)),
    Benchmark(security_probe.KEY, security_probe.NAME, security_probe.CATEGORY, 3,
              "builtin", security_probe.DESCRIPTION, security_probe.run,
              ctx_needed=2048, in_tokens=200, out_tokens=256,
              max_tasks=len(security_probe.KNOWLEDGE)),

    # ---- Tier 1 external harnesses (no docker) ----
    Benchmark("bfcl", "Berkeley Function-Calling Leaderboard", "tool-calling", 1,
              "harness", "Full BFCL: single/parallel/multi-turn tool calls, AST-checked.",
              requires=[], repo="ShishirPatil/gorilla",
              exec="endpoint", driver="bmm sends prompts to the model and AST-checks the calls.",
              ctx_needed=8192, in_tokens=1200, out_tokens=1024, max_tasks=400),
    Benchmark("evalplus", "EvalPlus (HumanEval+/MBPP+)", "code", 1,
              "harness", "Hardened HumanEval+ / MBPP+ execution-based code eval.",
              requires=[], repo="evalplus/evalplus",
              exec="endpoint", driver="bmm sends prompts to the model and executes the code.",
              ctx_needed=4096, in_tokens=400, out_tokens=1024, max_tasks=164),
    Benchmark("livecodebench", "LiveCodeBench", "code", 1,
              "harness", "Contamination-resistant fresh competitive-programming problems.",
              requires=[], repo="LiveCodeBench/LiveCodeBench",
              exec="endpoint", driver="bmm sends prompts to the model and executes the code.",
              ctx_needed=8192, in_tokens=1500, out_tokens=2048, max_tasks=400),
    Benchmark("aider-polyglot", "Aider Polyglot", "code", 1,
              "harness", "225 Exercism exercises across 6 languages + edit-format following.",
              requires=["docker"], repo="Aider-AI/aider",
              exec="agent", driver="Aider is the agent (bundled) and edits code in docker — not openclaw.",
              ctx_needed=16384, in_tokens=2000, out_tokens=2048, max_tasks=225),

    # ---- Tier 2 external harnesses (agentic; some need docker) ----
    Benchmark("tau2-bench", "tau2-bench (Sierra)", "tool-calling", 2,
              "harness", "Multi-turn tool-agent-user simulation; retail/airline/telecom.",
              requires=[], repo="sierra-research/tau2-bench",
              exec="endpoint",
              driver="Self-driven: the harness plays agent + user + tools using your model. Just pick a model.",
              ctx_needed=16384, in_tokens=3000, out_tokens=1024, max_tasks=165),
    Benchmark("terminal-bench", "Terminal-Bench 2.0 (Harbor)", "terminal", 2,
              "harness", "End-to-end shell tasks graded in sandboxed containers.",
              requires=["docker"], repo="harbor-framework/harbor",
              exec="agent",
              driver="Needs an agent to run shell commands. Default: bundled Terminus. Or plug in openclaw as --agent.",
              ctx_needed=32768, in_tokens=4000, out_tokens=2048, max_tasks=100),
    Benchmark("swe-bench", "SWE-bench Verified", "code", 2,
              "harness", "Real GitHub issues; patch must pass the repo's test suite.",
              requires=["docker"], repo="princeton-nlp/SWE-bench",
              exec="agent",
              driver="Only grades. openclaw must solve the issues and produce predictions.jsonl (set SWEBENCH_PREDS).",
              ctx_needed=32768, in_tokens=8000, out_tokens=4096, max_tasks=500),

    # ---- Tier 3 external harnesses (heavy infra / keys) ----
    Benchmark("cyberseceval", "CyberSecEval 4 (PurpleLlama)", "cybersecurity", 3,
              "harness", "Insecure-code gen, cyberattack uplift, prompt-injection resistance.",
              requires=[], repo="meta-llama/PurpleLlama",
              exec="endpoint", driver="bmm sends prompts to the model and judges the responses.",
              ctx_needed=8192, in_tokens=1500, out_tokens=1024, max_tasks=1000),
    Benchmark("cybench", "Cybench (CTF)", "cybersecurity", 3,
              "harness", "40 professional CTF tasks solved in a sandboxed shell.",
              requires=["docker"], repo="andyzorigin/cybench",
              exec="agent",
              driver="Needs an agent to work the shell/CTF. Use its default agent or wire in openclaw.",
              ctx_needed=32768, in_tokens=4000, out_tokens=2048, max_tasks=40),
    Benchmark("gaia", "GAIA (general assistant)", "web-search", 3,
              "harness", "Search + tools + files + reasoning; needs a web-search key.",
              requires=["key:TAVILY_API_KEY"], repo="huggingface/gaia",
              exec="agent",
              driver="Needs a search/tool agent (openclaw with web tools) to answer each question.",
              ctx_needed=16384, in_tokens=2000, out_tokens=1024, max_tasks=165),
    Benchmark("webarena", "WebArena", "browser", 3,
              "harness", "Self-hosted realistic sites; deterministic task grading.",
              requires=["docker", "node"], repo="web-arena-x/webarena",
              exec="agent",
              driver="Needs a browser-driving agent (openclaw with browser tools) against the hosted sites.",
              ctx_needed=16384, in_tokens=3000, out_tokens=1024, max_tasks=812),
]

BY_KEY = {b.key: b for b in BENCHMARKS}


def get(key: str) -> Benchmark | None:
    return BY_KEY.get(key)


def by_tier() -> dict[int, list[Benchmark]]:
    out: dict[int, list[Benchmark]] = {}
    for b in BENCHMARKS:
        out.setdefault(b.tier, []).append(b)
    return dict(sorted(out.items()))
