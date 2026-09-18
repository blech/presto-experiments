# Trace prefetch: proactive backfill via a shared fetch queue

Branch: `adsb-radar-echoes`. Follow-on to the nearest-to-centre / touch-down
trace prefetch work (this same branch, landed and then partially reverted
2026-09-18). This spec covers the next iteration: fetching a trace backfill
for every aircraft as it's tracked, not just the one nearest the centre, and
changing trace freshness from periodic re-fetch to one-time backfill plus
free ongoing accumulation from the existing position poll.

## Context

The original latency problem (`presto_radar.py` tap-to-trail feels slow next
to a ~65ms website lookup) was root-caused to a ~280ms TLS handshake plus
~90ms of inflate/parse, paid fresh on every tap-triggered fetch
(`prestoradar/traces.py`). Two mitigations were tried:

- **Nearest-to-centre prefetch** (shipped): once per feed cycle, warm the
  trace cache for whichever tracked aircraft is closest to the centre.
  Confirmed on-device to work — a prefetched aircraft's tap-to-trail latency
  drops to ~0 because the fetch already happened.
- **Touch-down prefetch** (shipped, then reverted): kick off a fetch the
  instant a finger lands near a blip, before the tap dispatches. Measured on
  `radar_listen.py` to buy nothing: `radar.py`'s `_touch_loop` already
  dispatches a tap on the touch-**down** edge itself, not on release, so the
  prefetch call and the real fetch fire in the same event-loop iteration.
  Removed as dead code.

This spec extends the surviving mechanism (prefetch ahead of the tap) from
"one candidate, refreshed periodically" to "every currently-tracked aircraft,
backfilled once." It also lays groundwork `UI-TRAILS.md`'s deferred list/board
mode will need: a bounded-concurrency, paced fetch queue serving many
aircraft, which that mode's route-fetching was already known to require.

## Goals

- Every aircraft currently in the feed gets its trace backfilled
  proactively, not just the single nearest-to-centre one, so more taps land
  on an already-warm trail.
