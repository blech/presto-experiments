#!/usr/bin/env python3
"""Desktop test for render._trace_pen_index -- the oldest->newest pen ramp
mapping for the selected-aircraft trail. No device, no PicoGraphics."""

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
    # Import the pure helper without constructing a Renderer (that needs a
    # display). render.py imports `traces` and `from backdrop import
    # _clip_segment`, both import-safe on CPython, so `import render` works.
    from render import _trace_pen_index

    _eq(_trace_pen_index(0, 10, 4), 0, "oldest segment -> pen 0")
    _eq(_trace_pen_index(9, 10, 4), 3, "newest segment -> last pen")
    _eq(_trace_pen_index(5, 10, 4), 2, "midway")
    _eq(_trace_pen_index(0, 1, 4), 3, "single segment -> brightest")
    _eq(_trace_pen_index(0, 0, 4), 3, "no segments -> brightest (guard)")
    # Never out of range, for any split.
    for n in range(1, 60):
        for i in range(n):
            idx = _trace_pen_index(i, n, 4)
            if not (0 <= idx <= 3):
                raise AssertionError("out of range: n=%d i=%d -> %d" % (n, i, idx))

    print("render._trace_pen_index: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
