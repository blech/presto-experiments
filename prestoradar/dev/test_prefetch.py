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
    def __init__(self, name="", traced=False, on_ground=False):
        self.name = name
        self.traced = traced
        self.on_ground = on_ground

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


def main():
    test_hit_test()
    test_enqueue_eligible()
    print("ui.hit_test + ui._enqueue_eligible: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
