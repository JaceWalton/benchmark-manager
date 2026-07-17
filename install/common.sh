#!/usr/bin/env bash
# Shared helpers for bmm harness installers.
# Each harness gets: harnesses/<key>/  with its own uv venv (.venv) + run.sh
# wrapper. Wrappers read BMM_MODEL / BMM_ENDPOINT / BMM_API_KEY / BMM_LIMIT and
# print a final line:  BMM_RESULT={json}  that the TUI parses.
set -uo pipefail

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
BMM_ROOT="${BMM_ROOT:-$HOME/benchmarks}"
HARN="$BMM_ROOT/harnesses"
LOGD="$BMM_ROOT/logs"
mkdir -p "$HARN" "$LOGD"

PYVER="3.12"
# Per-step install timeout (seconds) so a runaway torch download can't hang the
# whole run. Override with BMM_INSTALL_TIMEOUT.
INSTALL_TIMEOUT="${BMM_INSTALL_TIMEOUT:-900}"

c_info(){ printf "\033[1;36m[install]\033[0m %s\n" "$*"; }
c_ok(){   printf "\033[1;32m[  ok  ]\033[0m %s\n" "$*"; }
c_warn(){ printf "\033[1;33m[ warn ]\033[0m %s\n" "$*"; }
c_err(){  printf "\033[1;31m[ FAIL ]\033[0m %s\n" "$*"; }

have(){ command -v "$1" >/dev/null 2>&1; }

# clone_or_pull <giturl> <dest>
clone_or_pull(){
  local url="$1" dest="$2"
  if [ -d "$dest/.git" ]; then
    c_info "updating $(basename "$dest")"
    git -C "$dest" pull --ff-only --quiet 2>/dev/null || c_warn "pull skipped"
  else
    c_info "cloning $url"
    git clone --depth 1 "$url" "$dest" 2>&1 | tail -1 || { c_err "clone failed"; return 1; }
  fi
}

# mk_env <dir>  — create an isolated python venv for a harness
mk_env(){
  local dir="$1"
  if [ ! -x "$dir/.venv/bin/python" ]; then
    uv venv --python "$PYVER" "$dir/.venv" 2>&1 | tail -1
  fi
}

# uvpip <dir> <pip args...> — install into a harness venv, with a timeout
uvpip(){
  local dir="$1"; shift
  timeout "$INSTALL_TIMEOUT" uv pip install --python "$dir/.venv/bin/python" "$@" 2>&1 | tail -3
  return "${PIPESTATUS[0]}"
}

# write_wrapper <key> <<'EOF' ... EOF  — install a run.sh from stdin
write_wrapper(){
  local key="$1"
  local dst="$HARN/$key/run.sh"
  mkdir -p "$HARN/$key"
  cat > "$dst"
  chmod +x "$dst"
  c_ok "wrapper: $dst"
}
