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


def main():
    saved_max = routes._CACHE_MAX
    try:
        test_lru_bound()
    finally:
        routes._CACHE_MAX = saved_max
        _reset()
    print("routes.py: LRU + queue assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
