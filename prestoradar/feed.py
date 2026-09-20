import asyncio
import gc
import json
import sys

import net
from netlog import log, ticks_diff, ticks_ms
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

# Second position source + failure breaker (DATA_TODOS.md #1): after this
# many consecutive fully-failed fetches (both primary and fallback, or no
# fallback configured), stretch the poll interval to _BREAKER_COOLDOWN_MS
# instead of hammering a dead endpoint every fetch_interval_ms and churning
# gc on each timeout. Snaps back to normal the moment either source
# succeeds. Reference: flyover-alert's breaker (threshold=4, cooldown=300).
_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_MS = 300_000


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
    plain-int timestamps; the real caller passes ticks_diff so a
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


def _extract_aircraft(data, key):
    """The aircraft list from a parsed source response, or [] if data is
    None or the key is missing/null. adsb.lol has been seen to answer 200
    with a null `ac` "when its backend is unhappy" (routes.py's _get_json
    has the same note for its own endpoints); an empty list here is the
    trigger to try the fallback source, same as a request/parse failure."""
    return (data.get(key) if data else None) or []


def _combine_sources(primary_data, primary_aircraft, fallback_attempted,
                      fallback_data, fallback_aircraft):
    """Decide the aircraft list + overall success for one fetch cycle.

    fallback_attempted is False whenever the primary already had aircraft
    (the common case -- no second request, no extra cost) or no fallback is
    configured; fallback_data/fallback_aircraft are only meaningful when it's
    True.

    Returns (aircraft, ok). ok is False only when neither source produced a
    parseable response at all -- an empty aircraft list from a source that
    DID respond (both up, genuinely nothing in range right now) is a
    legitimate result, not a failure, and must not trip the caller's
    consecutive-failure breaker."""
    if not fallback_attempted:
        return primary_aircraft, primary_data is not None
    if fallback_data is not None:
        return fallback_aircraft, True
    # The fallback was tried and also failed outright -- fall back to
    # whatever the primary gave (its own empty-but-valid result if it had
    # one, or its own failure if it didn't).
    return primary_aircraft, primary_data is not None


