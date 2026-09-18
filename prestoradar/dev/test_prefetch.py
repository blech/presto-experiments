#!/usr/bin/env python3
"""Desktop test for ui.hit_test and ui._nearest_visible -- the two pure
helpers behind trace prefetch (touch-down + nearest-to-centre). No device."""

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
    def __init__(self, name="", dst=None, on_ground=False):
        self.name = name
        self.dst = dst
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
    # Exactly two candidates equidistant from the tap: first-seen wins (strict
    # '<' comparison), matching the pre-extraction loop's behaviour.
    tie_a, tie_b = _P("TIE_A"), _P("TIE_B")
    _eq(hit_test([(100, 100, tie_a), (100, 100, tie_b)], 100, 100, hit_radius=26),
        tie_a, "tie keeps the first candidate")


def test_nearest_visible():
    from ui import _nearest_visible
    hidden = lambda p: p.on_ground  # noqa: E731

    far, near = _P("FAR", dst=40), _P("NEAR", dst=5)
    _eq(_nearest_visible([far, near], hidden), near, "picks the smallest dst")
    _eq(_nearest_visible([], hidden), None, "no planes -> None")

    grounded_near = _P("GROUNDED", dst=1, on_ground=True)
    _eq(_nearest_visible([far, grounded_near], hidden), far,
        "a closer but hidden plane is skipped")

    no_dst = _P("NODST", dst=None)
    _eq(_nearest_visible([no_dst, far], hidden), far,
        "a plane with dst=None is skipped, not treated as nearest")

    _eq(_nearest_visible([no_dst], hidden), None,
        "every candidate lacking dst -> None")


def main():
    test_hit_test()
    test_nearest_visible()
    print("ui.hit_test + ui._nearest_visible: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
