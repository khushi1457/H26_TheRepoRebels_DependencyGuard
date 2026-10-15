#!/usr/bin/env bash
# One-time setup for DependencyGuard on macOS.
# Run from inside the dependencyguard folder:  bash setup_mac.sh
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11+ not found. Install it from https://www.python.org/downloads/macos/ and re-run." >&2
  exit 1
fi
echo "Using $($PY --version) at $(command -v $PY)"

if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt

if [ ! -d .git ]; then
  git init -b main
fi

if [ ! -d ../LinkWatch ]; then
  git clone https://github.com/rishrek/LinkWatch.git ../LinkWatch
fi

echo
echo "Setup complete. Next:"
echo "  source .venv/bin/activate"
echo "  cd backend && python -m app.services.manifests ../../LinkWatch"
