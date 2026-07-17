#!/usr/bin/env bash
# Installs the heavy, torch/vllm-pulling Tier-1 harnesses that the default
# installer skips: BFCL (gorilla) and LiveCodeBench. Dependency resolution
# alone takes several minutes; the download is multi-GB.
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "This installs BFCL + LiveCodeBench (heavy: torch/vllm, multi-GB)."
read -rp "Proceed? [y/N] " ok; [ "$ok" = y ] || { echo "aborted."; exit 0; }
BMM_INSTALL_HEAVY=1 BMM_INSTALL_TIMEOUT="${BMM_INSTALL_TIMEOUT:-1800}" \
  bash "$DIR/install-tier1.sh"