- Stay within adsb.lol's shared ~1 request/second courtesy budget even when
  a dense scope (the codebase's own note: 76 aircraft over SF) means dozens
  of aircraft need backfilling at once.
- Eliminate the periodic re-fetch entirely: a trace is fetched from adsb.lol
  **once** per aircraft sighting, then kept current for free using the
  position-poll fixes `feed.py` already computes every cycle.
- Reuse the resulting fetch-queue mechanism generically enough that a future
  list/board mode's route-fetching can be a second client of it.

## Non-goals

- Not building the list/board mode itself — only the shared queue it will
  need.
- Not fixing the trail fade-inconsistency issue already logged in
  `UI-TRAILS.md` decision 3, or matching backfill/live-poll resolution
  exactly (see "Resolution mismatch" below) — both are explicitly deferred.
- Not adding a visual distinction between a freshly-departed aircraft and
  one that entered from the screen edge — the takeoff/edge signal is used
  only to decide whether a backfill fetch is worth making.
- Not touching `routes.py` or route-lookup behaviour.

## Architecture overview

Three pieces change:

1. **`fetchqueue.py`** (new) — a generic, priority-ordered, single-concurrency
   paced work queue. Knows nothing about aircraft, hexes, or traces; just
   drains `(priority, key)` pairs at a fixed interval, calling a
   caller-supplied async callback per key.
2. **`plane.py`** — one new field on `Plane`, `traced`, a tri-state marker
   (`False` not yet attempted / `"pending"` queued or in-flight / `True`
   done) that replaces `traces.py`'s old per-hex cache dict as the source of
   truth for "does this aircraft still need a backfill."
3. **`traces.py`** — loses its TTL cache (`_cache`, `_fetched_ms`,
   `_MAX_ENTRIES`, `_TTL_MS`, `get()`); keeps the network fetch, gunzip,
   parse, and downsample logic, now writing the result directly into
   `plane.trail` instead of a module-level dict.

`radar.py` wires them together: it owns one `fetchqueue.Queue` instance,
starts its drain loop alongside the existing tasks, and supplies the
"resolve a queued hex back to today's live `Plane` object" callback — the
one piece that has to live outside `fetchqueue.py` (which is hex-agnostic)
and outside `traces.py` (which should only ever operate on an already-live
`Plane`, never look one up itself).

## Component: `fetchqueue.py`

```python
class Queue:
    def __init__(self, interval_ms):
        self._interval_ms = interval_ms
        self._pending = []          # [(priority, key), ...]

    def enqueue(self, key, priority):
        self._pending.append((priority, key))

    async def run(self, process_one):
        while True:
            if self._pending:
                self._pending.sort(key=lambda t: t[0])
                _, key = self._pending.pop(0)
                await process_one(key)
            await asyncio.sleep_ms(self._interval_ms)
```

A plain list with a linear sort is deliberate: expected depth is a few dozen
entries at most (the codebase's own high-water mark is 76 aircraft), where
an `O(n log n)` sort per drain tick is trivial — no heap needed.

`key` is opaque to the queue (a hex string, in this use). `priority` is a
plain comparable captured at enqueue time — not re-evaluated as the
underlying aircraft moves; at the queue's drain rate (below) an aircraft's
`dst` changes negligibly between enqueue and dequeue, so a static snapshot is
enough. No de-duplication inside the queue itself — the caller (via
`plane.traced`) is responsible for never enqueueing the same key twice while
it's still pending, which is simpler than teaching the queue about identity.

`Queue` is a class, not module-level singleton state, so one instance can be
constructed and shared across every feature that needs to stay under
adsb.lol's request budget (traces now; a future list mode's route-fetching
later) rather than each feature getting its own independent pacing that
could stack.

## Component: `plane.py`

One new `__slots__` entry: `traced`. Set to `False` when a `Plane` is first
created (alongside the existing `p.trail = []` in `from_feed()`, only on the
`into is None` branch — an existing sighting reuses its current value
untouched). No other change to `plane.py` — `_record_fix()`, `trail`, and
`_TRAIL_MAX` are unchanged; they're exactly the "keep growing for free"
mechanism this design leans on.

## Component: `traces.py`

Removed entirely: `_cache`, `_fetched_ms`, `_MAX_ENTRIES`, `_TTL_MS`,
`get()`, `request()`, the old `points_for()`'s seed-vs-RAM-trail choice.

Kept, mostly as-is: `TRACE_HOST`, `_KEEP`, `_MAX_COMPRESSED`, `_MAX_INFLATED`,
`_inflate()`, `_project()` (the gunzip/parse/downsample pipeline itself
doesn't change).

New shape:

```python
async def backfill(plane):
    """Fetch plane's trace_recent backfill and splice it into plane.trail.
    Always leaves plane.traced == True on return, success or not."""
    try:
        status, body = await net.http_get(TRACE_HOST, _path(plane.hex), settings.USER_AGENT)
        ...  # unchanged: status/size guards, gunzip, json.loads, _project()
        pts = _project(data)
        if pts and len(pts) >= 2:
            plane.trail = pts       # wholesale replace, not merge -- see below
    except Exception as e:
        log("trace:", plane.hex, "backfill failed:", repr(e))
    finally:
        plane.traced = True
```

`points_for(plane)` simplifies to:

```python
def points_for(plane):
    return plane.trail if plane.trail and len(plane.trail) >= 2 else None
```

**Splice strategy: replace, not merge.** When `backfill()` succeeds,
`plane.trail` is overwritten wholesale with the downsampled backfill points,
discarding whatever handful of live fixes had accumulated since the
aircraft was first sighted. The backfill's window covers that same recent
period at higher resolution, so replacing is strictly at least as good as
merging and avoids the complexity (and the timestamp bookkeeping — trail
tuples have no timestamp field today) of splicing two overlapping,
different-resolution point lists without duplicating or misordering points.
Every `_record_fix()` call after the replace just keeps appending onto this
same list exactly as it does today.

## Component: `ui.py` / `feed.py` wiring

`UI.__init__` gains one more injected dependency, `trace_queue` (a
`fetchqueue.Queue`), following the same pattern `backdrop`/`renderer`/
`hidden`/`request_redraw` already use.

`on_feed_update(fresh)` replaces the current single-candidate
`_nearest_visible()` call with:

```python
for p in fresh:
    if p.traced is False and not p.on_ground:
        p.traced = "pending"
        self.trace_queue.enqueue(p.hex, p.dst)
```

`_nearest_visible()` is removed — with every eligible aircraft now enqueued,
there's no separate "pick just the one nearest candidate" step; priority
within the queue (below) achieves the same effect.

`radar.py` constructs the queue and its resolver, and adds it to the
`asyncio.gather()` in `_amain()`:

```python
_trace_queue = fetchqueue.Queue(TRACE_QUEUE_INTERVAL_MS)

async def _process_traced_hex(hex_id):
    plane = _feed.resolve(hex_id)
    if plane is not None and plane.traced == "pending":
        await traces.backfill(plane)

async def _amain():
    await asyncio.gather(_render_loop(), _feed.run(), _touch_loop(),
                          _trace_queue.run(_process_traced_hex))
```

`feed.py` gains one small public method, `Feed.resolve(hex_id)` (`return
self._by_hex.get(hex_id)`), rather than `radar.py` reaching into `_by_hex`
directly — that dict is explicitly documented as `feed.py`'s own internal
state, rebuilt every fetch, and the rest of this codebase is careful to
inject callbacks or expose a method instead of reaching across a module
boundary into a leading-underscore attribute (see `render.py`/`ui.py`'s own
docstrings on this point).

This keeps `fetchqueue.py` hex/Plane-agnostic, keeps `traces.py` operating
only on a live `Plane` it's handed (never resolving one itself), and keeps
the "resolve a queued key back to a live object, or drop it" responsibility
where the rest of this codebase already puts this kind of glue: `radar.py`.

## Data flow walkthrough

1. `feed.run()` completes a poll cycle; `on_feed_update(fresh)` runs.
2. For each aircraft in `fresh` that's new, airborne, and not yet attempted
   (`traced is False`), it's marked `"pending"` and enqueued with priority =
   its current `dst`.
3. An aircraft first sighted on the ground is left at `traced is False` and
   simply re-checked next cycle — no queue entry, no network cost — until
   it's airborne (then enqueued) or leaves the screen (then evicted for
   free, see below).
4. `_trace_queue.run()` drains one entry per `TRACE_QUEUE_INTERVAL_MS`,
   nearest-to-centre first (lowest `dst` sorts first).
5. At dequeue, `_process_traced_hex` calls `_feed.resolve(hex_id)`, which
   looks up the hex against `feed`'s *current* registry. If the aircraft has
   left the screen, `resolve()` returns `None` and the entry is silently
   dropped — no fetch, no wasted work, and the (already-evicted) `Plane`
   object was never kept alive by the queue in the first place, since the
   queue only ever held its hex.
6. If found and still `"pending"`, `traces.backfill(plane)` runs: fetch,
   gunzip, parse, downsample, replace `plane.trail`, set `plane.traced =
   True` — terminal, regardless of outcome.
7. Every poll cycle from here on, `_record_fix()` (unchanged) keeps
   appending that cycle's real fix onto the same `plane.trail` — no further
   network cost for this aircraft's trace, ever, for as long as it stays on
   screen.
8. A tap on any aircraft calls `points_for(plane)`, which now just reads
   `plane.trail` directly — already backfilled if the queue got to it in
   time, otherwise whatever's accumulated live so far (today's fallback
   behaviour, unchanged).

