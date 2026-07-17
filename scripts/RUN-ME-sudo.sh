#!/usr/bin/env bash
# =============================================================================
#  RUN-ME-sudo.sh — the ONLY steps bmm needs you (a human with sudo) to run.
#
#  Installs Docker + Node.js, which several agentic benchmarks require to
#  execute (Terminal-Bench, SWE-bench, Cybench, WebArena, Aider-polyglot).
#  Everything else in bmm runs without sudo.
#
#  Usage:   bash ~/benchmarks/scripts/RUN-ME-sudo.sh
#  Safe to re-run; it skips anything already present.
# =============================================================================
set -uo pipefail

echo "This installs Docker Engine and Node.js (LTS). Requires sudo."
read -rp "Proceed? [y/N] " ok; [ "$ok" = y ] || { echo "aborted."; exit 0; }

if command -v apt-get >/dev/null 2>&1; then
  PM=apt
elif command -v dnf >/dev/null 2>&1; then
  PM=dnf
else
  PM=unknown
fi
echo "package manager: $PM"

# ---- Docker ----------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  echo "[docker] already installed: $(docker --version)"
else
  echo "[docker] installing via get.docker.com convenience script"
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sudo sh /tmp/get-docker.sh
  echo "[docker] adding $USER to the docker group (log out/in to take effect)"
  sudo usermod -aG docker "$USER" || true
  sudo systemctl enable --now docker 2>/dev/null || true
fi

# ---- Node.js ---------------------------------------------------------------
if command -v node >/dev/null 2>&1; then
  echo "[node] already installed: $(node --version)"
elif [ "$PM" = apt ]; then
  echo "[node] installing Node.js LTS via NodeSource"
  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
  sudo apt-get install -y nodejs
elif [ "$PM" = dnf ]; then
  sudo dnf install -y nodejs
else
  echo "[node] unknown package manager — install Node.js LTS manually"
fi

echo
echo "Done. Verify with:  docker run --rm hello-world   &&   node --version"
echo "Then re-check bmm status:  bmm list   (docker-gated benchmarks flip to READY)"
