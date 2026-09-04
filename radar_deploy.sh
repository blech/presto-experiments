#!/usr/bin/env bash
#
# Deploy the flight radar to a Presto over USB using mpremote.
#
#   * application modules go into /prestoradar/ so the stock launcher ignores
#     them (it only lists .py files at the root)
#   * shared modules (netlog, screenshot) go into /lib/, which MicroPython puts
#     on sys.path automatically -- imported by bare name
#   * the entry shim presto_radar.py is left at the root so the radar can be
#     started with `mpremote run presto_radar.py` or from the launcher
#   * no main.py is deployed -- the stock Pimoroni launcher is left untouched
#
# Desktop-only files (radar_debug.py, make_basemap.py, radar_listen.py,
# dev/*, screenshot_pull.py) are never copied. basemap_data.py and, if built,
# basemap.jpg (the map-mode raster backdrop) go into /prestoradar/.
#
# Usage:  ./radar_deploy.sh
# Then:   mpremote run presto_radar.py      (or reset and use the launcher)

set -euo pipefail
cd "$(dirname "$0")"

if ! command -v mpremote >/dev/null 2>&1; then
    echo "error: mpremote not found (pip install mpremote)" >&2
    exit 1
fi

# settings.py is per-location and gitignored; seed it from the template on first run.
if [ ! -f prestoradar/settings.py ]; then
    cp prestoradar/settings_example.py prestoradar/settings.py
    echo "Created prestoradar/settings.py from settings_example.py."
    echo "Set CENTER_LAT / CENTER_LON (and RADIUS_KM) in it, then re-run ./deploy.sh." >&2
    exit 1
fi

echo "Checking basemap matches settings.py ..."
python3 prestoradar/make_basemap.py --if-stale

echo "Creating :prestoradar/ and :lib/ ..."
mpremote mkdir :prestoradar 2>/dev/null || true
mpremote mkdir :lib 2>/dev/null || true

echo "Copying shared modules to :lib/ (netlog.py, screenshot.py) ..."
mpremote cp lib/netlog.py :lib/netlog.py
mpremote cp lib/screenshot.py :lib/screenshot.py
# Older deploys put screenshot.py in :prestoradar/, which would shadow :lib/.
mpremote rm :prestoradar/screenshot.py 2>/dev/null || true

echo "Copying prestoradar/radar.py + net.py + geometry.py + routes.py + feed.py + settings.py ..."
mpremote cp prestoradar/radar.py :prestoradar/radar.py
mpremote cp prestoradar/net.py :prestoradar/net.py
mpremote cp prestoradar/geometry.py :prestoradar/geometry.py
mpremote cp prestoradar/routes.py :prestoradar/routes.py
mpremote cp prestoradar/feed.py :prestoradar/feed.py
mpremote cp prestoradar/settings.py :prestoradar/settings.py

if [ -f prestoradar/basemap_data.py ]; then
    echo "Copying prestoradar/basemap_data.py ..."
    mpremote cp prestoradar/basemap_data.py :prestoradar/basemap_data.py
else
    echo "Skipping basemap_data.py (not generated yet -- run make_basemap.py)"
fi

# Raster backdrop for DISPLAY_MODE = "map" (PLAN item 8). Built out-of-band with
# `make_basemap.py --raster <image>`; regenerate it by hand when the centre or
# radius changes -- this script only copies whatever is present.
if [ -f prestoradar/basemap.jpg ]; then
    echo "Copying prestoradar/basemap.jpg (raster map-mode backdrop) ..."
    mpremote cp prestoradar/basemap.jpg :prestoradar/basemap.jpg
fi

echo "Copying presto_radar.py (root entry shim) ..."
mpremote cp presto_radar.py :presto_radar.py

echo
echo "Done. Start it with:"
echo "    mpremote run presto_radar.py"
echo "or reset the Presto and choose presto_radar.py in the launcher."
