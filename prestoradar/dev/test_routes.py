#!/usr/bin/env python3
"""Desktop test for routes.py's bounded cache -- eviction order and the
touch-on-get recency bump (DATA_TODOS.md #3). No network, no device."""

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


def _reset():
    import routes
    routes._cache.clear()
    routes._tries.clear()
    return routes


def test_evict_oldest_pops_the_first_inserted():
    routes = _reset()
    routes._cache.update({"AAA": None, "BBB": None, "CCC": None})
    routes._tries.update({"AAA": 1, "BBB": 2})
    routes._evict_oldest()
    _eq(list(routes._cache.keys()), ["BBB", "CCC"], "oldest key is dropped from _cache")
    _eq(list(routes._tries.keys()), ["BBB"], "the paired _tries entry is dropped too")


def test_evict_oldest_on_empty_cache_is_a_noop():
    routes = _reset()
    routes._evict_oldest()   # must not raise
    _eq(routes._cache, {}, "still empty")


def test_touch_moves_to_the_end():
    routes = _reset()
    routes._cache.update({"AAA": None, "BBB": None, "CCC": None})
    routes._touch("AAA")
    _eq(list(routes._cache.keys()), ["BBB", "CCC", "AAA"], "touched key moves to the end")


def test_touch_of_a_missing_key_is_a_noop():
    routes = _reset()
    routes._cache["AAA"] = None
    routes._touch("ZZZ")
    _eq(list(routes._cache.keys()), ["AAA"], "unaffected")


def test_get_touches_the_key():
    routes = _reset()
    routes._cache.update({"AAA": None, "BBB": ("LHR", "JFK")})
    routes.get("AAA")
    _eq(list(routes._cache.keys()), ["BBB", "AAA"], "get() bumps the read key to the end")


def test_request_evicts_before_adding_past_capacity():
    # request()'s eviction check runs before it fires the async lookup
    # (asyncio.create_task), which needs a running event loop this desktop
    # test doesn't have -- same reason test_feed.py/test_traces.py never
    # exercise their own real network paths directly. Stub create_task to a
    # no-op so only the synchronous cache bookkeeping under test runs.
    routes = _reset()
    from plane import Plane

    for i in range(routes._MAX_ENTRIES):
        routes._cache["CS%03d" % i] = None
    _eq(len(routes._cache), routes._MAX_ENTRIES, "cache pre-filled to capacity")

    def _no_op_create_task(coro):
        coro.close()   # avoid "coroutine was never awaited" noise
        return None

    orig_create_task = routes.asyncio.create_task
    routes.asyncio.create_task = _no_op_create_task
    try:
        p = Plane()
        p.callsign = "NEWCS1"
        p.hex = "abc123"
        p.e, p.n, p.heading = 0.0, 0.0, 90.0
        routes.request(p)
    finally:
        routes.asyncio.create_task = orig_create_task

    _eq(len(routes._cache), routes._MAX_ENTRIES, "adding one more stays at the cap")
    _eq("CS000" in routes._cache, False, "the oldest entry was evicted to make room")
    _eq(routes._cache.get("NEWCS1"), "", "the new callsign is now pending")


class _Basemap:
    def __init__(self, airports):
        self.AIRPORTS = airports


def test_near_displayed_airport_delegates_to_geometry():
    # _near_displayed_airport() is now a thin wrapper over the shared
    # geometry.near_airport() primitive -- this locks in the delegation
    # (radius, projection) rather than re-testing near_airport() itself
    # (see dev/test_geometry.py for that).
    import routes
    orig_basemap = routes.basemap_data
    try:
        routes.basemap_data = _Basemap([("SFO", 0.0, 0.0)])
        lat, lon = 37.74, -122.42   # settings_example.py's CENTER_LAT/LON -- e=n=0
        _eq(routes._near_displayed_airport(lat, lon), True,
            "the radar centre projects to (0, 0), on top of the airport mark")
        _eq(routes._near_displayed_airport(lat + 5, lon), False,
            "5 degrees of latitude is far outside NEAR_AIRPORT_KM (30 km)")

        routes.basemap_data = None
        _eq(routes._near_displayed_airport(lat, lon), False, "no basemap -> False")
    finally:
        routes.basemap_data = orig_basemap


def main():
    test_evict_oldest_pops_the_first_inserted()
    test_evict_oldest_on_empty_cache_is_a_noop()
    test_touch_moves_to_the_end()
    test_touch_of_a_missing_key_is_a_noop()
    test_get_touches_the_key()
    test_request_evicts_before_adding_past_capacity()
    test_near_displayed_airport_delegates_to_geometry()
    print("routes.py: cache cap + touch + near_displayed_airport: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