def _backoff_interval(consecutive_failures, normal_interval_ms,
                       threshold=_BREAKER_THRESHOLD, cooldown_ms=_BREAKER_COOLDOWN_MS):
    """The sleep interval for run()'s next cycle: cooldown_ms once
    consecutive_failures has reached threshold (both sources down for that
    many cycles running), otherwise the normal interval. Snaps back the
    moment a fetch succeeds (the caller resets consecutive_failures to 0)."""
    return cooldown_ms if consecutive_failures >= threshold else normal_interval_ms


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

    def __init__(self, host, path, user_agent, level_rate_fpm, fetch_interval_ms,
                 fallback=None):
        self.host = host
        self.path = path
        self.user_agent = user_agent
        self.level_rate_fpm = level_rate_fpm
        self.fetch_interval_ms = fetch_interval_ms
        # Second position source (DATA_TODOS.md #1): None, or a
        # (host, path, key) tuple -- adsb.fi's response key is "aircraft",
        # not adsb.lol's "ac". Only ever queried when the primary comes back
        # empty (failure or a genuinely empty result); a good cycle never
        # touches it.
        self.fallback = fallback
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
        # Consecutive fully-failed fetches (neither source produced anything
        # usable) -- drives the backoff breaker, see _backoff_interval().
        # Distinct from _stale_cycles: a too-small-but-real fetch is a
        # different problem with a different remedy (keep the old list, not
        # slow down polling).
        self._consecutive_failures = 0

    def resolve(self, hex_id):
        """The current Plane for hex_id, or None if it isn't (or is no
        longer) in the live feed. Used by radar.py's trace-backfill queue
        to look up a queued aircraft only once its turn to fetch actually
        comes up, rather than holding a direct Plane reference that could
        outlive the aircraft's time on screen."""
        return self._by_hex.get(hex_id)

    async def _fetch_source(self, host, path):
        """GET + JSON-parse one source. Returns the parsed body, or None on
        any failure (network error, non-200, bad JSON) -- never raises."""
        try:
            status, body = await net.http_get(host, path, self.user_agent)
        except Exception as e:  # noqa: BLE001
            log("fetch: request failed:", host, repr(e))
            return None

        log("fetch: HTTP", status, len(body), "bytes", "(%s)" % host)
        if status != 200:
            log("fetch: HTTP", status, body[:200], "(%s)" % host)
            return None

        try:
            return json.loads(body)
        except ValueError as e:
            log("fetch: bad JSON:", repr(e), len(body), "bytes", "(%s)" % host)
            return None
        finally:
            body = None
            gc.collect()

    async def _fetch(self):
        """Pull the current aircraft list from adsb.lol, falling back to
        self.fallback (adsb.fi, DATA_TODOS.md #1) only when the primary
        comes back empty -- a request/HTTP/JSON failure, or a technically-
        successful-but-empty response (adsb.lol has been seen to answer 200
        with a null `ac` "when its backend is unhappy"). A normal cycle with
        real primary data never touches the fallback: no extra request or
        RAM cost.

        Returns a list of Plane objects (see plane.py) holding position in
        the metric frame (e, n) and a per-second velocity (ve, vn) for dead
        reckoning between fetches, or None if nothing usable came back from
        either source (the caller keeps animating the old list and counts
        this towards the failure breaker, _backoff_interval()).
        """
        gc.collect()
        primary_data = await self._fetch_source(self.host, self.path)
        primary_aircraft = _extract_aircraft(primary_data, "ac")

        fallback_attempted = not primary_aircraft and self.fallback is not None
        fallback_data = fallback_aircraft = None
        if fallback_attempted:
            fb_host, fb_path, fb_key = self.fallback
            log("fetch: primary empty/failed -- trying fallback", fb_host)
            fallback_data = await self._fetch_source(fb_host, fb_path)
            fallback_aircraft = _extract_aircraft(fallback_data, fb_key)
            if fallback_data is not None:
                log("fetch: fallback", fb_host, "->", len(fallback_aircraft), "aircraft")

        ac_list, ok = _combine_sources(primary_data, primary_aircraft,
                                        fallback_attempted, fallback_data, fallback_aircraft)
        if not ok:
            return None

        # Per-aircraft decode lives in Plane.from_feed() (REFACTORING.md
        # #10) -- it returns None for an entry with no position, which used
        # to be a `continue` here. HIDE_ON_GROUND is still a draw-time
        # filter in radar.py's _hidden() (REFACTORING.md #5), not applied
        # here: every aircraft the feed returns is real data.
        planes = []
        by_hex = {}
        for aircraft in ac_list:
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
        carried, expired = _carry_forward(self._by_hex, by_hex, ticks_ms(),
                                           ticks_diff=ticks_diff)
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
            t = ticks_ms()
            try:
                fresh = await self._fetch()
            except Exception as e:  # noqa: BLE001
                log("FETCH ERROR:", repr(e))
                if hasattr(sys, "print_exception"):
                    sys.print_exception(e)
                fresh = None
            self.fetch_count += 1

            # Failure breaker (DATA_TODOS.md #1): tracked on _fetch()'s raw
            # result, before the snapshot guard below can turn a technically-
            # successful-but-small fetch into None -- that's a different
            # problem (kept the old list) from "neither source produced
            # anything at all," which is what should slow down polling.
            if fresh is None:
                self._consecutive_failures += 1
            else:
                self._consecutive_failures = 0

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
                    ticks_diff(ticks_ms(), t), "ms  mem", gc.mem_free())

            interval = _backoff_interval(self._consecutive_failures, self.fetch_interval_ms)
            if interval != self.fetch_interval_ms:
                log("fetch: breaker active --", self._consecutive_failures,
                    "consecutive failures, sleeping", interval // 1000, "s")
            await asyncio.sleep_ms(interval)
