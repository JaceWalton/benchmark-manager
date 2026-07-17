#!/usr/bin/env bash
# Tier 1 harnesses (no docker): BFCL, EvalPlus, LiveCodeBench, Aider-Polyglot.
# Each install is best-effort and non-fatal; a summary prints at the end.
DIR="$(cd "$(dirname "$0")" && pwd)"; source "$DIR/common.sh"

SUMMARY=""
note(){ SUMMARY+="  $1\n"; }

# ---------------------------------------------------------------- BFCL -------
install_bfcl(){
  local d="$HARN/bfcl"
  clone_or_pull https://github.com/ShishirPatil/gorilla "$d" || { note "bfcl: clone FAIL"; return; }
  mk_env "$d"
  # BFCL lives in a subdir; install it editable. openai backend => no torch.
  if uvpip "$d" -e "$d/berkeley-function-call-leaderboard"; then :; else
    note "bfcl: pip install FAIL (see log) — skipping wrapper"; return
  fi
  # qwen_agent is imported transitively by BFCL's model registry and needs
  # soundfile; without it every BFCL invocation crashes on import.
  uvpip "$d" soundfile || note "bfcl: soundfile install FAIL (BFCL import will crash)"

  # Shim: register the local $BMM_MODEL alias into BFCL's model map (BFCL rejects
  # models not in its built-in list) then hand off to its CLI.
  cat > "$d/bmm_register.py" <<'PYREG'
"""Run BFCL's CLI with the local llama-swap alias registered (prompt mode)."""
import os

from bfcl_eval.constants import model_config as mc
from bfcl_eval.model_handler.api_inference.openai_completion import (
    OpenAICompletionsHandler,
)

_name = os.environ.get("BMM_MODEL", "").strip()
if _name and _name not in mc.MODEL_CONFIG_MAPPING:
    mc.MODEL_CONFIG_MAPPING[_name] = mc.ModelConfig(
        model_name=_name, display_name=_name,
        url=os.environ.get("BMM_ENDPOINT", "http://127.0.0.1:9292/v1"),
        org="local", license="local",
        model_handler=OpenAICompletionsHandler,
        input_price=None, output_price=None,
        is_fc_model=False, underscore_to_dot=False,
    )

from bfcl_eval.__main__ import cli  # noqa: E402

if __name__ == "__main__":
    cli()
PYREG

  write_wrapper bfcl <<'WRAP'
#!/usr/bin/env bash
# BFCL against an OpenAI-compatible endpoint (llama-swap). See bmm_register.py
# (local-model registration) and the DOTENV write below (BFCL's load_dotenv
# override would otherwise clobber our creds). NOTE: BFCL has no --limit; it runs
# the whole --test-category (simple_python = ~400 prompts). Ctrl-C to stop early.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; PY="$HERE/.venv/bin"
export OPENAI_BASE_URL="$BMM_ENDPOINT" OPENAI_API_KEY="$BMM_API_KEY"
export PYTHONUNBUFFERED=1
CAT="${BFCL_CATEGORY:-simple_python}"
bfclpy(){ "$PY/python" "$HERE/bmm_register.py" "$@"; }

"$PY/python" - "$BMM_ENDPOINT" "$BMM_API_KEY" <<'PY' || true
import sys, pathlib
from bfcl_eval.constants.eval_config import DOTENV_PATH
base, key = sys.argv[1], sys.argv[2]
p = pathlib.Path(DOTENV_PATH); env = {}
if p.exists():
    for ln in p.read_text().splitlines():
        if "=" in ln and not ln.strip().startswith("#"):
            k, _, v = ln.partition("="); env[k.strip()] = v.strip()
env["OPENAI_API_KEY"] = key; env["OPENAI_BASE_URL"] = base
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")
print(f"[bfcl] wrote OPENAI_BASE_URL/API_KEY to {p}")
PY

echo "[bfcl] generate on $BMM_MODEL category=$CAT (full category — Ctrl-C to stop early)"
bfclpy generate --model "$BMM_MODEL" --test-category "$CAT" \
  --num-threads 1 --backend openai 2>&1 || true
echo "[bfcl] evaluate"
OUT="$(bfclpy evaluate --model "$BMM_MODEL" --test-category "$CAT" 2>&1)"
printf '%s\n' "$OUT" | tail -40
ACC="$(printf '%s' "$OUT" | grep -oiE 'accuracy[^0-9]*[0-9.]+' | grep -oE '[0-9.]+' | head -1)"
if [ -n "$ACC" ]; then
  awk -v a="$ACC" 'BEGIN{s=(a>1)?a/100:a; printf "BMM_RESULT={\"score\":%.4f,\"score_label\":\"bfcl_acc\",\"status\":\"completed\",\"metrics\":{\"category\":\"'"$CAT"'\"}}\n", s}'
else
  echo 'BMM_RESULT={"status":"partial","score":null,"metrics":{"note":"could not parse accuracy — see log"}}'
fi
WRAP
  note "bfcl: installed"
}

