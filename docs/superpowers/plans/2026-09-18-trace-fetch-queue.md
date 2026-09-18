# Trace Fetch Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every aircraft currently on screen gets its flight-history trail
backfilled from adsb.lol exactly once (via a shared, paced fetch queue),
then grows that same trail for free from the existing position poll — no
more per-tap fetches, no more periodic re-fetch on a TTL.

**Architecture:** A new generic `fetchqueue.Queue` drains one
`(priority, key)` entry at a time, paced under adsb.lol's shared courtesy
budget. `ui.py` enqueues every newly-tracked, airborne aircraft (priority =
distance from centre); `radar.py` resolves each dequeued hex against
`feed.py`'s live registry and, if still current, hands the `Plane` to
`traces.backfill()`, which fetches/parses/downsamples `trace_recent` and
splices it into `plane.trail` (wholesale replace, not merge). From then on,
`Plane._record_fix()` (unchanged) keeps that same list growing every poll
cycle at zero extra network cost.

**Tech Stack:** MicroPython (device) / CPython (desktop dev harness, no
pytest — this repo's own hand-rolled `dev/test_*.py` convention), `asyncio`,
adsb.lol's `trace_recent` HTTP+gzip+JSON endpoint.

**Spec:** `docs/superpowers/specs/2026-09-18-trace-fetch-queue-design.md`

## Global Constraints

- Pacing: **one request every 1500ms** (`TRACE_QUEUE_INTERVAL_MS = 1500`),
  a new constant in both `settings_example.py` and the local, gitignored
  `settings.py`.
- Priority: **`plane.dst` ascending** (nearest-to-centre drains first) —
  captured once at enqueue time, never re-evaluated.
- Skip heuristic: an aircraft is only enqueued when `plane.traced is False`
  **and** `not plane.on_ground` — a grounded aircraft is left un-enqueued
  and re-checked next cycle, no queue entry, no network cost.
- Splice strategy: a resolved backfill **replaces `plane.trail` wholesale**
  (never merges with whatever live fixes had accumulated so far).
- Error handling: any failure (network error, non-200, oversized body, bad
  JSON, or a too-short result) still sets `plane.traced = True` — **no
  retries, ever**, for a given sighting.
- The queue holds **hex strings, never `Plane` references** — a queued
  aircraft is resolved against `feed.py`'s *current* registry only at
  dequeue time (`Feed.resolve(hex_id)`), so a departed aircraft's entry
  silently no-ops instead of firing a wasted fetch or keeping a stale
  object alive.
- Candidate scope: **every** aircraft in the fresh feed list that passes the
  skip heuristic — no top-N cap.

---

## Task 1: `fetchqueue.py` — generic paced priority queue

**Files:**
- Create: `prestoradar/fetchqueue.py`
- Test: `prestoradar/dev/test_fetchqueue.py`

**Interfaces:**
- Produces: `fetchqueue.Queue(interval_ms)` with `.enqueue(key, priority)`
  and `async def run(self, process_one)`. `process_one` is an
  async-callable taking one `key` argument. `Queue` knows nothing about
  aircraft, hexes, or traces — `key`/`priority` are opaque to it.

- [ ] **Step 1: Write the failing test**

Create `prestoradar/dev/test_fetchqueue.py`:

```python
#!/usr/bin/env python3
"""Desktop test for fetchqueue.Queue -- priority ordering and draining.
No device, no asyncio event loop needed: only the pure enqueue/_pop pair is
exercised here. run() itself is thin async glue around _pop(), verified
on-device via radar_listen.py, the same way feed.py's run() is never
unit-tested directly -- only the pure logic behind it."""

import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _eq(got, want, what):
    if got != want:
        raise AssertionError("%s: got %r, want %r" % (what, got, want))


def test_empty_pop_is_none():
    from fetchqueue import Queue
    q = Queue(interval_ms=1500)
    _eq(q._pop(), None, "an empty queue pops None")


def test_lowest_priority_first():
    from fetchqueue import Queue
    q = Queue(interval_ms=1500)
    q.enqueue("far", 40.0)
    q.enqueue("near", 5.0)
    q.enqueue("mid", 20.0)
    _eq(q._pop(), "near", "lowest priority pops first")
    _eq(q._pop(), "mid", "then the next-lowest")
    _eq(q._pop(), "far", "then the highest")
    _eq(q._pop(), None, "drained -> None")


def test_ties_preserve_insertion_order():
    from fetchqueue import Queue
    q = Queue(interval_ms=1500)
    q.enqueue("first", 10.0)
    q.enqueue("second", 10.0)
    _eq(q._pop(), "first", "equal priority: first enqueued pops first")
    _eq(q._pop(), "second", "then the second")


def test_enqueue_after_partial_drain():
    from fetchqueue import Queue
    q = Queue(interval_ms=1500)
    q.enqueue("a", 5.0)
    q.enqueue("b", 1.0)
    _eq(q._pop(), "b", "lowest first")
    q.enqueue("c", 0.5)
    _eq(q._pop(), "c", "a later, lower-priority enqueue still jumps ahead")
    _eq(q._pop(), "a", "then whatever was left")


def main():
    test_empty_pop_is_none()
    test_lowest_priority_first()
    test_ties_preserve_insertion_order()
    test_enqueue_after_partial_drain()
    print("fetchqueue.Queue: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 prestoradar/dev/test_fetchqueue.py`
Expected: `ModuleNotFoundError: No module named 'fetchqueue'`

- [ ] **Step 3: Implement `fetchqueue.py`**

Create `prestoradar/fetchqueue.py`:

```python
import asyncio


class Queue:
    """Generic priority-ordered, single-concurrency, paced work queue.
    Knows nothing about aircraft, hexes, or traces -- just drains
    (priority, key) pairs at a fixed interval, calling a caller-supplied
    async callback per key. One instance is meant to be shared across every
    feature that needs to stay under a remote service's request budget
    (traces.py's backfill now; a future list mode's route-fetching later --
    see docs/superpowers/specs/2026-09-18-trace-fetch-queue-design.md)."""

    def __init__(self, interval_ms):
        self._interval_ms = interval_ms
        self._pending = []          # [(priority, key), ...]

    def enqueue(self, key, priority):
        self._pending.append((priority, key))

    def _pop(self):
        """Remove and return the lowest-priority key, or None if empty.
        A plain list with a sort-then-pop is deliberate: expected depth is
        a few dozen entries at most, where an O(n log n) sort per drain
        tick is trivial -- no heap needed. Ties keep insertion order
        (list.sort() is stable)."""
        if not self._pending:
            return None
        self._pending.sort(key=lambda entry: entry[0])
        return self._pending.pop(0)[1]

    async def run(self, process_one):
        """Drain one entry per `interval_ms`, calling `process_one(key)`.
        Runs forever -- start as its own asyncio task."""
        while True:
            key = self._pop()
            if key is not None:
                await process_one(key)
            await asyncio.sleep(self._interval_ms / 1000.0)
```

- [ ] **Step 4: Run the test again and confirm it passes**

Run: `python3 prestoradar/dev/test_fetchqueue.py`
Expected: `fetchqueue.Queue: all assertions passed`

- [ ] **Step 5: Commit**

```bash
git add prestoradar/fetchqueue.py prestoradar/dev/test_fetchqueue.py
git commit -m "prestoradar: add fetchqueue.Queue, a generic paced priority queue"
```

---

## Task 2: `plane.py` — a `traced` lifecycle slot

**Files:**
- Modify: `prestoradar/plane.py`
- Test: `prestoradar/dev/test_plane.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Plane.traced` — `False` on a fresh sighting (set alongside the
  existing `p.trail = []`), left untouched on every subsequent `from_feed(...,
  into=p)` call. Later tasks set it to `"pending"` (enqueued) or `True`
  (backfill attempted, any outcome).

- [ ] **Step 1: Write the failing test**

In `prestoradar/dev/test_plane.py`, find this existing block (around line
130):

```python
    ac0 = {"hex": "abc123", "flight": "TEST1", "lat": 52.0, "lon": -0.2,
           "alt_baro": 10000, "gs": 300.0, "track": 90.0, "baro_rate": 0}
    r = Plane.from_feed(ac0, _LEVEL_RATE_FPM)
    _eq(len(r.trail), 1, "a new Plane's trail is seeded with its first fix")
    _eq(r.trail[0], (r.e, r.n, r.alt), "a trail fix is (e, n, alt)")
```

Add a line right after it:

```python
    _eq(r.traced, False, "a new Plane starts un-traced")
```

Then find this block, a little further down:

```python
    ac1 = dict(ac0, lat=52.1, lon=-0.1, alt_baro=11000)
    r2 = Plane.from_feed(ac1, _LEVEL_RATE_FPM, into=r)
    _eq(r2 is r, True, "from_feed(into=p) updates and returns the same object")
    _eq(r.alt, 11000, "the reused object's fields are updated in place")
```

Add right after it (still inside that same section, before the next
unrelated assertion):

```python
    r.traced = True   # simulate a completed backfill
    Plane.from_feed(ac1, _LEVEL_RATE_FPM, into=r)
    _eq(r.traced, True, "a repeat sighting (into=) never resets traced")
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 prestoradar/dev/test_plane.py`
Expected: `AttributeError` — `Plane` (via `__slots__`) has no attribute
`traced` yet, raised on the first new assertion's `r.traced` read.

- [ ] **Step 3: Implement the `traced` slot**

In `prestoradar/plane.py`, change the `__slots__` tuple:

```python
    __slots__ = ("callsign", "e", "n", "ve", "vn", "heading", "gs",
                 "vstate", "cat", "hex", "reg", "type", "desc", "alt",
                 "vrate", "squawk", "emergency", "dst", "dir", "trail")
```

to:

```python
    __slots__ = ("callsign", "e", "n", "ve", "vn", "heading", "gs",
                 "vstate", "cat", "hex", "reg", "type", "desc", "alt",
                 "vrate", "squawk", "emergency", "dst", "dir", "trail",
                 "traced")
```

And change:

```python
        p = into if into is not None else cls()
        if into is None:
            p.trail = []
```

to:

```python
        p = into if into is not None else cls()
        if into is None:
            p.trail = []
            p.traced = False   # not yet attempted -- see fetchqueue.py / traces.backfill()
```

- [ ] **Step 4: Run the test again and confirm it passes**

Run: `python3 prestoradar/dev/test_plane.py`
Expected: `plane.py: all parse assertions passed (4 aircraft)`

- [ ] **Step 5: Commit**

```bash
git add prestoradar/plane.py prestoradar/dev/test_plane.py
git commit -m "prestoradar: add Plane.traced, the trace-backfill lifecycle marker"
```

---

## Task 3: `feed.py` — `resolve()`, so the queue never holds a stale `Plane`

**Files:**
- Modify: `prestoradar/feed.py`
- Test: `prestoradar/dev/test_feed.py` (new file)

**Interfaces:**
- Consumes: `Feed._by_hex` (existing internal state, rebuilt every fetch).
- Produces: `Feed.resolve(hex_id) -> Plane | None`.

- [ ] **Step 1: Write the failing test**

Create `prestoradar/dev/test_feed.py`:

```python
#!/usr/bin/env python3
"""Desktop test for feed.Feed.resolve() -- no network, no device."""

import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _eq(got, want, what):
    if got != want:
        raise AssertionError("%s: got %r, want %r" % (what, got, want))


class _P:
    def __init__(self, hex):
        self.hex = hex


def test_resolve():
    from feed import Feed
    f = Feed("host", "/path", "agent/1.0", 256, 30_000)
    plane = _P("aabbcc")
    f._by_hex = {"aabbcc": plane}

    _eq(f.resolve("aabbcc"), plane, "resolves a hex present in the current registry")
    _eq(f.resolve("ffffff"), None, "a hex not in the registry resolves to None")

    f._by_hex = {}   # simulates the aircraft dropping off the next fetch cycle
    _eq(f.resolve("aabbcc"), None, "a departed aircraft's hex no longer resolves")


def main():
    test_resolve()
    print("feed.Feed.resolve(): all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 prestoradar/dev/test_feed.py`
Expected: `AttributeError: 'Feed' object has no attribute 'resolve'`

- [ ] **Step 3: Implement `Feed.resolve()`**

In `prestoradar/feed.py`, right after `__init__` (after the `self._by_hex =
{}` line and before `async def _fetch(self):`), add:

```python
    def resolve(self, hex_id):
        """The current Plane for hex_id, or None if it isn't (or is no
        longer) in the live feed. Used by radar.py's trace-backfill queue
        to look up a queued aircraft only once its turn to fetch actually
        comes up, rather than holding a direct Plane reference that could
        outlive the aircraft's time on screen."""
        return self._by_hex.get(hex_id)
```

- [ ] **Step 4: Run the test again and confirm it passes**

Run: `python3 prestoradar/dev/test_feed.py`
Expected: `feed.Feed.resolve(): all assertions passed`

- [ ] **Step 5: Commit**

```bash
git add prestoradar/feed.py prestoradar/dev/test_feed.py
git commit -m "prestoradar: add Feed.resolve(hex), a safe lookup into the live registry"
```

---

## Task 4: `traces.py` — replace the TTL cache with `backfill()`

**Files:**
- Modify: `prestoradar/traces.py`
- Modify: `prestoradar/dev/trace_lookup.py` (manual live-network harness —
  update to the new API so it still runs; not part of the automated tests)
- Test: `prestoradar/dev/test_traces.py` (new file)

**Interfaces:**
- Consumes: `Plane.traced` / `Plane.trail` (Task 2), `net.http_get` (existing,
  unchanged), `geometry.project` (existing, unchanged), `settings.TRACE_SEED`
  / `settings.USER_AGENT` (existing).
- Produces: `traces.backfill(plane)` (async — fetches, parses, and splices
  a result into `plane.trail`, always leaving `plane.traced == True`),
  `traces.points_for(plane)` (unchanged external contract: `plane.trail` if
  it has ≥2 points, else `None`), `traces._apply(plane, pts)` (the pure
  splice-or-leave decision, exposed for the test below).
- Removes: `traces.request()`, `traces.get()`, `traces._cache`,
  `traces._fetched_ms`, `traces._MAX_ENTRIES`, `traces._TTL_MS`,
  `traces._store()`.

- [ ] **Step 1: Write the failing test**

Create `prestoradar/dev/test_traces.py`:

```python
#!/usr/bin/env python3
"""Desktop test for traces._apply and traces.points_for -- the pure
splice-or-leave and read-side logic. No device, no network: backfill()'s
fetch/inflate/parse pipeline is verified on-device and via
dev/trace_lookup.py, same as the old _fetch() always was."""

import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _eq(got, want, what):
    if got != want:
        raise AssertionError("%s: got %r, want %r" % (what, got, want))


class _P:
    def __init__(self, trail):
        self.trail = trail
        self.traced = False


def test_apply_replaces_on_a_good_result():
    from traces import _apply
    p = _P(trail=[(1.0, 2.0, 1000)])   # one live fix so far
    backfilled = [(0.0, 0.0, 900), (1.0, 2.0, 1000), (2.0, 3.0, 1100)]
    _apply(p, backfilled)
    _eq(p.trail, backfilled, "a >=2-point result replaces the trail wholesale")
    _eq(p.traced, True, "traced flips to True on success")


def test_apply_leaves_trail_on_a_short_or_missing_result():
    from traces import _apply
    p = _P(trail=[(1.0, 2.0, 1000)])
    _apply(p, None)
    _eq(p.trail, [(1.0, 2.0, 1000)], "no result leaves the trail untouched")
    _eq(p.traced, True, "traced still flips to True -- never retried")

    p2 = _P(trail=[(1.0, 2.0, 1000)])
    _apply(p2, [(0.0, 0.0, 900)])   # a single-point result: too short to be useful
    _eq(p2.trail, [(1.0, 2.0, 1000)], "a too-short result leaves the trail untouched")
    _eq(p2.traced, True, "traced still flips to True")


def test_points_for():
    from traces import points_for
    _eq(points_for(_P(trail=[(0, 0, 0), (1, 1, 100)])), [(0, 0, 0), (1, 1, 100)],
        ">=2 points is usable")
    _eq(points_for(_P(trail=[(0, 0, 0)])), None, "a single point is not enough")
    _eq(points_for(_P(trail=[])), None, "an empty trail -> None")


def main():
    test_apply_replaces_on_a_good_result()
    test_apply_leaves_trail_on_a_short_or_missing_result()
    test_points_for()
    print("traces._apply + traces.points_for: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 prestoradar/dev/test_traces.py`
Expected: `ImportError: cannot import name '_apply' from 'traces'`

- [ ] **Step 3: Replace `prestoradar/traces.py`**

Replace the entire file with:

```python
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
```

- [ ] **Step 4: Run the test again and confirm it passes**

Run: `python3 prestoradar/dev/test_traces.py`
Expected: `traces._apply + traces.points_for: all assertions passed`

- [ ] **Step 5: Update the manual live-network harness**

`prestoradar/dev/trace_lookup.py` calls the now-removed `traces.request()`/
`traces.get()`. It's a manual CLI tool (not part of the automated tests),
but keep it working. Replace:

```python
async def seed_trace(plane):
    traces.request(plane)
    while traces.get(plane.hex) == "":
        await asyncio.sleep(0.25)
    return traces.get(plane.hex)
```

with:

```python
async def seed_trace(plane):
    before = len(plane.trail)
    await traces.backfill(plane)
    return before
```

And in `async def run(args):`, replace:

```python
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
```

with:

```python
    before = await seed_trace(plane)
    print()
    if len(plane.trail) <= before:
        print(f"trace_recent: nothing usable on file for {plane.hex} "
              f"-- plane.trail is unchanged, radar would keep growing it live")
    else:
        print(f"trace_recent replaced plane.trail for {plane.hex}:")
        describe(plane.trail)

    chosen = traces.points_for(plane)
    print()
    print(f"points_for() would hand the renderer: "
          f"{'None' if chosen is None else str(len(chosen)) + ' points'}")
    return 0
```

Verify it still runs against a live aircraft (needs network + a real
callsign/hex currently in range — skip if none is available right now, but
don't skip the diff review):

Run: `python3 prestoradar/dev/trace_lookup.py --callsign <a real one>`
Expected: no `AttributeError`/`ImportError`; prints the same shape of output
as before.

- [ ] **Step 6: Commit**

```bash
git add prestoradar/traces.py prestoradar/dev/test_traces.py prestoradar/dev/trace_lookup.py
git commit -m "prestoradar: traces.backfill() replaces the per-tap TTL cache"
```

---

## Task 5: `ui.py` — enqueue every eligible aircraft, not just one

**Files:**
- Modify: `prestoradar/ui.py`
- Modify: `prestoradar/dev/test_prefetch.py`

**Interfaces:**
- Consumes: `fetchqueue.Queue.enqueue(key, priority)` (Task 1),
  `Plane.traced` / `.on_ground` / `.dst` / `.hex` / `.label` (Task 2 +
  existing), `routes.request` (existing, unchanged).
- Produces: `UI.__init__`'s new signature (adds a `trace_queue` parameter),
  `ui._enqueue_eligible(p)` (a pure predicate, exposed for the test below).
- Removes: `ui._nearest_visible()`, `ui.py`'s `import traces`, the
  `traces.request(p)` call inside `set_selected()`.

- [ ] **Step 1: Write the failing test**

In `prestoradar/dev/test_prefetch.py`, replace the whole file (it currently
tests `hit_test` and the now-removed `_nearest_visible`) with:

```python
#!/usr/bin/env python3
"""Desktop test for ui.hit_test (used by handle_tap) and
ui._enqueue_eligible (the trace-backfill enqueue decision). No device."""

import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _eq(got, want, what):
    if got != want:
        raise AssertionError("%s: got %r, want %r" % (what, got, want))


class _P:
    def __init__(self, name="", traced=False, on_ground=False):
        self.name = name
        self.traced = traced
        self.on_ground = on_ground

    def __repr__(self):
        return "<_P %s>" % self.name


def test_hit_test():
    from ui import hit_test
    a, b = _P("AAA"), _P("BBB")
    last_drawn = [(100, 100, a), (200, 200, b)]

    _eq(hit_test(last_drawn, 105, 102, hit_radius=26), a, "hits the nearer plane")
    _eq(hit_test(last_drawn, 205, 198, hit_radius=26), b, "hits the other plane")
    _eq(hit_test(last_drawn, 400, 400, hit_radius=26), None, "nothing within radius")
    _eq(hit_test([], 100, 100, hit_radius=26), None, "empty last_drawn -> None")
    tie_a, tie_b = _P("TIE_A"), _P("TIE_B")
    _eq(hit_test([(100, 100, tie_a), (100, 100, tie_b)], 100, 100, hit_radius=26),
        tie_a, "tie keeps the first candidate")


def test_enqueue_eligible():
    from ui import _enqueue_eligible

    _eq(_enqueue_eligible(_P()), True, "never-attempted + airborne -> eligible")
    _eq(_enqueue_eligible(_P(on_ground=True)), False, "grounded -> not eligible")
    _eq(_enqueue_eligible(_P(traced="pending")), False, "already pending -> not eligible")
    _eq(_enqueue_eligible(_P(traced=True)), False, "already done -> not eligible")


def main():
    test_hit_test()
    test_enqueue_eligible()
    print("ui.hit_test + ui._enqueue_eligible: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 prestoradar/dev/test_prefetch.py`
Expected: `ImportError: cannot import name '_enqueue_eligible' from 'ui'`

- [ ] **Step 3: Implement the `ui.py` changes**

Remove the `import traces` line near the top of `prestoradar/ui.py`:

```python
import routes
import traces
from netlog import log
```

becomes:

```python
import routes
from netlog import log
```

Replace the `hit_test` docstring's now-stale mention of touch-down
prefetch (that mechanism was tried and reverted earlier on this branch) and
delete `_nearest_visible` entirely, replacing it with `_enqueue_eligible`.
Find:

```python
def hit_test(last_drawn, tx, ty, hit_radius):
    """Nearest entry in `last_drawn` ([(x, y, plane), ...], as stashed by
    Renderer.draw_planes()) within `hit_radius` px of (tx, ty), or None if
    nothing is close enough. Pulled out of handle_tap's own nearest-hit
    search so radar.py's touch-down prefetch can reuse the exact same test
    the eventual tap will use, rather than a second, possibly-diverging
    copy."""
    best, best_d = None, hit_radius * hit_radius
    for x, y, p in last_drawn:
        d = (x - tx) * (x - tx) + (y - ty) * (y - ty)
        if d < best_d:
            best, best_d = p, d
    return best


def _nearest_visible(planes, hidden):
    """The plane with the smallest `dst` (nm from centre, from the feed)
    among `planes` not excluded by `hidden(p)`, or None if there isn't one.
    Used to pick an idle-cycle trace-prefetch candidate -- the aircraft a
    user's eye (and thumb) is most likely to land on next."""
    best, best_dst = None, None
    for p in planes:
        if hidden(p) or p.dst is None:
            continue
        if best_dst is None or p.dst < best_dst:
            best, best_dst = p, p.dst
    return best
```

Replace with:

```python
def hit_test(last_drawn, tx, ty, hit_radius):
    """Nearest entry in `last_drawn` ([(x, y, plane), ...], as stashed by
    Renderer.draw_planes()) within `hit_radius` px of (tx, ty), or None if
    nothing is close enough. Pulled out of handle_tap's own nearest-hit
    search so it's exercised directly by dev/test_prefetch.py rather than
    only through a full UI instance."""
    best, best_d = None, hit_radius * hit_radius
    for x, y, p in last_drawn:
        d = (x - tx) * (x - tx) + (y - ty) * (y - ty)
        if d < best_d:
            best, best_d = p, d
    return best


def _enqueue_eligible(p):
    """True if p should be enqueued for a trace backfill: never attempted
    yet, and airborne. A grounded aircraft has little or no history for
    trace_recent to backfill (it likely just took off), so it's left
    un-enqueued and simply re-checked next cycle -- see
    docs/superpowers/specs/2026-09-18-trace-fetch-queue-design.md's "Skip
    heuristic"."""
    return p.traced is False and not p.on_ground
```

In `UI.__init__`, add the `trace_queue` parameter and store it. Find:

```python
    def __init__(self, settings, backdrop, renderer, hidden, request_redraw,
                 hit_radius,
                 settings_btn, spanel, sp_row0, sp_rowh):
        self.settings = settings
        self.backdrop = backdrop
        self.renderer = renderer
        self.hidden = hidden
        self.request_redraw = request_redraw
        self.hit_radius = hit_radius
        self.settings_btn = settings_btn
        self.spanel = spanel
        self.sp_row0 = sp_row0
        self.sp_rowh = sp_rowh
```

Replace with:

```python
    def __init__(self, settings, backdrop, renderer, hidden, request_redraw,
                 hit_radius, trace_queue,
                 settings_btn, spanel, sp_row0, sp_rowh):
        self.settings = settings
        self.backdrop = backdrop
        self.renderer = renderer
        self.hidden = hidden
        self.request_redraw = request_redraw
        self.hit_radius = hit_radius
        self.trace_queue = trace_queue
        self.settings_btn = settings_btn
        self.spanel = spanel
        self.sp_row0 = sp_row0
        self.sp_rowh = sp_rowh
```

In `set_selected()`, drop the now-redundant trace fetch (every selectable
aircraft has already gone through the enqueue decision in `on_feed_update`
below by the time it could be tapped). Find:

```python
    def set_selected(self, p):
        # Select p, or None to dismiss. No view shift any more -- the detail
        # card sits in a corner (UI-TRAILS.md decision 9), so the scene stays
        # centred. Kicks the route and trace lookups, same as before.
        if p is None:
            self.detail_level = 1
        self.selected = p
        if p is not None:
            routes.request(p)
            traces.request(p)
```

Replace with:

```python
    def set_selected(self, p):
        # Select p, or None to dismiss. No view shift any more -- the detail
        # card sits in a corner (UI-TRAILS.md decision 9), so the scene stays
        # centred. Still kicks the route lookup -- but not a trace fetch:
        # every aircraft is already enqueued for backfill in on_feed_update()
        # below the moment it's first seen, so by the time it's selectable
        # here it's already pending, done, or (if grounded) correctly not
        # yet eligible. See
        # docs/superpowers/specs/2026-09-18-trace-fetch-queue-design.md.
        if p is None:
            self.detail_level = 1
        self.selected = p
        if p is not None:
            routes.request(p)
```

In `on_feed_update()`, replace the single-candidate prefetch with the
enqueue-everyone loop. Find:

```python
        # Trace prefetch (bounded design, adsb-radar-echoes): warm the trace
        # cache for whichever aircraft is nearest the centre, once per feed
        # cycle -- the aircraft a user's eye/thumb is statistically most
        # likely to land on next. traces.request() already no-ops on a
        # pending or TTL-fresh entry, so calling it here regardless of
        # today's selection is cheap and never duplicates a fetch.
        nearest = _nearest_visible(fresh, self.hidden)
        if nearest is not None:
            log("prefetch: nearest-to-centre", nearest.label, "dst", nearest.dst, "nm")
            traces.request(nearest)
```

Replace with:

```python
        # Trace prefetch (docs/superpowers/specs/2026-09-18-trace-fetch-queue-design.md):
        # enqueue every aircraft that's new (traced is False) and airborne --
        # not just one candidate. A grounded aircraft is left un-enqueued and
        # simply re-checked next cycle. Priority is dst (nm from centre)
        # ascending, so the queue drains nearest-to-centre first -- the same
        # signal the earlier single-candidate prefetch used.
        for p in fresh:
            if _enqueue_eligible(p):
                p.traced = "pending"
                log("prefetch: enqueued", p.label, "dst", p.dst, "nm")
                self.trace_queue.enqueue(p.hex, p.dst)
```

- [ ] **Step 4: Run the test again and confirm it passes**

Run: `python3 prestoradar/dev/test_prefetch.py`
Expected: `ui.hit_test + ui._enqueue_eligible: all assertions passed`

Also re-run the other CPython tests touched so far to make sure nothing
regressed:

Run: `python3 prestoradar/dev/test_tap_cycle.py && python3 prestoradar/dev/test_plane.py && python3 prestoradar/dev/test_feed.py && python3 prestoradar/dev/test_traces.py && python3 prestoradar/dev/test_fetchqueue.py`
Expected: all five print their own "all assertions passed" line.

- [ ] **Step 5: Commit**

```bash
git add prestoradar/ui.py prestoradar/dev/test_prefetch.py
git commit -m "prestoradar: ui.py enqueues every eligible aircraft for trace backfill"
```

---

## Task 6: `radar.py` — wire up the queue, plus the settings constant

**Files:**
- Modify: `prestoradar/radar.py`
- Modify: `prestoradar/settings_example.py`
- Modify: `prestoradar/settings.py` (local, gitignored — not part of the
  commit, but required for the on-device app to boot)

**Interfaces:**
- Consumes: `fetchqueue.Queue` (Task 1), `Feed.resolve` (Task 3),
  `traces.backfill` (Task 4), `ui.UI`'s new constructor signature (Task 5),
  `settings.TRACE_QUEUE_INTERVAL_MS` (added in this task).
- Produces: the fully wired app — `_trace_queue`, `_process_traced_hex`,
  `_amain()`'s updated `gather()`.

This task isn't unit-testable on CPython (`radar.py` imports `presto`, a
device-only module) — verified by `py_compile`, the full existing CPython
suite, and on-device in Task 7.

- [ ] **Step 1: Add the settings constant**

In `prestoradar/settings_example.py`, find:

```python
FETCH_INTERVAL_MS = 30_000   # adsb.lol public endpoints allow ~1 request/second
ANIM_INTERVAL = 0.5          # seconds between dead-reckoning redraws (~2 fps)
```

Replace with:

```python
FETCH_INTERVAL_MS = 30_000   # adsb.lol public endpoints allow ~1 request/second
ANIM_INTERVAL = 0.5          # seconds between dead-reckoning redraws (~2 fps)

# Pace of the trace-backfill queue (fetchqueue.py): one request every this
# many ms. 1500 leaves headroom under adsb.lol's shared ~1 req/s courtesy
# budget alongside the position poll above and any on-tap route lookups.
# See docs/superpowers/specs/2026-09-18-trace-fetch-queue-design.md.
TRACE_QUEUE_INTERVAL_MS = 1500
```

Then make the identical addition to the local `prestoradar/settings.py`
(gitignored — this file won't be part of the commit in Step 6, but without
it `radar.py` raises a `NameError` on `TRACE_QUEUE_INTERVAL_MS` at import
time, the same way it would for a missing `FETCH_INTERVAL_MS`). Find:

```python
FETCH_INTERVAL_MS = 30_000   # adsb.lol public endpoints allow ~1 request/second
ANIM_INTERVAL = 0.5          # seconds between dead-reckoning redraws (~2 fps)

DRAW_BASEMAP = 1             # 0 to skip the coastline layer entirely
```

Replace with:

```python
FETCH_INTERVAL_MS = 30_000   # adsb.lol public endpoints allow ~1 request/second
ANIM_INTERVAL = 0.5          # seconds between dead-reckoning redraws (~2 fps)
TRACE_QUEUE_INTERVAL_MS = 1500   # pace of the trace-backfill queue; see settings_example.py

DRAW_BASEMAP = 1             # 0 to skip the coastline layer entirely
```

- [ ] **Step 2: Wire `fetchqueue.Queue` and the resolver into `radar.py`**

Find:

```python
import feed                             # sibling module: fetch/parse (feed.Feed)
import backdrop                         # sibling module: vector cache + raster (backdrop.Backdrop)
import render                           # sibling module: pens + all draw_* (render.Renderer)
import ui                               # sibling module: touch/selection/settings (ui.UI)
```

Replace with:

```python
import feed                             # sibling module: fetch/parse (feed.Feed)
import backdrop                         # sibling module: vector cache + raster (backdrop.Backdrop)
import fetchqueue                       # sibling module: generic paced priority queue
import render                           # sibling module: pens + all draw_* (render.Renderer)
import traces                           # sibling module: trace_recent fetch/parse (traces.backfill)
import ui                               # sibling module: touch/selection/settings (ui.UI)
```

Find:

```python
_feed = feed.Feed(RADAR_HOST, RADAR_PATH, USER_AGENT, LEVEL_RATE_FPM, FETCH_INTERVAL_MS)

def log_init():
```

Replace with:

```python
_feed = feed.Feed(RADAR_HOST, RADAR_PATH, USER_AGENT, LEVEL_RATE_FPM, FETCH_INTERVAL_MS)

# Trace-backfill queue (docs/superpowers/specs/2026-09-18-trace-fetch-queue-design.md):
# one shared, paced priority queue for every aircraft's one-time trace_recent
# backfill, draining slowly enough to stay under adsb.lol's shared courtesy
# budget alongside the position poll above and any route lookups routes.py
# makes on a tap.
_trace_queue = fetchqueue.Queue(TRACE_QUEUE_INTERVAL_MS)

async def _process_traced_hex(hex_id):
    # Resolve against feed's *current* registry, not whatever Plane existed
    # when this hex was enqueued -- an aircraft that's left the screen by
    # the time its turn comes up simply isn't found here, so its (already
    # garbage-collectable) old Plane object is never touched and no fetch is
    # wasted on it. See the design spec's "Eviction & lifecycle correctness".
    plane = _feed.resolve(hex_id)
    if plane is None:
        log("prefetch: dropped (departed)", hex_id)
        return
    if plane.traced == "pending":
        await traces.backfill(plane)


def log_init():
```

- [ ] **Step 3: Pass the queue into `ui.UI(...)` and add it to `_amain()`**

Find:

```python
_redraw = asyncio.Event()
_ui = ui.UI(SETTINGS, _backdrop, _renderer, _hidden, _redraw.set,
            HIT_RADIUS,
            SETTINGS_BTN, _SPANEL, _SP_ROW0, _SP_ROWH)
_feed.on_update = _ui.on_feed_update
```

Replace with:

```python
_redraw = asyncio.Event()
_ui = ui.UI(SETTINGS, _backdrop, _renderer, _hidden, _redraw.set,
            HIT_RADIUS, _trace_queue,
            SETTINGS_BTN, _SPANEL, _SP_ROW0, _SP_ROWH)
_feed.on_update = _ui.on_feed_update
```

Find:

```python
async def _amain():
    await asyncio.gather(_render_loop(), _feed.run(), _touch_loop())
```

Replace with:

```python
async def _amain():
    await asyncio.gather(_render_loop(), _feed.run(), _touch_loop(),
                          _trace_queue.run(_process_traced_hex))
```

- [ ] **Step 4: Verify it compiles and the full CPython suite still passes**

Run:
```bash
python3 -m py_compile prestoradar/radar.py prestoradar/ui.py prestoradar/traces.py prestoradar/plane.py prestoradar/feed.py prestoradar/fetchqueue.py
python3 prestoradar/dev/test_tap_cycle.py
python3 prestoradar/dev/test_plane.py
python3 prestoradar/dev/test_feed.py
python3 prestoradar/dev/test_traces.py
python3 prestoradar/dev/test_fetchqueue.py
python3 prestoradar/dev/test_prefetch.py
```
Expected: `py_compile` prints nothing (success), and every test script's own
"all assertions passed" line appears once, cleanly.

- [ ] **Step 5: Commit**

```bash
git add prestoradar/radar.py prestoradar/settings_example.py
git commit -m "prestoradar: wire the trace-backfill queue into radar.py's main loop"
```

(`prestoradar/settings.py` is gitignored and stays uncommitted — see the
note in Step 1.)

---

## Task 7: On-device verification

No code changes — this is the manual check the spec's own "Testing plan"
calls for, since none of the earlier tasks can verify real network timing,
real pacing, or real touch/render behaviour from a desktop.

- [ ] **Step 1: Deploy and boot over a busy scope**

Deploy the branch to the Presto (however this repo's normal deploy step
works) and boot it pointed at a busy `CENTER_LAT`/`CENTER_LON` — SF at the
default `RADIUS_KM = 30` has previously shown ~35-76 aircraft.

- [ ] **Step 2: Watch `radar_listen.py` and confirm the queue drains as designed**

Run: `python3 prestoradar/radar_listen.py`

Confirm, over the first couple of minutes after boot:
- `prefetch: enqueued <callsign> dst <N> nm` lines appear for aircraft as
  they're first seen, and never twice for the same aircraft while it stays
  on screen.
- `trace: <hex> -> N points  mem ...` lines (from `traces.backfill`) follow
  roughly `TRACE_QUEUE_INTERVAL_MS` (1.5s) apart during the initial
  backlog-draining window, not bursts of several at once.
- The aircraft nearest the centre at boot gets its `trace:` completion line
  first, or very early, not buried behind farther aircraft.
- If any aircraft leaves the screen while still queued, a
  `prefetch: dropped (departed) <hex>` line appears for it instead of a
  wasted `trace:` fetch.

- [ ] **Step 3: Confirm tap responsiveness**

Tap an aircraft that's been on screen a minute or more (should already show
its full backfilled trail with no visible delay) and one that just appeared
seconds ago (may still show a short, live-only trail if its queue entry
hasn't drained yet — expected, not a bug).

- [ ] **Step 4: Confirm the settings.py note**

Confirm the device actually boots with the local `settings.py` edit from
Task 6 Step 1 in place (a missing `TRACE_QUEUE_INTERVAL_MS` would surface as
a `NameError` in `radar.py` at import time, visible on the serial console
before WiFi even connects).
