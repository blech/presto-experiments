"""
On-device gzip / trace-fetch probe -- the hard gate for DATA_TRACE.md.

Run it on the Presto (it needs the device's MicroPython firmware, not CPython):

    ../venv/bin/mpremote run prestoradar/dev/trace_gzip_test.py

It answers, in order, the questions DATA_TRACE.md says to settle before any
trace feature is built:

  1. Does `import deflate` exist in this firmware?  (Hard gate -- if not, the
     network trace seed is impossible and only the in-RAM trail is left.)
  2. Does requesting `adsb.lol` directly return a 200, not the 302 that
     `globe.adsb.lol` issues (which net.py can't follow)?
  3. Is the body really always gzipped with no Accept-Encoding sent, so a
     plain `json.loads(body)` fails on the raw bytes?
  4. Does `deflate.DeflateIO(io.BytesIO(body), deflate.GZIP).read()` inflate
     it, and does the inflated JSON parse to the 14-element trace entries the
     doc describes?
  5. How much RAM does the fetch + inflate transiently cost (compressed and
     inflated body both live briefly), on top of a bare connection?

By default it resolves a live aircraft to test against: it fetches the normal
`/v2/point` feed for settings.py's centre/radius and picks the nearest
airborne contact that has a hex. Set HEX below to pin a specific one.

This imports only `net` and `settings` from /prestoradar (both small and
stable) plus the standard library, so it doesn't depend on the currently
deployed feed.py / plane.py being in step with the working tree.
"""

import gc
import io
import sys
import time

if "/prestoradar" not in sys.path:
    sys.path.insert(0, "/prestoradar")

import network

import net
import settings

# Pin a specific ICAO hex to test (lower or upper case), or "" to resolve the
# nearest airborne aircraft from the live feed.
HEX = ""

TRACE_HOST = "adsb.lol"        # NOT globe.adsb.lol (302) and NOT api.adsb.lol
FEED_HOST = "api.adsb.lol"

# Size ceilings from DATA_TRACE.md's "guard the size" step, for context only --
# this probe reports against them, it doesn't enforce them.
MAX_COMPRESSED = 48 * 1024
MAX_INFLATED = 64 * 1024


def log(*parts):
    print("[{:8.3f}] {}".format(time.ticks_ms() / 1000,
                                " ".join(str(p) for p in parts)))


def mem():
    gc.collect()
    return gc.mem_free()


def connect_wifi():
    from secrets import WIFI_SSID, WIFI_PASSWORD
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        log("wifi: connecting to", WIFI_SSID)
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 30000:
                raise OSError("wifi connect timed out")
            time.sleep(0.5)
    log("wifi: up", wlan.ifconfig()[0])


async def resolve_hex():
    """Nearest airborne aircraft with a hex in the current feed, or None."""
    radius_nm = round(settings.RADIUS_KM / 1.852)
    path = "/v2/point/%s/%s/%s" % (settings.CENTER_LAT, settings.CENTER_LON, radius_nm)
    log("feed: GET https://%s%s" % (FEED_HOST, path))
    status, body = await net.http_get(FEED_HOST, path, settings.USER_AGENT)
    log("feed: HTTP", status, len(body), "bytes")
    if status != 200:
        return None
    import json
    data = json.loads(body)
    best = None
    best_dst = 1e9
    for ac in data.get("ac", []) or []:
        h = ac.get("hex")
        if not h:
            continue
        if ac.get("alt_baro") in (None, "ground"):
            continue
        dst = ac.get("dst")
        dst = 1e9 if dst is None else dst
        if dst < best_dst:
            best, best_dst = ac, dst
    if best is None:
        return None
    log("feed: nearest airborne =", best.get("hex"),
        "(%s)" % (best.get("flight") or "?").strip(),
        "%.0f nm" % best_dst, best.get("alt_baro"), "ft")
    return best.get("hex")


def check_deflate():
    log("-" * 60)
    try:
        import deflate  # noqa: F401
        log("GATE 1  import deflate: PASS")
        return True
    except ImportError as e:
        log("GATE 1  import deflate: FAIL --", repr(e))
        log("        network trace seed is impossible on this firmware;")
        log("        the in-RAM accumulated trail is the only option.")
        return False


