#!/usr/bin/env bash
# Tier 3 harnesses (heavy infra / keys): CyberSecEval (no docker), Cybench
# (docker), GAIA (search key), WebArena (docker + node).
DIR="$(cd "$(dirname "$0")" && pwd)"; source "$DIR/common.sh"
SUMMARY=""; note(){ SUMMARY+="  $1\n"; }

# --------------------------------------------------------- CyberSecEval -----
install_cyberseceval(){
  local d="$HARN/cyberseceval"
  clone_or_pull https://github.com/meta-llama/PurpleLlama "$d" || { note "cyberseceval: clone FAIL"; return; }
  mk_env "$d"
  local req="$d/CybersecurityBenchmarks/requirements.txt"
  if [ -f "$req" ] && uvpip "$d" -r "$req"; then :; else
    note "cyberseceval: pip FAIL (see log)"; return
  fi
  cp "$BMM_ROOT/harnesses/cyberseceval/run.sh" "$HARN/cyberseceval/run.sh" 2>/dev/null && \
    chmod +x "$HARN/cyberseceval/run.sh" && c_ok "wrapper: $HARN/cyberseceval/run.sh (canonical)" || \
  write_wrapper cyberseceval <<'WRAP'
#!/usr/bin/env bash
# CyberSecEval (PurpleLlama) against a local OpenAI-compatible endpoint.
# Spec is PROVIDER::model::api_key::base_url (4 fields); the 4th points it at
# llama-swap. "model not in valid list" is a harmless warning.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; PY="$HERE/.venv/bin"
CB="$HERE/CybersecurityBenchmarks"; B="${CSE_BENCHMARK:-mitre}"
LLM="OPENAI::$BMM_MODEL::$BMM_API_KEY::$BMM_ENDPOINT"
case "$B" in
  mitre)            PROMPTS="$CB/datasets/mitre/mitre_benchmark_100_per_category_with_augmentation.json" ;;
  instruct)         PROMPTS="$CB/datasets/instruct/instruct.json" ;;
  autocomplete)     PROMPTS="$CB/datasets/autocomplete/autocomplete.json" ;;
  prompt_injection) PROMPTS="$CB/datasets/prompt_injection/prompt_injection.json" ;;
  *)                PROMPTS="$(ls "$CB/datasets/$B/"*.json 2>/dev/null | head -1)" ;;
esac
[ -f "$PROMPTS" ] || PROMPTS="$(ls "$CB/datasets/$B/"*.json 2>/dev/null | head -1)"
if [ -z "$PROMPTS" ] || [ ! -f "$PROMPTS" ]; then
  echo 'BMM_RESULT={"status":"error","score":null,"metrics":{"note":"prompts file not found"}}'; exit 0
fi
WORK="$HERE/work"; mkdir -p "$WORK"
echo "[cyberseceval] benchmark=$B model=$BMM_MODEL num-test-cases=$BMM_LIMIT"
EXTRA=(); [ "$B" = mitre ] && EXTRA=(--judge-llm "$LLM" --expansion-llm "$LLM")
"$PY/python" -m CybersecurityBenchmarks.benchmark.run --benchmark="$B" \
  --prompt-path="$PROMPTS" --response-path="$WORK/${B}_responses.json" \
  --stat-path="$WORK/${B}_stat.json" --llm-under-test="$LLM" \
  --num-test-cases="$BMM_LIMIT" "${EXTRA[@]}" \
  2>&1 | tee "$HERE/last.log" | grep --line-buffered -vE 'DeprecationWarning|TripleDES|escape sequence' || true
[ -f "$WORK/${B}_stat.json" ] \
  && echo "BMM_RESULT={\"status\":\"completed\",\"score\":null,\"score_label\":\"see_stat\",\"metrics\":{\"benchmark\":\"$B\",\"stat_file\":\"$WORK/${B}_stat.json\"}}" \
  || echo 'BMM_RESULT={"status":"partial","score":null,"metrics":{"note":"no stat file — see last.log"}}'
