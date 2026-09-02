"""
Desktop (CPython) debug harness for the adsb.lol feed used by presto_radar.py.

Run on the Mac:      python3 radar_debug.py
Try a tighter box:   python3 radar_debug.py --radius 15
Dump the raw body:   python3 radar_debug.py --save response.json

Uses only the standard library (urllib), so there is nothing to pip install.
It hits the same URL and runs the same parsing logic the Presto version does,
but prints everything about the HTTP response so a JSON parse error can be
pinned down: wrong status, HTML error page, gzip, truncation, chunked, etc.
"""

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import CENTER_LAT, CENTER_LON, RADIUS_KM

WIDTH = HEIGHT = 480
DEFAULT_RADIUS_NM = round(RADIUS_KM / 1.852)


def lat_lon_to_xy(lat, lon, box):
    min_lat, max_lat, min_lon, max_lon = box
    x = int(((lon - min_lon) / (max_lon - min_lon)) * WIDTH)
    y = int((1.0 - ((lat - min_lat) / (max_lat - min_lat))) * HEIGHT)
    return x, y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=int, default=DEFAULT_RADIUS_NM,
                    help="nautical miles, max 250 (default from settings.RADIUS_KM)")
    ap.add_argument("--save", metavar="PATH", help="write the raw response body here")
    args = ap.parse_args()

    # Same box derivation as presto_radar.py: centre +/- radius, longitude
    # scaled by cos(latitude).
    lat_span = args.radius / 60.0
    lon_span = args.radius / (60.0 * math.cos(math.radians(CENTER_LAT)))
    box = (CENTER_LAT - lat_span, CENTER_LAT + lat_span,
           CENTER_LON - lon_span, CENTER_LON + lon_span)

    url = f"https://api.adsb.lol/v2/point/{CENTER_LAT}/{CENTER_LON}/{args.radius}"
    print(f"GET {url}\n")

    # Mirror the Presto's urequests: plain GET, no Accept-Encoding, so the
    # server should not gzip the reply. adsb.lol rejects generic user agents,
    # so identify the app and give a contact URL.
    user_agent = "presto-radar/1.0 (+https://github.com/blech/presto-experiments)"
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        status, reason, headers = resp.status, resp.reason, resp.headers
        body = resp.read()
    except urllib.error.HTTPError as e:
        status, reason, headers = e.code, e.reason, e.headers
        body = e.read()
        print(f"HTTPError {status} {reason}")
    except urllib.error.URLError as e:
        print(f"request failed: {e!r}")
        return 1

    text = body.decode("utf-8", errors="replace")

    # --- everything about the response, before we try to parse it ---
    print(f"status            : {status} {reason}")
    print(f"content-type      : {headers.get('Content-Type')}")
    print(f"content-encoding  : {headers.get('Content-Encoding')}")
    print(f"transfer-encoding : {headers.get('Transfer-Encoding')}")
    print(f"header length     : {headers.get('Content-Length')}")
    print(f"actual body bytes : {len(body)}")
    print()
    print("first 500 chars of body:")
    print("-" * 60)
    print(text[:500])
    print("-" * 60)
    print()

    if args.save:
        with open(args.save, "wb") as fh:
            fh.write(body)
        print(f"raw body written to {args.save}\n")

    # --- parse exactly the way we want the Presto to ---
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"JSON PARSE ERROR: {e}")
        print("-> body is not valid JSON. Check status/content-type above.")
        print("   Common causes: 429 rate limit, HTML error page, gzip'd body,")
        print("   or a response too big for the Presto to buffer (shrink --radius).")
        return 1

    if not isinstance(data, dict):
        print(f"unexpected top-level type: {type(data).__name__}")
        return 1

    print(f"top-level keys    : {sorted(data.keys())}")
    print(f"msg              : {data.get('msg')}")
    print(f"total            : {data.get('total')}")

    flights = data.get("ac") or []
    est_bytes = len(body)
    print(f"ac array length  : {len(flights)}")
    print(f"\n~{est_bytes} bytes / {len(flights)} aircraft "
          f"= {est_bytes // max(len(flights), 1)} bytes each")
    print("(the Presto must hold the whole body + the parsed dict in RAM at once;")
    print(" if this is tens of KB, drop --radius until it fits.)\n")

    missing = 0
    print(f"{'callsign':10} {'lat':>10} {'lon':>11} {'alt_baro':>10}   x,y on screen")
    for ac in flights:
        callsign = (ac.get("flight") or ac.get("hex", "")).strip()
        lat = ac.get("lat")
        lon = ac.get("lon")
        alt = ac.get("alt_baro")
        if lat is None or lon is None:
            missing += 1
            print(f"{callsign:10} {'--':>10} {'--':>11} {str(alt):>10}   (no position)")
            continue
        x, y = lat_lon_to_xy(lat, lon, box)
        onscreen = "" if 0 <= x <= WIDTH and 0 <= y <= HEIGHT else "  (off-screen)"
        print(f"{callsign:10} {lat:>10.4f} {lon:>11.4f} {str(alt):>10}   {x},{y}{onscreen}")

    print(f"\n{len(flights)} aircraft, {missing} without a position fix")
    return 0


if __name__ == "__main__":
    sys.exit(main())
