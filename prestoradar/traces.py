"""
Flight-trace backfill: adsb.lol's tar1090 `trace_recent` for one aircraft,
fetched once per sighting via radar.py's fetch queue (fetchqueue.py) and
spliced into plane.trail. After that, Plane._record_fix() keeps the same
list growing for free from the position poll -- see
docs/superpowers/specs/2026-09-18-trace-fetch-queue-design.md for the full
design and why this replaced the earlier per-tap, TTL-cached fetch.

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
renderer draws it exactly like the live-accumulated points that follow it.
"""

import gc
import io

import geometry
import net
import settings
from netlog import log

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


def points_for(plane):
    """Best available position history for `plane`, oldest->newest, or None.
    plane.trail is the one trail structure now -- backfilled once by
    backfill() below, then grown for free every poll cycle by
    Plane._record_fix(). Needs >= 2 points to be worth drawing."""
    trail = plane.trail
    return trail if trail and len(trail) >= 2 else None


def _apply(plane, pts):
    """Splice a resolved backfill result into plane.trail: a wholesale
    replace, not a merge with whatever live fixes had accumulated so far --
    the backfill covers that same recent window at higher resolution, so
    replacing is always at least as good and avoids reconciling two
    differently-sampled point lists. Leaves plane.traced == True either
    way, so backfill() is never retried for this aircraft."""
    if pts and len(pts) >= 2:
        plane.trail = pts
    plane.traced = True


async def backfill(plane):
    """Fetch plane's trace_recent backfill and splice it into plane.trail
    via _apply(). Called once per aircraft sighting, by radar.py's fetch
    queue -- never touched by the poll loop or a tap directly."""
    if not _enabled():
        _apply(plane, None)
        return
    h = (plane.hex or "").lower()
    if not h:
        _apply(plane, None)
        return

    gc.collect()
    path = "/data/traces/%s/trace_recent_%s.json" % (h[-2:], h)
    try:
        status, body = await net.http_get(TRACE_HOST, path, settings.USER_AGENT)
    except Exception as e:  # noqa: BLE001
        log("trace:", h, "request failed:", repr(e))
        _apply(plane, None)
        return

    if status != 200:
        # 404 = adsb.lol has no recent trace for this aircraft; anything else
        # is a transient. Either way fall back to the RAM trail.
        log("trace:", h, "HTTP", status, "-- falling back to the RAM trail")
        _apply(plane, None)
        return
    if len(body) > _MAX_COMPRESSED:
        log("trace:", h, "compressed body", len(body), "> ceiling; aborting")
        _apply(plane, None)
        return

    try:
        raw = _inflate(body)
    except Exception as e:  # noqa: BLE001
        log("trace:", h, "inflate failed:", repr(e))
        _apply(plane, None)
        return
    finally:
        body = None
        gc.collect()

    if len(raw) > _MAX_INFLATED:
        log("trace:", h, "inflated body", len(raw), "> ceiling; aborting")
        _apply(plane, None)
        return
    try:
        import json
        data = json.loads(raw)
    except ValueError as e:
        log("trace:", h, "bad JSON:", repr(e))
        _apply(plane, None)
        return
    finally:
        raw = None
        gc.collect()

    pts = _project(data)
    _apply(plane, pts)
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
