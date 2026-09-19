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


def test_extract_aircraft():
    from feed import _extract_aircraft

    _eq(_extract_aircraft({"ac": [1, 2, 3]}, "ac"), [1, 2, 3],
        "the named key's list, unchanged")
    _eq(_extract_aircraft({"ac": None}, "ac"), [],
        "a null value at the key (adsb.lol's unhappy-backend response) -> []")
    _eq(_extract_aircraft({"aircraft": [1]}, "ac"), [],
        "the wrong key -> [] (adsb.fi's key is 'aircraft', not 'ac')")
    _eq(_extract_aircraft(None, "ac"), [], "no data at all -> []")


def test_combine_sources_primary_has_aircraft_fallback_never_tried():
    from feed import _combine_sources

    aircraft, ok = _combine_sources({"ac": [1, 2]}, [1, 2], False, None, [])
    _eq((aircraft, ok), ([1, 2], True), "primary's own result, fallback untouched")


def test_combine_sources_fallback_succeeds():
    from feed import _combine_sources

    # Primary failed outright (data=None); fallback attempted and returned
    # real aircraft.
    aircraft, ok = _combine_sources(None, [], True, {"aircraft": [9]}, [9])
    _eq((aircraft, ok), ([9], True), "the fallback's aircraft list is used")


def test_combine_sources_both_empty_but_responsive_is_not_a_failure():
    from feed import _combine_sources

    # Primary responded 200 with an empty list; fallback tried (since
    # primary was empty) and also genuinely empty -- both sources are up,
    # there's just nothing in range right now.
    aircraft, ok = _combine_sources({"ac": []}, [], True, {"aircraft": []}, [])
    _eq((aircraft, ok), ([], True), "empty-but-responsive is a real result, not a failure")


def test_combine_sources_fallback_attempted_and_also_fails_falls_back_to_primary():
    from feed import _combine_sources

    # Primary gave a valid (if empty) response; fallback was tried and
    # failed outright (fallback_data=None) -- keep the primary's legitimate
    # empty result rather than reporting total failure.
    aircraft, ok = _combine_sources({"ac": []}, [], True, None, [])
    _eq((aircraft, ok), ([], True), "primary's empty-but-valid result survives a dead fallback")


def test_combine_sources_both_sources_fail_outright():
    from feed import _combine_sources

    aircraft, ok = _combine_sources(None, [], True, None, [])
    _eq((aircraft, ok), ([], False), "neither source produced a parseable response at all")


def test_backoff_interval():
    from feed import _backoff_interval

    _eq(_backoff_interval(0, 30_000, threshold=3, cooldown_ms=300_000), 30_000,
        "no failures -> the normal interval")
    _eq(_backoff_interval(2, 30_000, threshold=3, cooldown_ms=300_000), 30_000,
        "short of the threshold -> still normal")
    _eq(_backoff_interval(3, 30_000, threshold=3, cooldown_ms=300_000), 300_000,
        "at the threshold -> the cooldown")
    _eq(_backoff_interval(9, 30_000, threshold=3, cooldown_ms=300_000), 300_000,
        "well past the threshold -> still the cooldown, not escalating further")


def main():
    test_resolve()
    test_reject_snapshot()
    test_carry_forward()
    test_carry_forward_reports_zero_dropped_when_nothing_expires()
    test_extract_aircraft()
    test_combine_sources_primary_has_aircraft_fallback_never_tried()
    test_combine_sources_fallback_succeeds()
    test_combine_sources_both_empty_but_responsive_is_not_a_failure()
    test_combine_sources_fallback_attempted_and_also_fails_falls_back_to_primary()
    test_combine_sources_both_sources_fail_outright()
    test_backoff_interval()
    print("feed.py: resolve() + _reject_snapshot() + _carry_forward() + "
          "adsb.fi fallback helpers: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
