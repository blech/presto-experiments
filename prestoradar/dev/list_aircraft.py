#!/usr/bin/env python3
"""
Desktop (CPython) smoke test for feed.py -- fetches the current aircraft list
using settings.py's location/radius and prints one line per aircraft.

Run from anywhere:
    python3 prestoradar/dev/list_aircraft.py
    python3 prestoradar/dev/list_aircraft.py --radius 50

Unlike radar_debug.py (which reimplements the HTTP GET with urllib to dump
raw response diagnostics), this calls feed.Feed._fetch() directly -- the same
coroutine radar.py runs on-device -- so a change to feed.py's parsing can be
checked here without deploying to the Presto.
"""

import argparse
import asyncio
import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import feed  # noqa: E402
from settings import (  # noqa: E402
    CENTER_LAT, CENTER_LON, RADIUS_KM, USER_AGENT, LEVEL_RATE_FPM, FETCH_INTERVAL_MS,
)

RADAR_HOST = "api.adsb.lol"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--radius", type=float, default=RADIUS_KM,
                     help="km from settings.CENTER_LAT/LON (default: settings.RADIUS_KM)")
    ap.add_argument("--exclude_ground", action="store_true",
                     help="hide aircraft on the ground, same filter as settings.HIDE_ON_GROUND")
    ap.add_argument("--limit", type=int, metavar="N",
                     help="print at most N aircraft")
    args = ap.parse_args()

    radius_nm = round(args.radius / 1.852)
    path = f"/v2/point/{CENTER_LAT}/{CENTER_LON}/{radius_nm}"
    print(f"GET https://{RADAR_HOST}{path}\n")

    f = feed.Feed(RADAR_HOST, path, USER_AGENT, LEVEL_RATE_FPM, FETCH_INTERVAL_MS)
    planes = asyncio.run(f._fetch())

    if planes is None:
        print("fetch failed -- see the log lines above")
        return 1

    if args.exclude_ground:
        # Same filter radar.py's _hidden() applies when HIDE_ON_GROUND is set.
        planes = [p for p in planes if not p.on_ground]

    if not planes:
        print("0 aircraft in range")
        return 0

    planes.sort(key=lambda p: p.dst if p.dst is not None else 1e9)
    total = len(planes)
    if args.limit is not None:
        planes = planes[:args.limit]

    print(f"{'callsign':9} {'hex':6} {'alt':>7} {'gs':>5} {'hdg':>5} "
          f"{'vstate':7} {'dst':>6} {'dir':>5}  type")
    for p in planes:
        alt = p.alt if p.alt is not None else "--"
        hdg = f"{p.heading:.0f}" if p.heading is not None else "--"
        dst = f"{p.dst:.1f}" if p.dst is not None else "--"
        dirn = f"{p.dir:.0f}" if p.dir is not None else "--"
        print(f"{p.callsign:9} {p.hex:6} {str(alt):>7} {p.gs:5.0f} {hdg:>5} "
              f"{p.vstate:7} {dst:>6} {dirn:>5}  {p.type or ''}")

    shown = f"{len(planes)} of {total}" if args.limit is not None and len(planes) < total else str(total)
    print(f"\n{shown} aircraft")
    return 0


if __name__ == "__main__":
    sys.exit(main())
