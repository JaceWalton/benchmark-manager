#!/usr/bin/env bash
# Tier 2 harnesses (agentic): tau2-bench (no docker), Terminal-Bench 2.0 and
# SWE-bench Verified (both need docker to execute).
DIR="$(cd "$(dirname "$0")" && pwd)"; source "$DIR/common.sh"
SUMMARY=""; note(){ SUMMARY+="  $1\n"; }

# ---------------------------------------------------------- tau2-bench ------
install_tau2(){
  local d="$HARN/tau2-bench"
  clone_or_pull https://github.com/sierra-research/tau2-bench "$d" || { note "tau2: clone FAIL"; return; }
  mk_env "$d"
  if uvpip "$d" -e "$d"; then :; else note "tau2: pip FAIL"; return; fi
  write_wrapper tau2-bench <<'WRAP'
#!/usr/bin/env bash
# tau2-bench multi-turn tool-agent-user simulation via litellm -> local endpoint.
# Both agent and simulated user are served by the same local model.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; PY="$HERE/.venv/bin"
export OPENAI_API_BASE="$BMM_ENDPOINT" OPENAI_BASE_URL="$BMM_ENDPOINT" OPENAI_API_KEY="$BMM_API_KEY"
DOMAIN="${TAU2_DOMAIN:-retail}"; M="openai/$BMM_MODEL"
echo "[tau2] domain=$DOMAIN model=$M num-tasks=$BMM_LIMIT"
"$PY/tau2" run --domain "$DOMAIN" --agent-llm "$M" --user-llm "$M" \
  --num-tasks "$BMM_LIMIT" --max-concurrency 2 2>&1 | tee "$HERE/last.log" || true   # stream live; full log kept for parsing
# tau2 prints a reward/pass rate; parse the last percentage-like number.
R="$(grep -oiE 'reward[^0-9]*[0-9.]+|pass[^0-9]*rate[^0-9]*[0-9.]+' "$HERE/last.log" | grep -oE '[0-9.]+' | tail -1)"
if [ -n "$R" ]; then
  awk -v a="$R" 'BEGIN{s=(a>1)?a/100:a; printf "BMM_RESULT={\"score\":%.4f,\"score_label\":\"tau2_reward\",\"status\":\"completed\",\"metrics\":{\"domain\":\"'"$DOMAIN"'\"}}\n", s}'
else
  echo 'BMM_RESULT={"status":"partial","score":null,"metrics":{"note":"parse failed — inspect last.log; tau2 CLI is version-sensitive"}}'
fi
WRAP
  note "tau2-bench: installed (no docker; runs retail/airline/telecom)"
}

# ------------------------------------------------------- Terminal-Bench -----
install_terminal_bench(){
  local d="$HARN/terminal-bench"
  mk_env "$d"
  write_wrapper terminal-bench <<'WRAP'
#!/usr/bin/env bash
# Terminal-Bench runs each task in a Docker sandbox -> docker is mandatory.
# The tb tool is lazy-installed into .venv on first run (fast, no torch).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; PY="$HERE/.venv/bin"
if ! command -v docker >/dev/null 2>&1; then
  echo "[terminal-bench] docker not installed. Run scripts/RUN-ME-sudo.sh first."
  echo 'BMM_RESULT={"status":"error","score":null,"metrics":{"note":"needs docker"}}'
  exit 0
fi
[ -x "$PY/tb" ] || { echo "[terminal-bench] first-run install…"; uv pip install --python "$PY/python" terminal-bench 2>&1 | tail -2; }
export OPENAI_API_BASE="$BMM_ENDPOINT" OPENAI_API_KEY="$BMM_API_KEY"
echo "[terminal-bench] running terminus agent on openai/$BMM_MODEL (limit=$BMM_LIMIT)"
"$PY/tb" run --agent terminus --model "openai/$BMM_MODEL" \
  --dataset-name terminal-bench-core --n-tasks "$BMM_LIMIT" --n-concurrent 4 \
  2>&1 | tee "$HERE/last.log" || true   # stream live; full log kept for parsing
ACC="$(grep -oiE 'accuracy[^0-9]*[0-9.]+|resolved[^0-9]*[0-9.]+%' "$HERE/last.log" | grep -oE '[0-9.]+' | tail -1)"
if [ -n "$ACC" ]; then
  awk -v a="$ACC" 'BEGIN{s=(a>1)?a/100:a; printf "BMM_RESULT={\"score\":%.4f,\"score_label\":\"tb_resolved\",\"status\":\"completed\"}\n", s}'
else
  echo 'BMM_RESULT={"status":"partial","score":null,"metrics":{"note":"parse failed — see last.log"}}'
fi
WRAP
  note "terminal-bench: installed (NEEDS docker to run)"
}

# --------------------------------------------------------- SWE-bench --------
install_swebench(){
  local d="$HARN/swe-bench"
  mk_env "$d"
  write_wrapper swe-bench <<'WRAP'
#!/usr/bin/env bash
# SWE-bench evaluation builds per-repo Docker images -> docker mandatory.
# Note: bmm does not generate patches; point --predictions_path at a prediction
# file produced by your agent (e.g. SWE-agent / OpenHands) against the endpoint.
# swebench is lazy-installed into .venv on first run.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; PY="$HERE/.venv/bin"
if ! command -v docker >/dev/null 2>&1; then
  echo "[swe-bench] docker not installed. Run scripts/RUN-ME-sudo.sh first."
  echo 'BMM_RESULT={"status":"error","score":null,"metrics":{"note":"needs docker"}}'
  exit 0
fi
[ -x "$PY/python" ] && "$PY/python" -c "import swebench" 2>/dev/null || { echo "[swe-bench] first-run install…"; uv pip install --python "$PY/python" swebench 2>&1 | tail -2; }
echo "[swe-bench] set SWEBENCH_PREDS=/path/to/preds.jsonl to evaluate."
if [ -z "${SWEBENCH_PREDS:-}" ]; then
  echo 'BMM_RESULT={"status":"error","score":null,"metrics":{"note":"set SWEBENCH_PREDS to a predictions file"}}'
  exit 0
fi
"$HERE/.venv/bin/python" -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Verified \
  --predictions_path "$SWEBENCH_PREDS" --max_workers 4 --run_id bmm \
  2>&1 | tee "$HERE/last.log" || true   # stream live; full log kept for parsing
R="$(grep -oiE 'resolved[^0-9]*[0-9.]+' "$HERE/last.log" | grep -oE '[0-9.]+' | tail -1)"
[ -n "$R" ] && awk -v a="$R" 'BEGIN{printf "BMM_RESULT={\"score\":%.4f,\"score_label\":\"resolved\",\"status\":\"completed\"}\n",(a>1)?a/100:a}' \
  || echo 'BMM_RESULT={"status":"partial","score":null}'
WRAP
  note "swe-bench: installed (NEEDS docker + a predictions file to run)"
}

c_info "== Tier 2 installs =="
install_tau2
install_terminal_bench
install_swebench
echo
c_info "Tier 2 summary:"; printf "$SUMMARY"
