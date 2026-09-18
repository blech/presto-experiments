#!/usr/bin/env python3
"""Desktop test for ui.hit_test (used by handle_tap) and
ui._enqueue_eligible (the trace-backfill enqueue decision). No device."""

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
    def __init__(self, name="", traced=False, on_ground=False, dst=5.0, hex="abc123"):
        self.name = name
        self.traced = traced
        self.on_ground = on_ground
        self.dst = dst
        self.hex = hex
        self.label = name or hex

    def __repr__(self):
        return "<_P %s>" % self.name


def test_hit_test():
    from ui import hit_test
    a, b = _P("AAA"), _P("BBB")
    last_drawn = [(100, 100, a), (200, 200, b)]

    _eq(hit_test(last_drawn, 105, 102, hit_radius=26), a, "hits the nearer plane")
    _eq(hit_test(last_drawn, 205, 198, hit_radius=26), b, "hits the other plane")
    _eq(hit_test(last_drawn, 400, 400, hit_radius=26), None, "nothing within radius")
    _eq(hit_test([], 100, 100, hit_radius=26), None, "empty last_drawn -> None")
    tie_a, tie_b = _P("TIE_A"), _P("TIE_B")
    _eq(hit_test([(100, 100, tie_a), (100, 100, tie_b)], 100, 100, hit_radius=26),
        tie_a, "tie keeps the first candidate")


def test_enqueue_eligible():
    from ui import _enqueue_eligible

    _eq(_enqueue_eligible(_P()), True, "never-attempted + airborne -> eligible")
    _eq(_enqueue_eligible(_P(on_ground=True)), False, "grounded -> not eligible")
    _eq(_enqueue_eligible(_P(traced="pending")), False, "already pending -> not eligible")
    _eq(_enqueue_eligible(_P(traced=True)), False, "already done -> not eligible")
    _eq(_enqueue_eligible(_P(dst=None)), False,
        "a plane with dst=None is skipped, not treated as eligible")


class _Stub:
    """Trivial stand-in for a UI.__init__ dependency that on_feed_update
    never touches (self.selected stays None, the default) -- same pattern
    test_view_fixed.py uses to construct a real UI off-device."""

    def __getattr__(self, _n):
        return _Stub()

    def __call__(self, *a, **k):
        return None


class _StubQueue:
    """Records enqueue(key, priority) calls instead of actually queuing
    anything, so on_feed_update's enqueue loop can be checked without a
    real fetchqueue.Queue or asyncio."""

    def __init__(self):
        self.calls = []

    def enqueue(self, key, priority):
        self.calls.append((key, priority))


def test_on_feed_update_enqueues():
    """Exercises UI.on_feed_update() itself (not just the _enqueue_eligible
    predicate it calls) -- the gap that let Fix 1's dst=None crash slip
    past review: a real UI instance, a stub trace_queue, and a fresh-list
    with one eligible and one ineligible plane."""
    import ui

    tq = _StubQueue()
    u = ui.UI(_Stub(), _Stub(), _Stub(), lambda p: False, lambda: None,
              26, tq, _Stub(), _Stub(), 0, 0)

    eligible = _P("EL1", dst=12.0, hex="eligible1")
    grounded = _P("GR1", on_ground=True, dst=3.0, hex="grounded1")

    u.on_feed_update([eligible, grounded])

    _eq(eligible.traced, "pending", "the eligible plane's traced flips to pending")
    _eq(tq.calls, [("eligible1", 12.0)],
        "exactly one enqueue call, for the eligible plane's own hex/dst")
    _eq(grounded.traced, False, "the ineligible plane's traced is left untouched")


def main():
    test_hit_test()
    test_enqueue_eligible()
    test_on_feed_update_enqueues()
    print("ui.hit_test + ui._enqueue_eligible: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
