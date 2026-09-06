#!/usr/bin/env python3
"""
Desktop (CPython) smoke test for routes.py -- looks up an aircraft's route
the same way radar.py's tap-to-inspect panel does.

routes.py needs a live plane dict, not just a callsign: since it started
cross-checking candidate routes against the aircraft's actual position and
heading (adsbdb alone can hand back a stale or simply wrong route for a
multi-leg rotation -- see prestoradar/dev/route_check.py, where this was
prototyped), it needs to know where the plane actually is. So this always
resolves --hex or --callsign against the live feed (settings.py's
centre/radius) first, then hands the resulting plane dict to routes.request()
exactly as ui.py does on a tap.

    python3 prestoradar/dev/route_lookup.py --callsign BAW123
    python3 prestoradar/dev/route_lookup.py --hex 4ca1b2
    python3 prestoradar/dev/route_lookup.py --hex 4ca1b2 --radius 100
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
import routes  # noqa: E402
from settings import (  # noqa: E402
    CENTER_LAT, CENTER_LON, RADIUS_KM, USER_AGENT, LEVEL_RATE_FPM, FETCH_INTERVAL_MS,
)

RADAR_HOST = "api.adsb.lol"


async def find_plane(hex_id, callsign, radius_km):
    """Find hex_id or callsign in the live local feed. Returns (plane,
    error) -- exactly one is set."""
    radius_nm = round(radius_km / 1.852)
    path = f"/v2/point/{CENTER_LAT}/{CENTER_LON}/{radius_nm}"
    f = feed.Feed(RADAR_HOST, path, USER_AGENT, LEVEL_RATE_FPM, FETCH_INTERVAL_MS)
    planes = await f._fetch()
    if planes is None:
        return None, "feed fetch failed -- see the log lines above"

    hex_id = hex_id.lower() if hex_id else None
    callsign = callsign.upper() if callsign else None
    for p in planes:
        if (hex_id and p["hex"].lower() == hex_id) or (callsign and p["callsign"].upper() == callsign):
            if not p["callsign"] or p["callsign"].lower() == p["hex"].lower():
                return None, f"{hex_id or callsign} is in range but isn't broadcasting a usable callsign right now"
            return p, None
    who = hex_id or callsign
    return None, (f"{who} not in the current {radius_km:g} km feed around "
                   f"({CENTER_LAT}, {CENTER_LON}) -- try --radius, or check it's airborne")


async def lookup_route(plane):
    routes.request(plane)
    while routes.get(plane["callsign"]) == "":
        await asyncio.sleep(0.25)
    return routes.get(plane["callsign"])


async def run(args):
    plane, err = await find_plane(
        args.hex, args.callsign.strip() if args.callsign else None, args.radius)
    if err:
        print(err)
        return 1

    print(f"{plane['callsign']} (hex {plane['hex']})")
    route = await lookup_route(plane)
    if route is None:
        print(f"{plane['callsign']}: no route on file for this callsign")
    else:
        origin, dest = route
        print(f"{plane['callsign']}: {origin} -> {dest}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--hex", metavar="ID",
                    help="ICAO24 hex id (aka 'hex'/icao24), e.g. 4ca1b2")
    g.add_argument("--callsign", metavar="CS",
                    help="flight callsign (aka 'flight'/ident), e.g. BAW123")
    ap.add_argument("--radius", type=float, default=RADIUS_KM,
                    help="km to search the live feed (default: settings.RADIUS_KM)")
    args = ap.parse_args()

    if args.hex and not routes.is_hex_id(args.hex):
        ap.error(f"--hex expects a 6-character ICAO24 id, got {args.hex!r}")

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
