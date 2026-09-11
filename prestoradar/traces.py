"""
Flight-trace seed: adsb.lol's tar1090 `trace_recent` for one aircraft, fetched
lazily on selection and cached briefly. Mirrors routes.py's request()/get()
shape -- never touched by the poll loop, only by UI.set_selected() on a tap.

See DATA_TRACE.md for the source decision and the on-device gzip check this is
built on. The short version:

  * request the bare host `adsb.lol` (globe.adsb.lol 302-redirects and net.py
    can't follow a Location header);
  * the body is always gzipped even with no Accept-Encoding sent, so it has to
    be inflated before json.loads;
  * `trace_recent` is the last ~5 min (~90 points); `trace_full` is ~24 h and
    would OOM the device.

Each `trace` entry is a 14-element list; `[1:4]` are lat, lon and alt (feet, or
the string "ground"), the base epoch is the file's `timestamp`. Only position
is kept here -- projected to the metric (e, n) frame and downsampled -- so the
renderer draws it exactly like the in-RAM fallback trail.
"""

import gc
import io

import geometry
import net
import settings
from netlog import log

try:
    from time import ticks_ms, ticks_diff          # MicroPython
except ImportError:                                 # CPython, for the dev harness
    from time import monotonic as _monotonic

    def ticks_ms():
        return int(_monotonic() * 1000)

    def ticks_diff(a, b):
        return a - b

# deflate is a MicroPython firmware module (confirmed present, see
# dev/trace_gzip_test.py); on the CPython dev harness fall back to zlib with
# gzip framing (wbits=31). Same both-runtimes pattern as netlog's ticks_ms.
try:
    import deflate

    def _inflate(body):
        return deflate.DeflateIO(io.BytesIO(body), deflate.GZIP).read()
except ImportError:
    import zlib

    def _inflate(body):
        return zlib.decompress(body, 31)

TRACE_HOST = "adsb.lol"          # direct -- NOT globe.adsb.lol, NOT api.adsb.lol

# hex (lowercase) -> [(e, n, alt), ...] oldest->newest   resolved, has a trace
#                    | None                               resolved: no trace / failed
#                    | ""                                 fetch in flight
#                    | absent (missing key)               never requested
_cache = {}
_fetched_ms = {}                 # hex -> ticks_ms of the last resolve, for the TTL

_TTL_MS = 90_000                 # a trace "barely changes over a few minutes"
_MAX_ENTRIES = 4                 # bound the cache (routes._cache's unbounded-dict caution)
_KEEP = 40                       # downsample target: a ~90-point trace_recent -> step 2
#                                  -> ~45 points, matching DATA_TRACE.md's "30-60" and
#                                  keeping the on-scope polyline to ~45 line() calls

# Size guards (DATA_TRACE.md step 3): abort back to the RAM trail rather than
# risk the allocator on an unexpectedly large body.
_MAX_COMPRESSED = 48 * 1024
_MAX_INFLATED = 64 * 1024


def _enabled():
    # settings.py is per-location and can lag settings_example.py, so default on.
    return bool(getattr(settings, "TRACE_SEED", 1))


def _store(h, value):
    if h not in _cache and len(_cache) >= _MAX_ENTRIES:
        oldest = min(_fetched_ms, key=_fetched_ms.get)
        _cache.pop(oldest, None)
        _fetched_ms.pop(oldest, None)
    _cache[h] = value
    _fetched_ms[h] = ticks_ms()


def get(hex_id):
    """Cached trace state for hex_id: a list of (e, n, alt) oldest->newest,
    None (looked up, no trace on file), "" (pending), or "absent" (never
    requested)."""
    return _cache.get((hex_id or "").lower(), "absent")


def points_for(plane):
    """Best available position history for `plane`, oldest->newest, or None.
    The network trace_recent seed when it resolved with data; otherwise the
    plane's own in-RAM trail that feed.py accumulates one fix per fetch
    (DATA_TRACE.md item 6: real history when it's there, own-accumulated
    trail when it isn't). Needs >= 2 points to be worth drawing."""
    seed = _cache.get((plane.hex or "").lower())
    if isinstance(seed, list) and len(seed) >= 2:
        return seed
    ram = getattr(plane, "trail", None)
    if ram and len(ram) >= 2:
        return ram
    return None


def request(p):
    """Kick off a trace_recent fetch for plane.Plane p unless one is already
    pending or was resolved within the TTL. No-op when tracing is disabled or
    the aircraft has no hex."""
    if not _enabled():
        return
    h = (p.hex or "").lower()
    if not h:
        return
    cached = _cache.get(h, "absent")
    if cached == "":
        return                                          # already in flight
    if cached != "absent" and ticks_diff(ticks_ms(), _fetched_ms.get(h, 0)) < _TTL_MS:
        return                                          # fresh hit, or fresh miss -- leave it
    _cache[h] = ""                                      # pending
    _fetched_ms[h] = ticks_ms()
    import asyncio
    asyncio.create_task(_fetch(h))


async def _fetch(h):
    gc.collect()
    path = "/data/traces/%s/trace_recent_%s.json" % (h[-2:], h)
    try:
        status, body = await net.http_get(TRACE_HOST, path, settings.USER_AGENT)
    except Exception as e:  # noqa: BLE001
        log("trace:", h, "request failed:", repr(e))
        _store(h, None)
        return

    if status != 200:
        # 404 = adsb.lol has no recent trace for this aircraft; anything else
        # is a transient. Either way fall back to the RAM trail.
        log("trace:", h, "HTTP", status, "-- falling back to the RAM trail")
        _store(h, None)
        return
    if len(body) > _MAX_COMPRESSED:
        log("trace:", h, "compressed body", len(body), "> ceiling; aborting")
        _store(h, None)
        return

    try:
        raw = _inflate(body)
    except Exception as e:  # noqa: BLE001
        log("trace:", h, "inflate failed:", repr(e))
        _store(h, None)
        return
    finally:
        body = None
        gc.collect()

    if len(raw) > _MAX_INFLATED:
        log("trace:", h, "inflated body", len(raw), "> ceiling; aborting")
        _store(h, None)
        return
    try:
        import json
        data = json.loads(raw)
    except ValueError as e:
        log("trace:", h, "bad JSON:", repr(e))
        _store(h, None)
        return
    finally:
        raw = None
        gc.collect()

    pts = _project(data)
    _store(h, pts or None)
    mem = gc.mem_free() if hasattr(gc, "mem_free") else "n/a"   # CPython has no mem_free
    log("trace:", h, "->", len(pts) if pts else 0, "points  mem", mem)


def _project(data):
    """tar1090 trace JSON -> [(e, n, alt), ...] oldest->newest, downsampled to
    ~_KEEP points. Each `trace` entry is a 14-element list; [1:4] are lat, lon
    and alt. See DATA_TRACE.md "Trace entry shape"."""
    entries = data.get("trace") if isinstance(data, dict) else None
    if not entries:
        return []
    step = max(1, len(entries) // _KEEP)
    out = []
    for e in entries[::step]:
        if not e or len(e) < 4:
            continue
        lat, lon, alt = e[1], e[2], e[3]
        if lat is None or lon is None:
            continue
        ee, nn = geometry.project(lat, lon)
        out.append((ee, nn, alt))
    return out
