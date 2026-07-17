#!/usr/bin/env bash
# Install LiveCodeBench for API generation against llama-swap (no torch/vllm).
#
# Why this is its own script (and not in install-tier1.sh):
#   * LiveCodeBench pins torch>=2.3 and vllm>=0.5 in pyproject — multi-GB and
#     unused for API generation — so we DON'T `pip install -e .`; we install just
#     the runtime deps its OpenAI runner needs.
#   * one of those deps, `pyext`, fails to build on Python 3.12
#     (uses inspect.getargspec, removed in 3.11), so this venv uses Python 3.10.
#   * LiveCodeBench validates --model against a built-in registry and its OpenAI
#     runner points at api.openai.com — bmm_register.py registers the local alias
#     and the wrapper points the OpenAI SDK at llama-swap via env.
#
# Reality check: at ~5 tok/s a full LiveCodeBench code-generation pass (hundreds
# of problems × ~2k output tokens) is on the order of *days*. There is no clean
# --limit, so treat this as "installed and wired", and prefer `evalplus` or the
# `code-mini` built-in for day-to-day code signal. It also downloads its dataset
# from HuggingFace on first run.
set -uo pipefail
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
BMM_ROOT="${BMM_ROOT:-$HOME/benchmarks}"
d="$BMM_ROOT/harnesses/livecodebench"

echo "[lcb] repo: $d"
if [ ! -d "$d/.git" ]; then
  git clone --depth 1 https://github.com/LiveCodeBench/LiveCodeBench "$d" || {
    echo "[lcb] clone failed"; exit 1; }
fi

echo "[lcb] creating Python 3.10 venv (pyext won't build on 3.12)…"
uv venv --python 3.10 "$d/.venv" 2>&1 | tail -1 || { echo "[lcb] venv failed"; exit 1; }

echo "[lcb] installing API-only deps (no torch/vllm)…"
uv pip install --python "$d/.venv/bin/python" \
  openai datasets tqdm numpy pyext python-dateutil 2>&1 | tail -4 \
  || { echo "[lcb] dep install failed — see output above"; exit 1; }

echo "[lcb] writing model-registration shim…"
cat > "$d/bmm_register.py" <<'PYREG'
"""Register the local llama-swap alias into LiveCodeBench's model store, then run
its CLI. LCB does `LanguageModelStore[args.model]`, so an unregistered alias
raises KeyError; we add it with the OpenAI-chat style. The OpenAI runner reads
OPENAI_KEY + OPENAI_BASE_URL from the environment (set by run.sh)."""
import os
from datetime import datetime

from lcb_runner.lm_styles import LanguageModel, LanguageModelStore, LMStyle

_name = os.environ.get("BMM_MODEL", "").strip()
if _name and _name not in LanguageModelStore:
    LanguageModelStore[_name] = LanguageModel(
        _name, _name, LMStyle.OpenAIChat, datetime(2020, 1, 1), link="local")

from lcb_runner.runner.main import main  # noqa: E402

if __name__ == "__main__":
    main()
PYREG

echo "[lcb] writing wrapper…"
mkdir -p "$d"
cat > "$d/run.sh" <<'WRAP'
#!/usr/bin/env bash
# LiveCodeBench code-generation against llama-swap. Heavy: full set is hundreds
# of problems; at ~5 tok/s that is days. Ctrl-C to stop early. First run
# downloads the dataset from HuggingFace.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; PY="$HERE/.venv/bin"
# LCB's OpenAI runner uses OPENAI_KEY (not OPENAI_API_KEY); the SDK picks up
# OPENAI_BASE_URL to route at llama-swap instead of api.openai.com.
export OPENAI_KEY="$BMM_API_KEY" OPENAI_API_KEY="$BMM_API_KEY"
export OPENAI_BASE_URL="$BMM_ENDPOINT"
export PYTHONUNBUFFERED=1
REL="${LCB_RELEASE:-release_latest}"
echo "[lcb] codegen on $BMM_MODEL (release=$REL, full set — Ctrl-C to stop early)"
"$PY/python" "$HERE/bmm_register.py" --model "$BMM_MODEL" \
  --scenario codegeneration --evaluate --release_version "$REL" \
  --n 1 --temperature 0 2>&1 | tee "$HERE/.last_run.log"
PASS="$(grep -oiE 'pass@1[^0-9]*[0-9.]+' "$HERE/.last_run.log" | grep -oE '[0-9.]+' | head -1)"
if [ -n "$PASS" ]; then
  awk -v a="$PASS" 'BEGIN{s=(a>1)?a/100:a; printf "BMM_RESULT={\"score\":%.4f,\"score_label\":\"lcb_pass@1\",\"status\":\"completed\"}\n", s}'
else
  echo 'BMM_RESULT={"status":"partial","score":null,"metrics":{"note":"no pass@1 parsed — see .last_run.log (dataset download? interrupted?)"}}'
fi
WRAP
chmod +x "$d/run.sh"
echo "[lcb] done. Try:  bmm run --model <m> --bench livecodebench   (expect a long run)"