## Candidate selection & priority

Every currently-visible, airborne aircraft is a candidate (no top-N cap),
matching `UI-TRAILS.md`'s own accepted list-mode UX of results "filling in
progressively." Within the queue, priority is `dst` ascending — nearest to
centre drains first, reusing the exact signal that was already proven to
predict "about to be tapped" (from the nearest-to-centre prefetch shipped
earlier this branch), rather than introducing a new or blended ranking.

## Skip heuristic: takeoff vs. edge-entry

Used purely to decide whether a backfill fetch is worth its network cost,
not as a UI distinction. An aircraft first sighted `on_ground` (the same
`Plane.on_ground` property `HIDE_ON_GROUND` already uses) has, by
definition, little or no history for `trace_recent` to return — so it's left
un-enqueued until it's airborne, at which point the normal enqueue path picks
it up. An aircraft first sighted already airborne (entering from the screen
edge) is exactly the case where backfill is valuable — it may have many
minutes of flight the live poll alone would never show — and gets enqueued
immediately.

## Rate limiting & worst-case timing

`TRACE_QUEUE_INTERVAL_MS = 1500` (new `settings.py` constant): one request
every 1.5s, comfortably under adsb.lol's ~1 req/s courtesy ceiling with
headroom for the position poll (1 req/30s, negligible) and any concurrent
route lookups (`routes.py`, only on an actual tap).

