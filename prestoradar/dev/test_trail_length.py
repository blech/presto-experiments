#!/usr/bin/env python3
"""Desktop test for render._trail_cap -- the draw-time cap on the
selected-aircraft trail. No device."""

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


def main():
    from render import _trail_cap

    pts = list(range(10))
    _eq(_trail_cap(pts, 3), [7, 8, 9], "keeps the last 3")
    _eq(_trail_cap(pts, 20), pts, "limit above length -> unchanged")
    _eq(_trail_cap(pts, 10), pts, "limit == length -> unchanged")
    _eq(_trail_cap(pts, 0), [], "0 -> nothing")
    _eq(_trail_cap(pts, -5), [], "negative -> nothing")
    _eq(_trail_cap(pts, None), pts, "None -> unchanged")

    print("render._trail_cap: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
