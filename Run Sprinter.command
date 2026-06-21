#!/bin/bash
# Double-clickable launcher for the Sprinter math solver on macOS.
# (On Windows, use "Run Sprinter.bat" instead.)
cd "$(dirname "$0")" || exit 1

# Pick whichever Python 3 is available.
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
    echo "Python 3 is not installed. Get it from https://www.python.org/downloads/"
    echo "Press Return to close."
    read -r
    exit 1
fi

# Make sure dependencies are installed (quiet check).
if ! "$PY" -c "import PIL, requests" 2>/dev/null; then
    echo "Installing required packages, please wait..."
    "$PY" -m pip install -r requirements.txt || {
        echo
        echo "Could not install dependencies. Press Return to close."
        read -r
        exit 1
    }
fi

# Launch the app.
"$PY" math_solver.py
