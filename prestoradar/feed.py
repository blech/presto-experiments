import asyncio
import gc
import json
import sys
import time

import net
from netlog import log
from plane import Plane

# Snapshot sanity guard (DATA_TODOS.md #5): a fetch returning fewer than this
# fraction of the previous cycle's aircraft count is treated as suspect (a
# partial or null-ish adsb.lol response) and the old list is kept instead --
# unless that old list has already been kept for _MAX_STALE_CYCLES in a row,
# in which case whatever comes back is accepted anyway so a genuine, lasting
# drop in traffic isn't locked out forever.
_MIN_RETAIN_FRACTION = 0.5
_MAX_STALE_CYCLES = 3

# Carry-forward for dropped contacts (DATA_TODOS.md #4): an aircraft absent
# from one fetch (feed hiccup, edge-of-range flicker) keeps dead-reckoning
# from its last fix for up to this long since it was last actually seen,
# instead of popping off the scope and reappearing later as a "new" contact.
_CARRY_FORWARD_MS = 75_000


def _reject_snapshot(new_count, old_count, stale_cycles, min_retain_fraction=_MIN_RETAIN_FRACTION,
                      max_stale_cycles=_MAX_STALE_CYCLES):
    """True if a fresh fetch of new_count aircraft should be rejected in
    favour of keeping the previous old_count-aircraft list: new_count falls
    below min_retain_fraction of old_count, and the old list hasn't already
    been kept for max_stale_cycles in a row (the anti-lockout escape valve).
    old_count == 0 has nothing worth protecting, so it's never rejected."""
    if old_count == 0:
        return False
    if stale_cycles >= max_stale_cycles:
        return False
    return new_count < min_retain_fraction * old_count


def _carry_forward(prev_by_hex, fresh_by_hex, now_ms, carry_forward_ms=_CARRY_FORWARD_MS,
                    ticks_diff=lambda a, b: a - b):
    """Planes present in prev_by_hex but absent from fresh_by_hex this cycle,
    kept alive for up to carry_forward_ms since they were last actually seen
    (Plane.missing_since, cleared by Plane.from_feed() whenever a hex is seen
    again). Mutates each newly-missing plane's missing_since in place.

    Returns (carried, dropped): carried is the list of planes still inside
    the window (so they drop out of the feed, same as today's behaviour with
    no carry-forward). dropped -- how many candidates fell outside it this
    cycle -- lets the caller log a one-line summary without a second pass
    over prev_by_hex.

    ticks_diff defaults to plain subtraction, fine for a desktop test's
    plain-int timestamps; the real caller passes time.ticks_diff so a
    MicroPython ticks_ms() wraparound (matters for a display left running
    for days) is handled correctly."""
    carried = []
    dropped = 0
    for h, p in prev_by_hex.items():
        if h in fresh_by_hex:
            continue
        if p.missing_since is None:
            p.missing_since = now_ms
        if ticks_diff(now_ms, p.missing_since) <= carry_forward_ms:
            carried.append(p)
        else:
            dropped += 1
    return carried, dropped


