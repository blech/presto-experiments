#!/usr/bin/env python3
"""
Desktop (CPython) tool that cross-checks an aircraft's route across three
free sources, instead of trusting whichever one answers first.

route_lookup.py -- the simple version, matching what routes.py does on the
Presto -- only queries adsbdb. That's fine for the tap-to-inspect panel, but
adsbdb (and hexdb.io) just return whatever route happens to be on file for a
callsign, and that can be stale or wrong for a multi-leg rotation. Case in
point, callsign UAL2274 climbing out of SFO on 2026-09-05:

    adsbdb        -> KEWR -> KRSW   (2100 nm off to one side, wrong)
    hexdb.io      -> KIAD -> KSFO   (a 2019 arrival leg, also wrong)
    adsb.lol      -> KSAN -> KSFO -> KDEN  (the actual rotation)

adsb.lol's route endpoint (VRS standing-data) is the only one of the three
that carries the plane's *whole* rotation, not just one remembered leg, so
it's the one worth cross-checking others against. Its own "plausible" field
can't be used for that, though: `adsb_api/utils/api_routes.py`'s
calc_plausible() does `if await plausible(...)` where plausible() always
returns a 2-tuple -- truthy in Python no matter what's inside -- so the
field is always True once a route has 2+ known airports (confirmed live:
https://api.adsb.lol/api/0/route/UAL2274/-33.87/151.21, Sydney, still comes
back "plausible": true for the SFO rotation). This script fetches each
source's raw airport coordinates and scores every candidate leg itself:

  - cross-track distance from the aircraft's position to the leg's
    great-circle line must be within 50 nm or 20% of the leg's length,
    whichever is bigger (adsb.lol's intended threshold, from the same
    file, just correctly applied), and it must sit between the two
    airports (not far off either end).
  - the aircraft's current track must point roughly (within 90 degrees)
    at the candidate destination -- this is what actually separates "just
    left SFO for DEN" from "arriving at SFO from IAD": both put the plane
    near SFO, but only one has it heading away from SFO in the right
    direction.

Same CLI as route_lookup.py:

    python3 prestoradar/dev/route_check.py --callsign UAL2274
    python3 prestoradar/dev/route_check.py --hex aa3ae4
    python3 prestoradar/dev/route_check.py --hex aa3ae4 --radius 100

Position lookup tries adsb.lol's network-wide /v2/hex or /v2/callsign first
-- the aircraft doesn't need to be anywhere near your radar's centre for
that to work -- and only falls back to a local point+radius search around
settings.py's centre (--radius, same meaning as route_lookup.py's) if the
global index doesn't have it.
"""

import argparse
import asyncio
import json
import os
import sys
from math import acos, asin, atan2, cos, degrees, radians, sin, sqrt

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import net  # noqa: E402
import routes  # noqa: E402 -- just for is_hex_id()
from settings import CENTER_LAT, CENTER_LON, RADIUS_KM, USER_AGENT  # noqa: E402

ADSBLOL_HOST = "api.adsb.lol"
ADSBDB_HOST = "api.adsbdb.com"
HEXDB_HOST = "hexdb.io"

EARTH_RADIUS_KM = 6371.0


# --- great-circle geometry -------------------------------------------------

