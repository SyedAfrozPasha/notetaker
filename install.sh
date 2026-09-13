#!/usr/bin/env bash
set -euo pipefail

MIN_PY_MINOR=10
MAX_PY_MINOR=11

find_python() {
  for candidate in python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      local major minor
      major=$("$candidate" -c 'import sys; print(sys.version_info.major)')
      minor=$("$candidate" -c 'import sys; print(sys.version_info.minor)')
      if [ "$major" = "3" ] && [ "$minor" -ge "$MIN_PY_MINOR" ] && [ "$minor" -le "$MAX_PY_MINOR" ]; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON_BIN=$(find_python) || {
  echo "error: notetaker requires Python 3.10 or 3.11 (faster-whisper's PyAV dependency" >&2
  echo "       does not reliably install on 3.12+/3.13). Install one, e.g.:" >&2
  echo "         brew install python@3.11" >&2
  echo "       then re-run ./install.sh" >&2
  exit 1
}

echo "Using $PYTHON_BIN ($("$PYTHON_BIN" --version))"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"

if [ -f "$HOME/.notetaker/current_session.json" ]; then
  echo "error: a recording is in progress (found ~/.notetaker/current_session.json)." >&2
  echo "       Run 'notetaker stop' first, then re-run ./install.sh" >&2
  exit 1
fi

if [ -d "$VENV_DIR" ]; then
  echo "Existing virtualenv found at $VENV_DIR, recreating it..."
  # The dashboard service runs out of this venv; stop it if it is running so
  # it doesn't crash-loop while the venv is rebuilt, and restart it at the end.
  RESTART_SERVICES=""
  if command -v brew >/dev/null 2>&1; then
    for svc in notetaker-dashboard; do
      if brew services list 2>/dev/null | grep -E "^$svc\s+(started|scheduled|error)" >/dev/null; then
        echo "Stopping $svc while the virtualenv is rebuilt..."
        brew services stop "$svc" >/dev/null 2>&1 || true
        RESTART_SERVICES="$RESTART_SERVICES $svc"
      fi
    done
  fi
  rm -rf "$VENV_DIR"
fi

# Older installs ran the menu bar as its own `notetaker-menubar` service. The
# menu bar now lives inside the `notetaker dashboard` process, so a leftover
# menubar service would put a second icon in the menu bar — retire it.
if command -v brew >/dev/null 2>&1 && brew services list 2>/dev/null | grep -E "^notetaker-menubar\s" >/dev/null; then
  echo "Retiring the old notetaker-menubar service (the menu bar is part of notetaker-dashboard now)..."
  brew services stop notetaker-menubar >/dev/null 2>&1 || true
  brew uninstall notetaker-menubar >/dev/null 2>&1 || true
fi
rm -f "$REPO_DIR/Formula/notetaker-menubar.rb"

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -e "$REPO_DIR[dev]"   # [dev] = pytest, so the documented test suite works from a fresh install

BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/notetaker" "$BIN_DIR/notetaker"

echo ""
echo "Generating the personal Homebrew formula for 'brew services' (dashboard + menu bar, one process)..."
"$VENV_DIR/bin/python" -m notetaker.brew_formula

for svc in ${RESTART_SERVICES:-}; do
  echo "Restarting $svc..."
  brew services start "$svc" >/dev/null 2>&1 || echo "warning: could not restart $svc; run 'brew services start $svc'" >&2
done

echo ""
echo "Installed. Make sure $BIN_DIR is on your PATH, then run:"
echo "  notetaker init"
