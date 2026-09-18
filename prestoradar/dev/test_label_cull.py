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
    def __init__(self, callsign):
        self.callsign = callsign


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

    print("Renderer._ambient_label_set: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
