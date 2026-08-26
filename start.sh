#!/usr/bin/env bash
# ===========================================================================
#  Intelligent Hospital Workforce Scheduling System
#
#  macOS and Linux launcher. Run with:  bash start.sh
#  Sets everything up the first time, then starts straight away after that.
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo
echo " =========================================================="
echo "  Intelligent Hospital Workforce Scheduling System"
echo " =========================================================="
echo

# --- Find a suitable Python ------------------------------------------------
PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version=$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")
        major=${version%%.*}
        minor=${version##*.}
        if [ "$major" -eq 3 ] && [ "$minor" -ge 9 ]; then
            PY="$candidate"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo " [X] Python 3.9 or newer was not found."
    echo "     Install it from https://www.python.org/downloads/ and try again."
    exit 1
fi
echo " Using $($PY --version)"

# --- Create the virtual environment ---------------------------------------
if [ ! -x ".venv/bin/python" ]; then
    echo " [1/4] Creating a private Python environment..."
    "$PY" -m venv .venv
else
    echo " [1/4] Python environment found."
fi
VENV_PY=".venv/bin/python"

# --- Install dependencies --------------------------------------------------
echo " [2/4] Checking dependencies..."
if ! "$VENV_PY" -m pip install --quiet --disable-pip-version-check -r requirements.txt; then
    echo
    echo " [X] Could not install the dependencies."
    echo "     This step needs an internet connection the first time."
    exit 1
fi

# --- Build the demonstration database -------------------------------------
if [ ! -f "instance/hospital.db" ]; then
    echo " [3/4] Building the demonstration hospital (about 15 seconds)..."
    "$VENV_PY" seed.py
else
    echo " [3/4] Database found, keeping the existing data."
fi

# --- Run -------------------------------------------------------------------
echo " [4/4] Starting the portal..."
cat <<'BANNER'

 ----------------------------------------------------------
   Open your browser at:  http://127.0.0.1:5000

   Sign in with:          TH-ADM-001
   Password:              Password123

   Press Ctrl+C to stop the portal.
 ----------------------------------------------------------

BANNER

exec "$VENV_PY" run.py
