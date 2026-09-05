#!/usr/bin/env python3
"""
Desktop (CPython) smoke test for routes.py -- looks up an aircraft's route
via api.adsbdb.com, the same lookup radar.py's tap-to-inspect panel does.

adsbdb only indexes routes by callsign (the "flight" field in the adsb.lol
feed, e.g. BAW123), not by ICAO24 hex id (the "hex" field, e.g. 4ca1b2 --
also called icao24 elsewhere). If you only have the hex, pass --hex: this
first fetches the live feed (settings.py's centre/radius) to find that
aircraft's current callsign, then looks up the route for it.

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


async def resolve_callsign(hex_id, radius_km):
    """Find hex_id's current callsign in the live feed. Returns (callsign,
    error) -- exactly one is set."""
    radius_nm = round(radius_km / 1.852)
    path = f"/v2/point/{CENTER_LAT}/{CENTER_LON}/{radius_nm}"
    f = feed.Feed(RADAR_HOST, path, USER_AGENT, LEVEL_RATE_FPM, FETCH_INTERVAL_MS)
    planes = await f._fetch()
    if planes is None:
        return None, "feed fetch failed -- see the log lines above"

    hex_id = hex_id.lower()
    for p in planes:
        if p["hex"].lower() == hex_id:
            cs = p["callsign"]
            if not cs or routes.is_hex_id(cs):
                return None, f"{hex_id} is in range but isn't broadcasting a callsign right now"
            return cs, None
    return None, (f"{hex_id} not in the current {radius_km:g} km feed around "
                   f"({CENTER_LAT}, {CENTER_LON}) -- try --radius, or check it's airborne")


async def lookup_route(callsign):
    routes.request(callsign)
    while routes.get(callsign) == "":
        await asyncio.sleep(0.25)
    return routes.get(callsign)


async def run(args):
    if args.hex:
        callsign, err = await resolve_callsign(args.hex, args.radius)
        if err:
            print(err)
            return 1
        print(f"hex {args.hex} -> callsign {callsign}")
    else:
        callsign = args.callsign.strip()

    route = await lookup_route(callsign)
    if route is None:
        print(f"{callsign}: no route on file for this callsign")
    else:
        origin, dest = route
        print(f"{callsign}: {origin} -> {dest}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--hex", metavar="ID",
                    help="ICAO24 hex id (aka 'hex'/icao24), e.g. 4ca1b2 -- "
                         "resolved to a live callsign first")
    g.add_argument("--callsign", metavar="CS",
                    help="flight callsign (aka 'flight'/ident), e.g. BAW123")
    ap.add_argument("--radius", type=float, default=RADIUS_KM,
                    help="km to search when resolving --hex (default: settings.RADIUS_KM)")
    args = ap.parse_args()

    if args.hex and not routes.is_hex_id(args.hex):
        ap.error(f"--hex expects a 6-character ICAO24 id, got {args.hex!r}")

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
