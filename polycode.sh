#!/bin/bash
# Wrapper script to run PolyCode CLI with virtual environment

# Get the directory of this script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Activate virtual environment
if [ -d "$DIR/.venv" ]; then
    source "$DIR/.venv/bin/activate"
else
    echo "Error: Virtual environment not found in $DIR/.venv"
    exit 1
fi

# Run the Python application
exec python "$DIR/polycode.py" "$@"