Worst case — cold boot over a dense scope (76 aircraft, the codebase's own
recorded high-water mark) — takes roughly 76 × 1.5s ≈ 114s to fully backfill
every visible aircraft. This is a one-time warm-up, not a steady-state cost:
once backfilled, an aircraft's trace never needs re-fetching for as long as
it stays on screen (see "Data flow," step 7), so the queue's steady-state
load is just newly-arriving aircraft, not the whole visible set repeatedly.
Because priority is nearest-to-centre first, the aircraft most likely to be
tapped soon after boot clear the backlog first.

Each fetch's ~90ms of synchronous inflate/parse work (measured in
`DATA_TRACE.md`) briefly delays whatever the render/touch loop would have
done next, since everything shares one core. At a 1.5s pace this is a small,
transient hitch roughly once every 1.5s during the backlog-draining window
only — the same kind of cost the already-shipped nearest-to-centre prefetch
and every tap-triggered fetch already accept. Not mitigated here; revisit
only if it's visibly bad on-device.

## Memory

No new ceiling. Steady-state trail storage already scales with aircraft
count today (`plane.trail`, capped at `_TRAIL_MAX = 45` per aircraft,
regardless of this design), and this change doesn't raise that cap or add a
second storage location — it just changes what populates the existing one.
The only transient memory increase is the same per-fetch inflate/parse
buffer traces.py already uses (~16KB, per `DATA_TRACE.md`'s on-device
probe), now potentially recurring every 1.5s during a backlog-draining
window instead of only on a tap or once per 30s — still small against the
~8MB free `gc.mem_free()` typically reports.

## Eviction & lifecycle correctness