class Feed:
    """Owns the fetched aircraft list and its fetch lifecycle. `planes`/
    `fetch_count`/`fetch_ok` replace the module globals radar.py used to
    mutate directly (REFACTORING.md #2) -- any code holding this instance
    sees the same live state, no `global` needed.

    `on_update(planes)`, if set, is called with the fresh list after every
    successful fetch. It's the hook the caller (today radar.py, eventually
    ui.py) uses to react -- e.g. re-point a selection at the same aircraft
    in the new list -- without this module needing to know selection, or
    any other UI concept, exists.
    """

    def __init__(self, host, path, user_agent, level_rate_fpm, fetch_interval_ms):
        self.host = host
        self.path = path
        self.user_agent = user_agent
        self.level_rate_fpm = level_rate_fpm
        self.fetch_interval_ms = fetch_interval_ms
        self.planes = []
        self.fetch_count = 0     # completed fetch attempts, any outcome (0 == still loading)
        self.fetch_ok = False    # did the most recent attempt succeed?
        self.on_update = None
        # hex -> Plane, covering the aircraft the last fetch actually
        # returned *plus* whatever _carry_forward() kept alive past it, so it
        # stays bounded by "currently on screen or recently dropped," never
        # growing with long-gone contacts. Its point is object identity: an
        # aircraft still in range (or still within the carry-forward window)
        # keeps the same Plane instance across fetches, so its `trail` of
        # past fixes survives (DATA_TRACE.md item 6).
        self._by_hex = {}
        self._pending_by_hex = {}   # see _fetch()'s comment on why this is separate
        # Consecutive fetches rejected by the snapshot sanity guard
        # (DATA_TODOS.md #5) -- see _reject_snapshot()'s anti-lockout note.
        self._stale_cycles = 0

    def resolve(self, hex_id):
        """The current Plane for hex_id, or None if it isn't (or is no
        longer) in the live feed. Used by radar.py's trace-backfill queue
        to look up a queued aircraft only once its turn to fetch actually
        comes up, rather than holding a direct Plane reference that could
        outlive the aircraft's time on screen."""
        return self._by_hex.get(hex_id)

    async def _fetch(self):
        """Pull the current aircraft list from adsb.lol.

        Returns a list of Plane objects (see plane.py) holding position in
        the metric frame (e, n) and a per-second velocity (ve, vn) for dead
        reckoning between fetches, or None if the fetch/parse failed (the
        caller keeps animating the old list).
        """
        gc.collect()
        try:
            status, body = await net.http_get(self.host, self.path, self.user_agent)
        except Exception as e:  # noqa: BLE001
            log("fetch: request failed:", repr(e))
            return None

        log("fetch: HTTP", status, len(body), "bytes")
        if status != 200:
            log("fetch: HTTP", status, body[:200])
            return None

        try:
            data = json.loads(body)
        except ValueError as e:
            log("fetch: bad JSON:", repr(e), len(body), "bytes")
            return None
        finally:
            body = None
            gc.collect()

        # Per-aircraft decode lives in Plane.from_feed() (REFACTORING.md
        # #10) -- it returns None for an entry with no position, which used
        # to be a `continue` here. HIDE_ON_GROUND is still a draw-time
        # filter in radar.py's _hidden() (REFACTORING.md #5), not applied
        # here: every aircraft the feed returns is real data.
        planes = []
        by_hex = {}
        for aircraft in data.get("ac", []) or []:
            h = aircraft.get("hex") or ""
            # Reuse last fetch's Plane for this hex so its trail carries over
            # (Plane.from_feed updates it in place); a first sighting gets a
            # fresh object with an empty trail.
            p = Plane.from_feed(aircraft, self.level_rate_fpm,
                                into=self._by_hex.get(h) if h else None)
            if p is not None:
                planes.append(p)
                if h:
                    by_hex[h] = p

        # Carry forward any aircraft this fetch didn't mention but that's
        # still within its window since last really seen (DATA_TODOS.md #4) --
        # a feed hiccup or edge-of-range flicker keeps dead-reckoning instead
        # of popping off the scope and reappearing later as a "new" contact.
        carried, expired = _carry_forward(self._by_hex, by_hex, time.ticks_ms(),
                                           ticks_diff=time.ticks_diff)
        for p in carried:
            planes.append(p)
            by_hex[p.hex] = p
        if carried or expired:
            log("fetch: carried", len(carried), "expired", expired,
                "(past %ds)" % (_CARRY_FORWARD_MS // 1000))

        # Not committed to self._by_hex here: run()'s snapshot guard may
        # still reject this whole result and keep the previous planes list,
        # and self._by_hex has to stay in lock-step with whatever self.planes
        # actually ends up as -- otherwise the *next* cycle's carry-forward
        # and object-reuse lookups above would use a _by_hex that no longer
        # matches self.planes. run() commits self._pending_by_hex only when
        # it accepts this fetch. A direct _fetch() caller that never calls
        # run() (the dev/ harnesses) simply never commits one -- they don't
        # use resolve() or repeat fetches, so that's fine.
        self._pending_by_hex = by_hex
        return planes

    async def run(self):
        while True:
            log("fetch...")
            t = time.ticks_ms()
            try:
                fresh = await self._fetch()
            except Exception as e:  # noqa: BLE001
                log("FETCH ERROR:", repr(e))
                if hasattr(sys, "print_exception"):
                    sys.print_exception(e)
                fresh = None
            self.fetch_count += 1

            # Snapshot sanity guard (DATA_TODOS.md #5): a suspiciously small
            # fetch (a partial or null-ish adsb.lol response) keeps the
            # previous list instead of blanking or gutting the scope for a
            # cycle -- unless that list is already stale past the
            # anti-lockout limit, in which case accept whatever came back.
            if fresh is not None and self.planes and _reject_snapshot(
                    len(fresh), len(self.planes), self._stale_cycles):
                log("fetch: snapshot too small (%d of previous %d) -- "
                    "keeping the old list" % (len(fresh), len(self.planes)))
                self._stale_cycles += 1
                fresh = None
            elif fresh is not None:
                self._stale_cycles = 0

            self.fetch_ok = fresh is not None
            if fresh is not None:
                self.planes = fresh
                self._by_hex = self._pending_by_hex
                if self.on_update:
                    self.on_update(fresh)
                log("fetch done:", len(self.planes), "planes",
                    time.ticks_diff(time.ticks_ms(), t), "ms  mem", gc.mem_free())
            await asyncio.sleep_ms(self.fetch_interval_ms)
