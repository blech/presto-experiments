#!/usr/bin/env python3
"""
Desktop (CPython) test for routes.py's LRU cache bound and the throttled
batch queue -- no WiFi, no device.

routes._cache / _tries were unbounded: one entry per callsign ever looked
up (DATA_TODOS.md #3). A list view touching ~30 callsigns a cycle makes
that acute, so the cache is now an LRU capped at routes._CACHE_MAX and
batch callers go through routes.enqueue_many() / routes.run_queue(), which
space requests out at adsb.lol's ~1/s courtesy rate. This pins both without
hitting the network -- routes._fetch is monkeypatched.

    python3 prestoradar/dev/test_routes_queue.py
"""

import asyncio
import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes  # noqa: E402


def _eq(got, want, what):
    if got != want:
        raise AssertionError("%s: got %r, want %r" % (what, got, want))


def _reset():
    routes._cache.clear()
    routes._tries.clear()
    routes._pending.clear()


class _P:
    """The plane fields routes.enqueue_many() reads."""

    def __init__(self, callsign, hexid, e=0.0, n=0.0, heading=90.0):
        self.callsign = callsign
        self.hex = hexid
        self.e = e
        self.n = n
        self.heading = heading


def test_lru_bound():
    _reset()
    routes._CACHE_MAX = 3

    routes._cache_set("AAA", ("SFO", "JFK"))
    routes._cache_set("BBB", None)
    routes._tries["BBB"] = 2
    routes._cache_set("CCC", "")

    # A read of AAA counts as a use, so it should outlast BBB when the cap
    # is next exceeded.
    _eq(routes._cache_get("AAA"), ("SFO", "JFK"), "resolved route reads back")
    routes._cache_set("DDD", "")

    _eq("BBB" in routes._cache, False, "least-recently-used entry is evicted")
    _eq(routes._tries.get("BBB"), None, "_tries is pruned with the cache entry")
    _eq(set(routes._cache), {"AAA", "CCC", "DDD"}, "cap is held at _CACHE_MAX")
    _eq(routes._cache_get("AAA"), ("SFO", "JFK"),
        "a hot resolved route survives eviction")

    _eq(routes.get("never-seen"), "absent", "an unknown callsign still reads absent")


def test_enqueue_eligibility_and_reseed():
    _reset()

    ok = _P("SWA1", "a1b2c3")
    hex_only = _P("a1b2c4", "a1b2c4")          # callsign == hex -> not a route
    empty = _P("", "a1b2c5")
    resolved = _P("UAL2", "b0b0b0")
    routes._cache_set("UAL2", ("SFO", "JFK"))  # already known -> don't re-queue

    routes.enqueue_many([ok, hex_only, empty, resolved])
    _eq(set(routes._pending), {"SWA1"},
        "only the eligible, unresolved callsign is queued")

    # Re-seed: the next batch is the new priority set. A callsign no longer
    # visible is dropped rather than fetched late (UI-TRAILS.md #3).
    other = _P("DAL3", "c0c0c0")
    routes.enqueue_many([other, resolved])
    _eq(set(routes._pending), {"DAL3"},
        "a callsign absent from the next batch is dropped, a new one is added")


def test_run_queue_spaces_and_drains():
    _reset()
    calls = []
    orig_fetch = routes._fetch

    async def fake_fetch(cs, lat, lon, track):
        calls.append((cs, asyncio.get_event_loop().time()))
        routes._cache_set(cs, ("ORG", "DST"))
        routes._tries.pop(cs, None)

    async def drive():
        worker = asyncio.create_task(
            routes.run_queue(concurrency=1, min_interval=0.05))
        routes.enqueue_many([_P("AAA", "h1"), _P("BBB", "h2"), _P("CCC", "h3")])
        for _ in range(400):
            if all(isinstance(routes.get(c), tuple) for c in ("AAA", "BBB", "CCC")):
                break
            await asyncio.sleep(0.01)
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    routes._fetch = fake_fetch
    try:
        asyncio.run(drive())
    finally:
        routes._fetch = orig_fetch
        _reset()

    _eq([c[0] for c in calls], ["AAA", "BBB", "CCC"],
        "every queued callsign is fetched, in queue order")
    gaps = [calls[i + 1][1] - calls[i][1] for i in range(len(calls) - 1)]
    for g in gaps:
        if g < 0.045:
            raise AssertionError("requests not spaced by min_interval: gap %.3fs" % g)


def main():
    saved_max = routes._CACHE_MAX
    try:
        test_lru_bound()
        test_enqueue_eligibility_and_reseed()
        test_run_queue_spaces_and_drains()
    finally:
        routes._CACHE_MAX = saved_max
        _reset()
    print("routes.py: LRU + queue assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