`feed.py` already rebuilds `_by_hex` and `planes` from scratch every poll
cycle, so a departed aircraft's `Plane` object is dropped from every place
that currently references one (`_by_hex`, `Feed.planes`, `render.py`'s
`last_drawn`, `ui.selected` via `on_feed_update`'s re-pointing) with no new
code. The one place that would have kept a departed aircraft's object alive
longer than that — a queue entry holding a direct `Plane` reference — is why
the queue holds hexes, not objects: `_process_traced_hex` resolves the hex
against `feed`'s *current* registry only at dequeue time, so a departed
aircraft's entry simply resolves to `None` and is dropped, with no lingering
reference and no wasted fetch. If the same hex reappears before its old
queue entry drains, the fresh lookup picks up whatever `Plane` object is
current (a new one, per `DATA_TODOS.md`'s note that a one-fetch gap isn't
carried forward) and makes a fresh, correct decision based on *its*
`traced` state — self-correcting, no special-casing needed.

## Error handling

A failed fetch (network error, non-200, oversized body, bad JSON, or a
trace with fewer than 2 usable points) leaves `plane.trail` untouched and
sets `plane.traced = True` — terminal, no retry. This matches
`DATA_TRACE.md`'s original reasoning for the tap-triggered fetch it's
replacing: a 404 (no trace on file) is unlikely to change moments later, so
retrying only spends queue budget for no expected gain. The aircraft's trail
still grows from live fixes going forward, same as always.

## Testing plan

CPython-testable (following the existing `dev/test_*.py` convention,
extending `dev/test_prefetch.py`):

- `fetchqueue.Queue`: enqueue order is priority-respecting (lowest first),
  ties preserve insertion order, `run()` drains one entry per tick and calls
  back with the right key, an empty queue tick is a no-op.
- The enqueue-eligibility decision in `on_feed_update` (replacing
  `_nearest_visible`'s tests): a `traced is False`, airborne, non-hidden
  plane gets enqueued and flips to `"pending"`; an on-ground one doesn't; an
  already-`"pending"` or `True` one is never re-enqueued.
- `traces.backfill`'s splice-or-leave decision, given a fake fetch result:
  a ≥2-point result replaces `plane.trail`; a too-short or failed one leaves
  it untouched; `plane.traced` ends at `True` either way.

Needs on-device verification (via `radar_listen.py`, as used for the
nearest-to-centre prefetch): the queue actually drains at the configured
pace under a real, busy feed; a departed aircraft's queue entry really does
no-op rather than firing a request; the ~114s cold-boot backlog-drain time
in a dense scope is tolerable in practice, not just on paper.

## Migration: code removed or changed

- `traces.py`: `_cache`, `_fetched_ms`, `_MAX_ENTRIES`, `_TTL_MS`, `get()`,
  `request()` deleted; `points_for()` simplified; new `backfill()` replaces
  the old `_fetch()`/`_store()` pair.
- `plane.py`: new `traced` slot.
- `feed.py`: new public `Feed.resolve(hex_id)` method (`return
  self._by_hex.get(hex_id)`), so `radar.py` never reaches into `_by_hex`
  directly.
- `ui.py`: `_nearest_visible()` deleted; `on_feed_update()`'s prefetch call
  replaced with the enqueue-everyone loop; `UI.__init__` takes a
  `trace_queue` parameter.
- `radar.py`: constructs `fetchqueue.Queue`, `_process_traced_hex`, and adds
  the queue's `run()` to `_amain()`'s `gather()`; passes `TRACE_QUEUE_INTERVAL_MS`
  and the queue into `ui.UI(...)`.
- `settings.py` / `settings_example.py`: new `TRACE_QUEUE_INTERVAL_MS = 1500`.
- `fetchqueue.py`: new file.
- `dev/test_prefetch.py`: `_nearest_visible` tests removed; new tests for
  the enqueue-eligibility decision and `fetchqueue.Queue` added (a new
  `dev/test_fetchqueue.py` may be cleaner than growing this file further —
  implementer's call).

`hit_test()` (added in the prior bounded change) is untouched — still used
by `handle_tap`, unrelated to this feature.

## Future reuse

`fetchqueue.Queue` is deliberately generic (opaque `key`, caller-supplied
`process_one`) so `UI-TRAILS.md`'s deferred list/board mode can construct a
second instance (or share this one, if a single combined adsb.lol-wide
budget turns out to matter more than per-feature pacing — a decision for
that mode's own design) for batched route-fetching, without redesigning the
scheduling primitive.

## Open follow-ups (deliberately deferred)

- **Resolution mismatch**: the backfilled portion of a trail is
  higher-resolution (~6-7s between points) than the live-appended portion
  (30s, `FETCH_INTERVAL_MS`), so a long-lived selection's trail gets visibly
  coarser toward "now." Accepted for now; revisit only if it reads badly
  on-device over a long dwell.
- **Trail fade inconsistency** (already logged in `UI-TRAILS.md` decision 3,
  2026-09-18): `_trace_pen_index()`'s fade reads differently depending on
  point count. This design doesn't fix it, and changes its shape slightly
  (one growing list instead of a seed/RAM-trail choice) without resolving
  it — still open.