def _gc_distance_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (radians(x) for x in (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def _bearing_deg(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (radians(x) for x in (lat1, lon1, lat2, lon2))
    y = sin(lon2 - lon1) * cos(lat2)
    x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(lon2 - lon1)
    return degrees(atan2(y, x)) % 360


def _angle_diff(a, b):
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def score_leg(pos_lat, pos_lon, track, a_lat, a_lon, b_lat, b_lon):
    """How well does (pos_lat, pos_lon), heading `track` (degrees, or None),
    fit as being somewhere on the great-circle leg from A to B? Standard
    cross-track/along-track formulae (movable-type.co.uk/scripts/latlong),
    plus a track check A-B distance alone can't do -- see module docstring.
    Returns a dict of the raw numbers plus `ok`.
    """
    dist_ab = _gc_distance_km(a_lat, a_lon, b_lat, b_lon)
    threshold = max(50 * 1.852, 0.20 * dist_ab)  # 50 nm, or 20% of the leg

    d13 = _gc_distance_km(a_lat, a_lon, pos_lat, pos_lon) / EARTH_RADIUS_KM
    theta13 = radians(_bearing_deg(a_lat, a_lon, pos_lat, pos_lon))
    theta12 = radians(_bearing_deg(a_lat, a_lon, b_lat, b_lon))
    xt = max(-1.0, min(1.0, sin(d13) * sin(theta13 - theta12)))
    cross_track = asin(xt) * EARTH_RADIUS_KM
    c = max(-1.0, min(1.0, cos(d13) / cos(cross_track / EARTH_RADIUS_KM)))
    along_track = acos(c) * EARTH_RADIUS_KM
    if _angle_diff(degrees(theta13), degrees(theta12)) > 90:
        along_track = -along_track

    on_the_line = (abs(cross_track) <= threshold
                   and -threshold <= along_track <= dist_ab + threshold)

    track_diff = None
    if track is not None:
        track_diff = _angle_diff(track, _bearing_deg(pos_lat, pos_lon, b_lat, b_lon))

    ok = on_the_line and (track_diff is None or track_diff <= 90)
    return {"dist_ab_km": dist_ab, "cross_track_km": cross_track,
            "along_track_km": along_track, "track_diff_deg": track_diff, "ok": ok}


# --- position lookup: adsb.lol's network-wide index first (works anywhere,
# regardless of settings.py's location), falling back to a local point+radius
# search around settings.py's centre -- the kind route_lookup.py and
# list_aircraft.py do -- in case the global index is lagging or missing it.

async def _get_json(host, path):
    """GET and parse a JSON object, or None on any failure -- unreachable
    host, non-200, an unparseable body, or a bare `null` (adsb.lol's
    airport endpoint has been seen to answer 200 with `null` when its
    backend is unhappy). Keeps one flaky source from aborting the whole
    cross-check with a traceback."""
    try:
        status, body = await net.http_get(host, path, USER_AGENT)
        if status != 200:
            return None
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


async def _fetch_ac_list(path):
    data = await _get_json(ADSBLOL_HOST, path)
    if data is None:
        return None, f"adsb.lol returned no usable data for {path}"
    return data.get("ac") or [], None


def _ac_to_plane(ac):
    lat, lon = ac.get("lat"), ac.get("lon")
    if lat is None or lon is None:
        return None
    return {
        "hex": ac.get("hex", ""),
        "callsign": (ac.get("flight") or "").strip(),
        "lat": lat, "lon": lon,
        "track": ac.get("track", ac.get("true_heading")),
        "alt": ac.get("alt_baro"),
    }


def _find_in(ac_list, hex_id, callsign):
    for ac in ac_list:
        h = (ac.get("hex") or "").lower()
        cs = (ac.get("flight") or "").strip().upper()
        if (hex_id and h == hex_id.lower()) or (callsign and cs == callsign.upper()):
            return ac
    return None


async def find_aircraft(hex_id=None, callsign=None, radius_km=RADIUS_KM):
    global_path = f"/v2/hex/{hex_id}" if hex_id else f"/v2/callsign/{callsign}"
    ac_list, err = await _fetch_ac_list(global_path)
    if err:
        return None, err

    # Validate the global-index hit rather than trusting ac_list[0]:
    # /v2/callsign/<cs> can match on adsb.lol's own standing data and hand
    # back an aircraft whose live `flight` is blank (or, with callsign
    # reuse, more than one aircraft), and analysing the wrong plane's
    # position yields a silently bogus plausibility verdict.
    ac = _find_in(ac_list, hex_id, callsign)
    if ac is None:
        radius_nm = round(radius_km / 1.852)
        local_path = f"/v2/point/{CENTER_LAT}/{CENTER_LON}/{radius_nm}"
        ac_list, err = await _fetch_ac_list(local_path)
        if err:
            return None, err
        ac = _find_in(ac_list, hex_id, callsign)

    if ac is None:
        who = hex_id or callsign
        return None, (f"{who} not found on adsb.lol's global index, and not in the "
                       f"local {radius_km:g} km feed around ({CENTER_LAT}, {CENTER_LON}) either")

    plane = _ac_to_plane(ac)
    if plane is None:
        return None, "visible but has no position fix right now"
    return plane, None


# --- the three route sources -----------------------------------------------

async def fetch_adsbdb(callsign):
    """[(a_code, a_lat, a_lon, b_code, b_lat, b_lon)], or [] if adsbdb has
    nothing on file."""
    data = await _get_json(ADSBDB_HOST, "/v0/callsign/" + callsign)
    resp = (data or {}).get("response")
    fr = resp.get("flightroute") if isinstance(resp, dict) else None
    if not fr:
        return []
    o, d = fr.get("origin") or {}, fr.get("destination") or {}
    if o.get("latitude") is None or d.get("latitude") is None:
        return []
    a_code = o.get("icao_code") or o.get("iata_code") or "?"
    b_code = d.get("icao_code") or d.get("iata_code") or "?"
    return [(a_code, o["latitude"], o["longitude"], b_code, d["latitude"], d["longitude"])]


async def _airport_latlon(icao):
    data = await _get_json(ADSBLOL_HOST, f"/api/0/airport/{icao}")
    if not data or data.get("lat") is None:
        return None
    return data["lat"], data["lon"]


async def fetch_hexdb(callsign):
    """Same shape as fetch_adsbdb(). hexdb.io's route string has no
    coordinates, so this makes two follow-up calls to adsb.lol's static
    airport lookup (independent of the plausibility question -- it's just a
    name/lat/lon table, not adsb.lol's own route guess)."""
    data = await _get_json(HEXDB_HOST, "/api/v1/route/icao/" + callsign)
    route = (data or {}).get("route") or ""
    if "-" not in route:
        return []
    a_code, b_code = route.split("-", 1)
    a_pos, b_pos = await asyncio.gather(_airport_latlon(a_code), _airport_latlon(b_code))
    if not a_pos or not b_pos:
        return []
    return [(a_code, a_pos[0], a_pos[1], b_code, b_pos[0], b_pos[1])]


async def fetch_adsblol_route(callsign, lat, lon):
    """One tuple per leg of the plane's whole rotation, in order. lat/lon
    only feed adsb.lol's own (unreliable, see module docstring) `plausible`
    field, which this ignores -- passed through anyway, as the real
    position, so as not to seed their route cache with a bogus one."""
    data = await _get_json(ADSBLOL_HOST, f"/api/0/route/{callsign}/{lat}/{lon}")
    airports = (data or {}).get("_airports") or []
    legs = []
    for a, b in zip(airports, airports[1:]):
        if a.get("lat") is None or b.get("lat") is None:
            continue
        legs.append((a.get("icao") or "?", a["lat"], a["lon"],
                     b.get("icao") or "?", b["lat"], b["lon"]))
    return legs


# --- report ------------------------------------------------------------

async def run(args):
    plane, err = await find_aircraft(
        hex_id=args.hex, callsign=args.callsign.strip() if args.callsign else None,
        radius_km=args.radius)
    if err:
        print(err)
        return 1

    callsign = plane["callsign"]
    if not callsign or routes.is_hex_id(callsign):
        print(f"hex {plane['hex']} is in the air but isn't broadcasting a usable callsign right now")
        return 1

    track_str = f"{plane['track']:.0f}°" if plane["track"] is not None else "unknown"
    print(f"{callsign} (hex {plane['hex']}) at {plane['lat']:.4f}, {plane['lon']:.4f}, "
          f"track {track_str}, alt {plane['alt']}\n")

    # return_exceptions=True: the fetchers above are hardened not to raise,
    # but a surprise in one source still shouldn't sink the other two.
    sources = await asyncio.gather(
        fetch_adsbdb(callsign), fetch_hexdb(callsign),
        fetch_adsblol_route(callsign, plane["lat"], plane["lon"]),
        return_exceptions=True)
    names = ("adsbdb", "hexdb.io", "adsb.lol")

    rows = []  # (source, a_code, b_code, score, leg_label)
    for name, legs in zip(names, sources):
        if isinstance(legs, Exception):
            rows.append((name, f"(lookup errored: {legs!r})", None, None, None))
            continue
        if not legs:
            rows.append((name, None, None, None, None))
            continue
        for i, (a_code, a_lat, a_lon, b_code, b_lat, b_lon) in enumerate(legs):
            score = score_leg(plane["lat"], plane["lon"], plane["track"],
                               a_lat, a_lon, b_lat, b_lon)
            label = f"leg {i + 1}/{len(legs)}" if len(legs) > 1 else ""
            rows.append((name, a_code, b_code, score, label))

    print(f"{'source':9} {'route':17} {'plausible':9} {'cross-track':>11} "
          f"{'track diff':>10}  leg")
    for name, a_code, b_code, score, label in rows:
        if score is None:
            print(f"{name:9} {a_code or '(no route on file)':17}")
            continue
        route_str = f"{a_code} -> {b_code}"
        td = f"{score['track_diff_deg']:.0f}°" if score["track_diff_deg"] is not None else "--"
        print(f"{name:9} {route_str:17} {'yes' if score['ok'] else 'no':9} "
              f"{score['cross_track_km']:9.0f}km {td:>10}  {label}")

    plausible = [(name, a_code, b_code) for name, a_code, b_code, score, _ in rows
                 if score is not None and score["ok"]]
    print()
    if plausible:
        for name, a_code, b_code in plausible:
            print(f"plausible: {a_code} -> {b_code}  ({name})")
    else:
        print("no candidate passed the plausibility check -- treat all of the above as unconfirmed")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--hex", metavar="ID",
                    help="ICAO24 hex id (aka 'hex'/icao24), e.g. aa3ae4")
    g.add_argument("--callsign", metavar="CS",
                    help="flight callsign (aka 'flight'/ident), e.g. UAL2274")
    ap.add_argument("--radius", type=float, default=RADIUS_KM,
                    help="km to search locally around settings.py's centre, if "
                         "adsb.lol's global index doesn't have --hex/--callsign "
                         "(default: settings.RADIUS_KM)")
    args = ap.parse_args()

    if args.hex and not routes.is_hex_id(args.hex):
        ap.error(f"--hex expects a 6-character ICAO24 id, got {args.hex!r}")

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