WRAP
  note "cyberseceval: installed (no docker; MITRE benchmark by default)"
}

# ------------------------------------------------------------- Cybench ------
install_cybench(){
  local d="$HARN/cybench"
  clone_or_pull https://github.com/andyzorigin/cybench "$d" || { note "cybench: clone FAIL"; return; }
  write_wrapper cybench <<'WRAP'
#!/usr/bin/env bash
# Cybench runs CTF challenges in Docker containers -> docker mandatory.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if ! command -v docker >/dev/null 2>&1; then
  echo "[cybench] docker not installed. Run scripts/RUN-ME-sudo.sh first."
  echo 'BMM_RESULT={"status":"error","score":null,"metrics":{"note":"needs docker"}}'
  exit 0
fi
export OPENAI_API_BASE="$BMM_ENDPOINT" OPENAI_API_KEY="$BMM_API_KEY"
echo "[cybench] use ./run_task.sh with --model openai/$BMM_MODEL per repo README."
echo 'BMM_RESULT={"status":"partial","score":null,"metrics":{"note":"docker present; drive cybench/run_task.sh"}}'
WRAP
  note "cybench: cloned (NEEDS docker to run)"
}

# --------------------------------------------------------------- GAIA -------
install_gaia(){
  local d="$HARN/gaia"
  mk_env "$d"
  uvpip "$d" datasets huggingface-hub >/dev/null 2>&1 || true
  write_wrapper gaia <<'WRAP'
#!/usr/bin/env bash
# GAIA needs (a) HF access to the gated gaia-benchmark/GAIA dataset and (b) a
# web-search-capable agent. bmm ships neither agent nor keys by default.
set -uo pipefail
if [ -z "${TAVILY_API_KEY:-}${SERPER_API_KEY:-}${BING_API_KEY:-}" ]; then
  echo "[gaia] no web-search API key set (TAVILY_API_KEY / SERPER_API_KEY / BING_API_KEY)."
  echo 'BMM_RESULT={"status":"error","score":null,"metrics":{"note":"needs a search API key + agent scaffold"}}'
  exit 0
fi
echo "[gaia] wire a search-tool agent against openai/$BMM_MODEL (see SETUP.md)."
echo 'BMM_RESULT={"status":"partial","score":null,"metrics":{"note":"search key present; agent scaffold not bundled"}}'
WRAP
  note "gaia: prepped (needs search key + an agent scaffold — see SETUP.md)"
}

# ------------------------------------------------------------ WebArena ------
install_webarena(){
  local d="$HARN/webarena"
  clone_or_pull https://github.com/web-arena-x/webarena "$d" || { note "webarena: clone FAIL"; return; }
  mk_env "$d"
  uvpip "$d" -r "$d/requirements.txt" >/dev/null 2>&1 || note "webarena: pip partial"
  write_wrapper webarena <<'WRAP'
#!/usr/bin/env bash
# WebArena needs the self-hosted site containers (docker) + node tooling.
set -uo pipefail
miss=""
command -v docker >/dev/null 2>&1 || miss="$miss docker"
command -v node   >/dev/null 2>&1 || miss="$miss node"
if [ -n "$miss" ]; then
  echo "[webarena] missing:$miss. Run scripts/RUN-ME-sudo.sh, then bring up the site containers."
  echo 'BMM_RESULT={"status":"error","score":null,"metrics":{"note":"needs docker + node + hosted sites"}}'
  exit 0
fi
echo "[webarena] point BASE_URL at your hosted sites and run run.py per README."
echo 'BMM_RESULT={"status":"partial","score":null,"metrics":{"note":"infra present; configure hosted sites"}}'
WRAP
  note "webarena: cloned (NEEDS docker + node + hosted sites)"
}

c_info "== Tier 3 installs =="
install_cyberseceval
install_cybench
install_gaia
install_webarena
echo
c_info "Tier 3 summary:"; printf "$SUMMARY"
