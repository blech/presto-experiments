import asyncio
import json
import math

import geometry
import net
import settings
from netlog import log

ADSBLOL_HOST = "api.adsb.lol"
ADSBDB_HOST = "api.adsbdb.com"

EARTH_RADIUS_KM = 6371.0

# callsign -> (origin, dest) | None (unknown) | "" (pending) | absent (never requested)
_cache = {}


def is_hex_id(cs):
    return len(cs) == 6 and all(c in "0123456789ABCDEF" for c in cs.upper())


# --- great-circle plausibility check ----------------------------------
#
# Is the aircraft's current position/track consistent with being somewhere
# on the leg from airport A to airport B? Standard cross-track/along-track
# distance from the great-circle line (movable-type.co.uk/scripts/latlong),
# plus a track check position alone can't do: "just departed A" and
# "arriving at A" put the plane in the same place, only its heading tells
# them apart. Ported down from prestoradar/dev/route_check.py, the desktop
# tool this was prototyped in -- see that file's docstring for how this was
# arrived at, including a bug found in adsb.lol's own "plausible" flag
# (always true) that ruled out just trusting it as-is.

def _gc_distance_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (math.radians(x) for x in (lat1, lon1, lat2, lon2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _bearing_deg(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (math.radians(x) for x in (lat1, lon1, lat2, lon2))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return math.degrees(math.atan2(y, x)) % 360


def _angle_diff(a, b):
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def _plausible(pos_lat, pos_lon, track, a_lat, a_lon, b_lat, b_lon):
    dist_ab = _gc_distance_km(a_lat, a_lon, b_lat, b_lon)
    threshold = max(50 * 1.852, 0.20 * dist_ab)  # 50 nm, or 20% of the leg

    d13 = _gc_distance_km(a_lat, a_lon, pos_lat, pos_lon) / EARTH_RADIUS_KM
    theta13 = math.radians(_bearing_deg(a_lat, a_lon, pos_lat, pos_lon))
    theta12 = math.radians(_bearing_deg(a_lat, a_lon, b_lat, b_lon))
    xt = max(-1.0, min(1.0, math.sin(d13) * math.sin(theta13 - theta12)))
    cross_track = math.asin(xt) * EARTH_RADIUS_KM
    c = max(-1.0, min(1.0, math.cos(d13) / math.cos(cross_track / EARTH_RADIUS_KM)))
    along_track = math.acos(c) * EARTH_RADIUS_KM
    if _angle_diff(math.degrees(theta13), math.degrees(theta12)) > 90:
        along_track = -along_track

    on_the_line = abs(cross_track) <= threshold and -threshold <= along_track <= dist_ab + threshold
    if not on_the_line:
        return False
    if track is None:
        return True
    return _angle_diff(track, _bearing_deg(pos_lat, pos_lon, b_lat, b_lon)) <= 90


# --- route sources -------------------------------------------------------

async def _fetch_adsbdb(callsign):
    """One (code, lat, lon, code, lat, lon) leg, or None if adsbdb has
    nothing (or something without coordinates)."""
    status, body = await net.http_get(
        ADSBDB_HOST, "/v0/callsign/" + callsign, settings.USER_AGENT)
    if status != 200:
        return None
    resp = json.loads(body).get("response")
    fr = resp.get("flightroute") if isinstance(resp, dict) else None
    if not fr:
        return None
    o, d = fr.get("origin") or {}, fr.get("destination") or {}
    if o.get("latitude") is None or d.get("latitude") is None:
        return None
    a_code = o.get("iata_code") or o.get("icao_code") or "?"
    b_code = d.get("iata_code") or d.get("icao_code") or "?"
    return (a_code, o["latitude"], o["longitude"], b_code, d["latitude"], d["longitude"])


async def _fetch_adsblol_legs(callsign, lat, lon):
    """One leg tuple per leg of the plane's whole rotation, in order --
    unlike adsbdb, which only ever has one remembered leg. lat/lon only feed
    adsb.lol's own (unusable, see module docstring) `plausible` field; this
    ignores it and scores every leg itself."""
    status, body = await net.http_get(
        ADSBLOL_HOST, "/api/0/route/%s/%s/%s" % (callsign, lat, lon), settings.USER_AGENT)
    if status != 200:
        return []
    airports = json.loads(body).get("_airports") or []
    legs = []
    for a, b in zip(airports, airports[1:]):
        a_code = a.get("iata") or a.get("icao") or "?"
        b_code = b.get("iata") or b.get("icao") or "?"
        legs.append((a_code, a["lat"], a["lon"], b_code, b["lat"], b["lon"]))
    return legs


async def _fetch(callsign, lat, lon, track):
    """adsbdb first -- one request, same cost as before. Only escalate to
    adsb.lol's full-rotation lookup (a second request) if adsbdb has
    nothing, or what it has doesn't fit the plane's actual position and
    heading -- e.g. a multi-leg rotation where adsbdb only knows a
    different remembered leg."""
    route = None
    try:
        leg = await _fetch_adsbdb(callsign)
        if leg and _plausible(lat, lon, track, leg[1], leg[2], leg[4], leg[5]):
            route = (leg[0], leg[3])
        else:
            for leg in await _fetch_adsblol_legs(callsign, lat, lon):
                if _plausible(lat, lon, track, leg[1], leg[2], leg[4], leg[5]):
                    route = (leg[0], leg[3])
                    break
    except Exception as e:  # noqa: BLE001
        log("route lookup failed:", callsign, repr(e))
        route = None
    _cache[callsign] = route
    log("route", callsign, "->", route)


def request(p):
    """Kick off a route lookup for plane dict p if one isn't already cached
    or in flight. A no-op when the plane isn't broadcasting a callsign --
    feed.py falls back to the ICAO hex id in that case, so p["callsign"] ==
    p["hex"], and that's never a route to look up -- or when it's an empty
    string. (Can't just test is_hex_id(cs): a real callsign like ACA568 is
    six characters that all happen to be hex digits.) Callers can pass a
    plane straight through even before its callsign is known to be real."""
    cs = (p["callsign"] or "").strip()
    if cs and cs.lower() != (p.get("hex") or "").lower() and cs not in _cache:
        _cache[cs] = ""            # pending
        lat, lon = geometry.unproject(p["e"], p["n"])
        asyncio.create_task(_fetch(cs, lat, lon, p.get("heading")))


def get(callsign):
    """Current cached state for callsign: an (origin, dest) tuple, None
    (looked up, unknown), "" (pending), or "absent" (never requested)."""
    return _cache.get(callsign, "absent")
