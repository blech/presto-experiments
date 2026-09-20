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


def _run_backfill(response, plane):
    """Drive traces.backfill() on CPython with net.http_get stubbed."""
    import asyncio
    import traces

    async def fake_get(host, path, user_agent, **kw):
        if isinstance(response, Exception):
            raise response
        return response

    orig = traces.net.http_get
    traces.net.http_get = fake_get
    try:
        asyncio.run(traces.backfill(plane))
    finally:
        traces.net.http_get = orig


class _HexPlane(_P):
    def __init__(self, trail):
        _P.__init__(self, trail)
        self.hex = "ABC123"


def test_backfill_splices_a_gzipped_trace():
    import gzip
    import json
    import settings

    trace = {"timestamp": 1000.0, "trace": [
        [i * 3, settings.CENTER_LAT + i * 0.001, settings.CENTER_LON, 5000 + i * 10,
         200, 90, 0, 0, None] for i in range(10)]}
    p = _HexPlane(trail=[(0.0, 0.0, 5000)])
    _run_backfill((200, gzip.compress(json.dumps(trace).encode())), p)
    _eq(len(p.trail), 10, "the 10 trace points replace the 1 live fix")
    _eq(p.traced, True, "traced is set")


def test_backfill_failure_leaves_the_trail_and_marks_traced():
    p = _HexPlane(trail=[(0.0, 0.0, 5000)])
    _run_backfill((404, b""), p)
    _eq(p.trail, [(0.0, 0.0, 5000)], "a 404 leaves the trail alone")
    _eq(p.traced, True, "still marked traced -- never retried")

    p2 = _HexPlane(trail=[(0.0, 0.0, 5000)])
    _run_backfill(OSError("boom"), p2)
    _eq(p2.traced, True, "a network error is also terminal")


def main():
    test_backfill_splices_a_gzipped_trace()
    test_backfill_failure_leaves_the_trail_and_marks_traced()
    test_apply_replaces_on_a_good_result()
    test_apply_leaves_trail_on_a_short_or_missing_result()
    test_points_for()
    print("traces._apply + traces.points_for: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
