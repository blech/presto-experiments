import asyncio
import json

import net
import settings
from netlog import log

# callsign -> (origin, dest) | None (unknown) | "" (pending) | absent (never requested)
_cache = {}


def is_hex_id(cs):
    return len(cs) == 6 and all(c in "0123456789abcdefABCDEF" for c in cs)


async def _fetch(callsign):
    try:
        status, body = await net.http_get(
            "api.adsbdb.com", "/v0/callsign/" + callsign, settings.USER_AGENT)
        route = None
        if status == 200:
            resp = json.loads(body).get("response")
            fr = resp.get("flightroute") if isinstance(resp, dict) else None
            if fr:
                o = (fr.get("origin") or {})
                d = (fr.get("destination") or {})
                route = (o.get("iata_code") or o.get("icao_code") or "?",
                         d.get("iata_code") or d.get("icao_code") or "?")
        _cache[callsign] = route
        log("route", callsign, "->", route)
    except Exception as e:  # noqa: BLE001
        log("route lookup failed:", callsign, repr(e))
        _cache[callsign] = None


def request(callsign):
    """Kick off a route lookup for callsign if one isn't already cached or in
    flight. A no-op for a bare ICAO hex id (never a real callsign broadcast
    over ADS-B) or an empty string -- callers can pass a plane's raw,
    unstripped `callsign` field straight through."""
    cs = (callsign or "").strip()
    if cs and not is_hex_id(cs) and cs not in _cache:
        _cache[cs] = ""            # pending
        asyncio.create_task(_fetch(cs))


def get(callsign):
    """Current cached state for callsign: an (origin, dest) tuple, None
    (looked up, unknown), "" (pending), or "absent" (never requested)."""
    return _cache.get(callsign, "absent")
