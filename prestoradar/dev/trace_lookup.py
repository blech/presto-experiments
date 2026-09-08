#!/usr/bin/env python3
"""
Desktop (CPython) smoke test for traces.py -- seeds an aircraft's position
history the same way radar.py's tap-to-inspect will.

Like dev/route_lookup.py, this resolves --hex or --callsign against the live
feed first (settings.py's centre/radius), then hands the resulting Plane to
traces.request() exactly as ui.py does on a tap. traces.py inflates the gzip
body with zlib here (the device uses the firmware's `deflate` module) so the
fetch/parse/project pipeline can be checked without a deploy.

    python3 prestoradar/dev/trace_lookup.py --callsign UAL599
    python3 prestoradar/dev/trace_lookup.py --hex aa79a6
    python3 prestoradar/dev/trace_lookup.py --hex aa79a6 --radius 100
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
import geometry  # noqa: E402
import traces  # noqa: E402
from settings import (  # noqa: E402
    CENTER_LAT, CENTER_LON, RADIUS_KM, USER_AGENT, LEVEL_RATE_FPM, FETCH_INTERVAL_MS,
)

RADAR_HOST = "api.adsb.lol"


async def find_plane(hex_id, callsign, radius_km):
    """Find hex_id or callsign in the live local feed. Returns (plane, error)
    -- exactly one is set. A trace only needs the hex, so unlike
    route_lookup.py this does not insist on a usable callsign."""
    radius_nm = round(radius_km / 1.852)
    path = f"/v2/point/{CENTER_LAT}/{CENTER_LON}/{radius_nm}"
    f = feed.Feed(RADAR_HOST, path, USER_AGENT, LEVEL_RATE_FPM, FETCH_INTERVAL_MS)
    planes = await f._fetch()
    if planes is None:
        return None, "feed fetch failed -- see the log lines above"

    hex_id = hex_id.lower() if hex_id else None
    callsign = callsign.upper() if callsign else None
    for p in planes:
        if (hex_id and p.hex.lower() == hex_id) or (callsign and p.callsign.upper() == callsign):
            return p, None
    who = hex_id or callsign
    return None, (f"{who} not in the current {radius_km:g} km feed around "
                   f"({CENTER_LAT}, {CENTER_LON}) -- try --radius, or check it's airborne")


async def seed_trace(plane):
    traces.request(plane)
    while traces.get(plane.hex) == "":
        await asyncio.sleep(0.25)
    return traces.get(plane.hex)


def describe(points):
    """Print the projected trail: extent, endpoints, altitude span."""
    print(f"  {len(points)} points (e, n km east/north of centre; alt ft or 'ground')")
    es = [e for e, _, _ in points]
    ns = [n for _, n, _ in points]
    alts = [a for _, _, a in points if isinstance(a, (int, float))]
    print(f"  bbox   e {min(es):+.1f}..{max(es):+.1f} km   n {min(ns):+.1f}..{max(ns):+.1f} km")
    if alts:
        print(f"  alt    {min(alts)}..{max(alts)} ft")
    for label, (e, n, a) in (("oldest", points[0]), ("newest", points[-1])):
        lat, lon = geometry.unproject(e, n)
        print(f"  {label:6} e={e:+8.2f} n={n:+8.2f}  ({lat:.4f}, {lon:.4f})  alt={a}")


async def run(args):
    plane, err = await find_plane(
        args.hex, args.callsign.strip() if args.callsign else None, args.radius)
    if err:
        print(err)
        return 1

    print(f"{plane.callsign or '(no callsign)'}  hex {plane.hex}  "
          f"alt {plane.alt}  {plane.gs:.0f} kt")
    print(f"live RAM trail so far: {len(plane.trail)} fix(es) "
          f"(one per feed fetch; this harness only fetched once)")

    seed = await seed_trace(plane)
    print()
    if seed is None:
        print(f"trace_recent: nothing on file for {plane.hex} "
              f"-- radar would fall back to the RAM trail")
    else:
        print(f"trace_recent for {plane.hex}:")
        describe(seed)

    chosen = traces.points_for(plane)
    print()
    print(f"points_for() would hand the renderer: "
          f"{'None' if chosen is None else str(len(chosen)) + ' points'} "
          f"({'network seed' if chosen is seed else 'RAM trail' if chosen is not None else 'nothing yet'})")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--hex", metavar="ID", help="ICAO24 hex id, e.g. aa79a6")
    g.add_argument("--callsign", metavar="CS", help="flight callsign, e.g. UAL599")
    ap.add_argument("--radius", type=float, default=RADIUS_KM,
                    help="km to search the live feed (default: settings.RADIUS_KM)")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
