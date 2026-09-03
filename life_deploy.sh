#!/usr/bin/env bash
#
# Deploy the Life implementation to a Presto over USB using mpremote.
# This also copies the required RLEs (Life initialisation patterns).
#
# Usage:  ./life-deploy.sh
# Then:   mpremote run life.py      (or reset and use the launcher)

set -euo pipefail
cd "$(dirname "$0")"

if ! command -v mpremote >/dev/null 2>&1; then
    echo "error: mpremote not found (pip install mpremote)" >&2
    exit 1
fi

echo "Creating :life/ ..."
mpremote fs mkdir :life 2>/dev/null || true

echo "Copying Life library and RLEs ..."
mpremote fs cp -r life/life.py :life/
mpremote fs cp -r life/__init__.py :life/
mpremote fs cp -r life/rles :life/

echo "Copying life.py (root entry shim) ..."
mpremote fs cp life.py :life.py

echo
echo "Done. Start it with:"
echo "    mpremote run life.py"
echo "or reset the Presto and choose 'Game of Life' in the launcher."