async def fetch_trace(hex_id):
    hex_lc = hex_id.lower()
    path = "/data/traces/%s/trace_recent_%s.json" % (hex_lc[-2:], hex_lc)
    log("-" * 60)
    log("GATE 2  GET https://%s%s" % (TRACE_HOST, path))

    m0 = mem()
    t0 = time.ticks_ms()
    status, body = await net.http_get(TRACE_HOST, path, settings.USER_AGENT)
    dt = time.ticks_diff(time.ticks_ms(), t0)
    m1 = mem()

    log("GATE 2  HTTP", status, "in", dt, "ms  (302 would mean the redirect host)")
    if status == 302:
        log("GATE 2  FAIL -- got the redirect; check the host is 'adsb.lol'")
        return None
    if status == 404:
        log("GATE 2  404 -- adsb.lol has no recent trace for this aircraft.")
        log("        Not a bug: this is the case the RAM-trail fallback covers.")
        return None
    if status != 200:
        log("GATE 2  unexpected status; body[:200] =", body[:200])
        return None

    log("compressed body:", len(body), "bytes",
        "(<= %d ceiling: %s)" % (MAX_COMPRESSED, "yes" if len(body) <= MAX_COMPRESSED else "NO"))
    log("first 4 bytes:", " ".join("%02x" % b for b in body[:4]),
        "(gzip magic is 1f 8b)")
    log("mem: %d -> %d  (fetch cost ~%d bytes held)" % (m0, m1, m0 - m1))

    log("-" * 60)
    log("GATE 3  json.loads() on the RAW gzip bytes (expected to fail):")
    import json
    try:
        json.loads(body)
        log("GATE 3  ... it PARSED -- body was not gzipped after all?")
    except Exception as e:  # noqa: BLE001
        log("GATE 3  raised", repr(e), "-- confirms the body needs inflating")

    log("-" * 60)
    log("GATE 4  inflate with deflate.DeflateIO(..., GZIP):")
    import deflate
    m2 = mem()
    t0 = time.ticks_ms()
    raw = deflate.DeflateIO(io.BytesIO(body), deflate.GZIP).read()
    dt = time.ticks_diff(time.ticks_ms(), t0)
    m3 = mem()
    body = None
    gc.collect()
    log("inflated:", len(raw), "bytes in", dt, "ms",
        "(<= %d ceiling: %s)" % (MAX_INFLATED, "yes" if len(raw) <= MAX_INFLATED else "NO"))
    log("mem: before inflate %d, after %d  (peak transient ~%d bytes: both bodies live)"
        % (m2, m3, m2 - m3))

    t0 = time.ticks_ms()
    data = json.loads(raw)
    dt = time.ticks_diff(time.ticks_ms(), t0)
    raw = None
    gc.collect()
    log("json.loads(inflated):", dt, "ms  mem now", mem())

    return data


def inspect_trace(data):
    log("-" * 60)
    log("GATE 4  trace structure:")
    if not isinstance(data, dict):
        log("        top-level is %s, expected dict" % type(data).__name__)
        return
    base = data.get("timestamp")
    trace = data.get("trace") or []
    log("        keys:", sorted(data.keys()))
    log("        icao:", data.get("icao"), " r:", data.get("r"), " type:", data.get("t"))
    log("        timestamp (epoch base):", base)
    log("        trace points:", len(trace))
    if not trace:
        return
    first, last = trace[0], trace[-1]
    log("        entry length:", len(first), "(doc says 14)")
    log("        first entry:", first[:8])
    log("        [0:6] = (dt_s, lat, lon, alt, gs, track) =", tuple(first[:6]))
    span = last[0] - first[0]
    log("        time span: %.1f s (%.1f min) across %d points -> ~%.1f s cadence"
        % (span, span / 60.0, len(trace), span / max(len(trace) - 1, 1)))
    # DATA_TRACE.md step 4: keep ~30-60 points, so a scope trail downsamples
    # roughly every Nth. Show what that would look like here.
    keep = 45
    step = max(1, len(trace) // keep)
    log("        downsample every %d -> %d points kept" % (step, len(trace[::step])))
    grounds = sum(1 for e in trace if e[3] == "ground")
    log("        entries with alt == \"ground\":", grounds)


async def run():
    connect_wifi()
    log("baseline mem after wifi:", mem())

    have_deflate = check_deflate()

    hex_id = HEX or await resolve_hex()
    if not hex_id:
        log("no aircraft to test against -- set HEX at the top of this file")
        return
    log("testing hex:", hex_id)

    if not have_deflate:
        log("skipping the fetch/inflate gates -- no deflate module")
        return

    data = await fetch_trace(hex_id)
    if data is not None:
        inspect_trace(data)

    log("-" * 60)
    log("final mem:", mem())
    log("done")


import asyncio  # noqa: E402  -- after the module docstring / constants, matching net.py

asyncio.run(run())