# ------------------------------------------------------------- EvalPlus -----
install_evalplus(){
  local d="$HARN/evalplus"
  mk_env "$d"
  if uvpip "$d" evalplus; then :; else note "evalplus: pip FAIL"; return; fi
  write_wrapper evalplus <<'WRAP'
#!/usr/bin/env bash
# EvalPlus HumanEval+ via the openai backend. NOTE: full HumanEval+ is 164
# problems — slow at ~5 tok/s. There is no first-class --limit, so this runs
# the full set; budget accordingly or use the built-in code-mini for quick runs.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; PY="$HERE/.venv/bin"
export OPENAI_BASE_URL="$BMM_ENDPOINT" OPENAI_API_KEY="$BMM_API_KEY"
export PYTHONUNBUFFERED=1
DS="${EVALPLUS_DATASET:-humaneval}"
echo "[evalplus] codegen $DS on $BMM_MODEL (full set = 164 problems, slow at ~5 tok/s)"
# Stream directly (no `| tail`, which buffers until EOF and hid all progress).
"$PY/python" -m evalplus.codegen --model "$BMM_MODEL" --backend openai \
  --base-url "$BMM_ENDPOINT" --dataset "$DS" --greedy 2>&1 || true
OUT="$("$PY/python" -m evalplus.evaluate --dataset "$DS" \
  --samples "$(ls -t "$HERE"/*"$DS"*.jsonl 2>/dev/null | head -1)" 2>&1)"
printf '%s\n' "$OUT" | tail -40
BASE="$(printf '%s' "$OUT" | grep -oiE 'pass@1[^0-9]*[0-9.]+' | grep -oE '[0-9.]+' | head -1)"
if [ -n "$BASE" ]; then
  awk -v a="$BASE" 'BEGIN{s=(a>1)?a/100:a; printf "BMM_RESULT={\"score\":%.4f,\"score_label\":\"pass@1(base)\",\"status\":\"completed\"}\n", s}'
else
  echo 'BMM_RESULT={"status":"partial","score":null,"metrics":{"note":"parse failed — see log"}}'
fi
WRAP
  note "evalplus: installed"
}

# ---------------------------------------------------------- LiveCodeBench ---
install_livecodebench(){
  local d="$HARN/livecodebench"
  clone_or_pull https://github.com/LiveCodeBench/LiveCodeBench "$d" || { note "lcb: clone FAIL"; return; }
  mk_env "$d"
  # LiveCodeBench pulls torch/vllm by default; that's heavy and unused for API
  # generation. Install core deps only; may still be sizeable.
  if uvpip "$d" -e "$d"; then
    write_wrapper livecodebench <<'WRAP'
#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; PY="$HERE/.venv/bin"
export OPENAI_BASE_URL="$BMM_ENDPOINT" OPENAI_API_KEY="$BMM_API_KEY"
echo "[lcb] LiveCodeBench generation is heavy; consult repo README for the"
echo "     custom-endpoint runner. Emitting a stub result for now."
echo 'BMM_RESULT={"status":"partial","score":null,"metrics":{"note":"LiveCodeBench installed; wire its openai runner in run.sh"}}'
WRAP
    note "livecodebench: installed (wrapper is a stub — see run.sh)"
  else
    note "livecodebench: pip FAIL (likely torch) — skipped"
  fi
}

# ---------------------------------------------------------- Aider Polyglot --
install_aider(){
  local d="$HARN/aider-polyglot"
  mk_env "$d"
  if uvpip "$d" aider-chat; then :; else note "aider: pip FAIL"; return; fi
  clone_or_pull https://github.com/Aider-AI/aider "$d/src" || true
  write_wrapper aider-polyglot <<'WRAP'
#!/usr/bin/env bash
# Aider's polyglot benchmark runs each Exercism exercise inside docker; it is
# therefore gated on docker at runtime even though the CLI installs cleanly.
set -uo pipefail
if ! command -v docker >/dev/null 2>&1; then
  echo "[aider-polyglot] docker required for the benchmark harness."
  echo 'BMM_RESULT={"status":"error","score":null,"metrics":{"note":"needs docker"}}'
  exit 0
fi
echo "[aider-polyglot] see src/benchmark/README.md — run benchmark.py with"
echo "  --model openai/$BMM_MODEL and OPENAI_API_BASE=$BMM_ENDPOINT"
echo 'BMM_RESULT={"status":"partial","score":null,"metrics":{"note":"docker present; invoke src/benchmark/benchmark.py"}}'
WRAP
  note "aider-polyglot: installed (needs docker to run)"
}

c_info "== Tier 1 installs (no docker) =="
# Light, fast, no-torch harnesses: always install.
install_evalplus
install_aider
# Heavy (pull torch/vllm; resolution alone takes many minutes). Opt in with
# BMM_INSTALL_HEAVY=1  — or run  install/optional-heavy.sh.
if [ "${BMM_INSTALL_HEAVY:-0}" = 1 ]; then
  install_bfcl
  install_livecodebench
else
  note "bfcl: SKIPPED (heavy) — run: BMM_INSTALL_HEAVY=1 bash install/install-tier1.sh"
  note "livecodebench: SKIPPED (heavy) — same as above"
fi
echo
c_info "Tier 1 summary:"; printf "$SUMMARY"
