#!/usr/bin/env python3
"""
Desktop (CPython) test for routes.py's LRU cache bound and its batch-use
building blocks -- no WiFi, no device.

routes._cache / _tries were unbounded: one entry per callsign ever looked
up (DATA_TODOS.md #3). A list view touching ~30 callsigns a cycle makes
that acute, so the cache is now an LRU capped at routes._CACHE_MAX.

Pacing a batch of lookups used to be routes.py's own job
(enqueue_many()/run_queue()); that's now the caller's fetchqueue.Queue (see
nearby.py) -- routes.py only offers eligible()/fetch_for(), the two
building blocks a queue's enqueue/process_one needs. This pins those
without hitting the network -- routes._fetch is monkeypatched.

    python3 prestoradar/dev/test_routes_batch.py
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


class _P:
    """The plane fields routes.eligible()/fetch_for() read."""

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


def test_eligible():
    _reset()
    _eq(routes.eligible(_P("SWA1", "a1b2c3")), True,
        "a real, never-looked-up callsign is eligible")
    _eq(routes.eligible(_P("a1b2c4", "a1b2c4")), False,
        "callsign == hex (no real callsign broadcast) is never eligible")
    _eq(routes.eligible(_P("", "a1b2c5")), False, "an empty callsign is never eligible")

    routes._cache_set("UAL2", ("SFO", "JFK"))
    _eq(routes.eligible(_P("UAL2", "b0b0b0")), False, "an already-resolved route is not eligible")

    routes._cache_set("DAL3", "")
    _eq(routes.eligible(_P("DAL3", "c0c0c0")), False, "a fetch already in flight is not eligible")

    routes._cache_set("SKW4", None)
    routes._tries["SKW4"] = routes._MAX_TRIES
    _eq(routes.eligible(_P("SKW4", "d0d0d0")), False,
        "an unresolved route that's used up its retries is not eligible")

    routes._cache_set("SKW5", None)
    routes._tries["SKW5"] = routes._MAX_TRIES - 1
    _eq(routes.eligible(_P("SKW5", "e0e0e0")), True,
        "an unresolved route with retries left is still eligible")


def test_fetch_for():
    _reset()
    calls = []
    orig_fetch = routes._fetch

    async def fake_fetch(cs, lat, lon, track):
        calls.append((cs, lat, lon, track))
        routes._cache_set(cs, ("ORG", "DST"))
        routes._tries.pop(cs, None)

    routes._fetch = fake_fetch
    try:
        p = _P("SWA1", "a1b2c3", e=10.0, n=-5.0, heading=270.0)
        asyncio.run(routes.fetch_for(p))
        _eq(len(calls), 1, "fetch_for awaits exactly one fetch for an eligible plane")
        _eq(calls[0][0], "SWA1", "fetch_for passes the plane's callsign")
        _eq(calls[0][3], 270.0, "fetch_for passes the plane's heading as track")
        _eq(routes.get("SWA1"), ("ORG", "DST"), "the fetch's result lands in the cache")

        # Already resolved (by the call above) -> a second fetch_for is a no-op.
        asyncio.run(routes.fetch_for(p))
        _eq(len(calls), 1, "fetch_for on an already-resolved plane makes no fetch")

        # Never a real callsign -> also a no-op.
        asyncio.run(routes.fetch_for(_P("a1b2c4", "a1b2c4")))
        _eq(len(calls), 1, "fetch_for on a hex-only callsign makes no fetch")
    finally:
        routes._fetch = orig_fetch
        _reset()


def main():
    saved_max = routes._CACHE_MAX
    try:
        test_lru_bound()
        test_eligible()
        test_fetch_for()
    finally:
        routes._CACHE_MAX = saved_max
        _reset()
    print("routes.py: LRU + batch-lookup assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
