#!/usr/bin/env bash
#
# Deploy the flight radar to a Presto over USB using mpremote.
#
#   * application modules go into /prestoradar/ so the stock launcher ignores
#     them (it only lists .py files at the root)
#   * the entry shim presto_radar.py is left at the root so the radar can be
#     started with `mpremote run presto_radar.py` or from the launcher
#   * no main.py is deployed -- the stock Pimoroni launcher is left untouched
#
# Desktop-only files (radar_debug.py, make_basemap.py) are never copied.
#
# Usage:  ./deploy.sh
# Then:   mpremote run presto_radar.py      (or reset and use the launcher)

set -euo pipefail
cd "$(dirname "$0")"

if ! command -v mpremote >/dev/null 2>&1; then
    echo "error: mpremote not found (pip install mpremote)" >&2
    exit 1
fi

echo "Creating :prestoradar/ ..."
mpremote mkdir :prestoradar 2>/dev/null || true

echo "Copying prestoradar/radar.py ..."
mpremote cp prestoradar/radar.py :prestoradar/radar.py

if [ -f prestoradar/basemap_data.py ]; then
    echo "Copying prestoradar/basemap_data.py ..."
    mpremote cp prestoradar/basemap_data.py :prestoradar/basemap_data.py
else
    echo "Skipping basemap_data.py (not generated yet -- run make_basemap.py)"
fi

echo "Copying presto_radar.py (root entry shim) ..."
mpremote cp presto_radar.py :presto_radar.py

echo
echo "Done. Start it with:"
echo "    mpremote run presto_radar.py"
echo "or reset the Presto and choose presto_radar.py in the launcher."
