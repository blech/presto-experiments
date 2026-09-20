#!/usr/bin/env python3
"""Desktop test for Renderer._ambient_label_set -- the greedy nearest-centre
label cull. Stubs the display so no PicoGraphics is needed."""

import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class _Plane:
    def __init__(self, callsign, heading=None, gs=0):
        self.callsign = callsign
        self.heading = heading
        self.gs = gs


def _eq(got, want, what):
    if got != want:
        raise AssertionError("%s: got %r, want %r" % (what, got, want))


def _make_renderer():
    # Build a Renderer without running __init__ (it needs a display); we only
    # exercise the pure-geometry methods, which touch no instance state.
    import render
    return object.__new__(render.Renderer)


def main():
    r = _make_renderer()

    # Two blips far apart -> both labelled.
    far = [(50, 50, _Plane("AAA111")), (400, 400, _Plane("BBB222"))]
    _eq(r._ambient_label_set(far), {0, 1}, "well-separated tags both kept")

    # Three blips stacked on the same y within a few px -> only the
    # nearest-to-centre (240, 240) survives.
    cx = 240
    stacked = [
        (cx + 0, 240, _Plane("NEAR00")),     # closest to centre
        (cx + 6, 240, _Plane("MID666")),     # box overlaps NEAR00
        (cx + 12, 240, _Plane("FAR121")),    # box overlaps NEAR00
    ]
    _eq(r._ambient_label_set(stacked), {0}, "overlapping stack culled to the closest")

    # A blank callsign is never placed and never blocks a later one.
    mixed = [(100, 100, _Plane("")), (108, 100, _Plane("REALCS"))]
    _eq(r._ambient_label_set(mixed), {1}, "blank callsign skipped, real one kept")

    # Two blips at the exact same point would collide under the fixed
    # (x+8, y-8) anchor -- but AAA111 heads NE (a tick is drawn, so its tag
    # flips below the blip, TODOS.md 2026-09-19) while BBB222 has no heading
    # (default anchor, above). The two boxes now occupy different vertical
    # bands, so both survive the cull instead of one culling the other.
    same_spot = [
        (cx, cx, _Plane("AAA111", heading=45, gs=100)),
        (cx, cx, _Plane("BBB222", heading=None, gs=0)),
    ]
    _eq(r._ambient_label_set(same_spot), {0, 1},
        "heading-flipped anchor avoids a same-point collision with a default-anchor tag")

    print("Renderer._ambient_label_set: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
