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
  # The dashboard/menubar services run out of this venv; stop any that are
  # running so they don't crash-loop while it is rebuilt, and restart them
  # at the end.
  RESTART_SERVICES=""
  if command -v brew >/dev/null 2>&1; then
    for svc in notetaker-dashboard notetaker-menubar; do
      if brew services list 2>/dev/null | grep -E "^$svc\s+(started|scheduled|error)" >/dev/null; then
        echo "Stopping $svc while the virtualenv is rebuilt..."
        brew services stop "$svc" >/dev/null 2>&1 || true
        RESTART_SERVICES="$RESTART_SERVICES $svc"
      fi
    done
  fi
  rm -rf "$VENV_DIR"
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -e "$REPO_DIR"

BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/notetaker" "$BIN_DIR/notetaker"

echo ""
echo "Generating personal Homebrew formulas for 'brew services' (dashboard + menu bar)..."
"$VENV_DIR/bin/python" -m notetaker.brew_formula

for svc in ${RESTART_SERVICES:-}; do
  echo "Restarting $svc..."
  brew services start "$svc" >/dev/null 2>&1 || echo "warning: could not restart $svc; run 'brew services start $svc'" >&2
done

echo ""
echo "Installed. Make sure $BIN_DIR is on your PATH, then run:"
echo "  notetaker init"
