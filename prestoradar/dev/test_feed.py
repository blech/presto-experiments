#!/usr/bin/env python3
"""Desktop test for feed.Feed.resolve() and the two pure helpers behind
DATA_TODOS.md #4 (carry-forward) and #5 (snapshot sanity guard) -- no
network, no device. The async fetch/parse pipeline these feed into is
verified on-device, same as traces.backfill()'s own network path."""

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
    def __init__(self, hex, missing_since=None):
        self.hex = hex
        self.missing_since = missing_since


def test_resolve():
    from feed import Feed
    f = Feed("host", "/path", "agent/1.0", 256, 30_000)
    plane = _P("aabbcc")
    f._by_hex = {"aabbcc": plane}

    _eq(f.resolve("aabbcc"), plane, "resolves a hex present in the current registry")
    _eq(f.resolve("ffffff"), None, "a hex not in the registry resolves to None")

    f._by_hex = {}   # simulates the aircraft dropping off the next fetch cycle
    _eq(f.resolve("aabbcc"), None, "a departed aircraft's hex no longer resolves")


def test_reject_snapshot():
    from feed import _reject_snapshot

    _eq(_reject_snapshot(40, 40, stale_cycles=0), False,
        "a same-sized fetch is never rejected")
    _eq(_reject_snapshot(20, 40, stale_cycles=0), False,
        "exactly half the previous count is accepted, not rejected")
    _eq(_reject_snapshot(19, 40, stale_cycles=0), True,
        "just under half the previous count is rejected")
    _eq(_reject_snapshot(0, 40, stale_cycles=0), True,
        "an empty snapshot is rejected")
    _eq(_reject_snapshot(0, 0, stale_cycles=0), False,
        "nothing to protect when the previous list was already empty")
    _eq(_reject_snapshot(5, 40, stale_cycles=3, max_stale_cycles=3), False,
        "anti-lockout: already stale for max_stale_cycles -> stop rejecting")
    _eq(_reject_snapshot(5, 40, stale_cycles=2, max_stale_cycles=3), True,
        "still short of max_stale_cycles -> keep rejecting")


def test_carry_forward():
    from feed import _carry_forward

    kept = _P("aabbcc")            # still present in the fresh response
    dropped_now = _P("bbccdd")     # missing for the first time this cycle
    already_missing = _P("ccddee", missing_since=10_000)  # well within the window
    expired = _P("ddeeff", missing_since=500)             # missing long enough to drop

    prev_by_hex = {"aabbcc": kept, "bbccdd": dropped_now,
                    "ccddee": already_missing, "ddeeff": expired}
    fresh_by_hex = {"aabbcc": kept}

    carried, dropped = _carry_forward(prev_by_hex, fresh_by_hex, now_ms=76_000,
                                       carry_forward_ms=75_000)

    _eq(dropped_now.missing_since, 76_000,
        "a newly-missing plane is timestamped with now")
    _eq(sorted(p.hex for p in carried), ["bbccdd", "ccddee"],
        "carries forward planes still inside the window, drops the expired one")
    _eq(expired.hex in [p.hex for p in carried], False,
        "a plane missing longer than carry_forward_ms is not carried forward")
    _eq(kept.missing_since, None,
        "a plane present in the fresh response is left alone (from_feed() clears it)")
    _eq(dropped, 1, "one candidate (the expired plane) fell outside the window")


def test_carry_forward_reports_zero_dropped_when_nothing_expires():
    from feed import _carry_forward

    p = _P("aabbcc")
    carried, dropped = _carry_forward({"aabbcc": p}, {}, now_ms=1_000,
                                       carry_forward_ms=75_000)
    _eq([q.hex for q in carried], ["aabbcc"], "still within the window")
    _eq(dropped, 0, "nothing expired this cycle")


def main():
    test_resolve()
    test_reject_snapshot()
    test_carry_forward()
    test_carry_forward_reports_zero_dropped_when_nothing_expires()
    print("feed.py: resolve() + _reject_snapshot() + _carry_forward(): all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
