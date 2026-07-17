#!/usr/bin/env bash
# Install every tier's harnesses. Best-effort: failures in one harness never
# abort the rest. Heavy/torch installs are bounded by BMM_INSTALL_TIMEOUT.
DIR="$(cd "$(dirname "$0")" && pwd)"
set -uo pipefail
echo "=== bmm: installing Tier 1, 2, 3 harnesses ==="
echo "(docker-gated benchmarks install their code but need docker to run —"
echo " see scripts/RUN-ME-sudo.sh and SETUP.md)"
echo
bash "$DIR/install-tier1.sh"
echo; bash "$DIR/install-tier2.sh"
echo; bash "$DIR/install-tier3.sh"
echo
echo "=== done. Check status with:  bmm list ==="
