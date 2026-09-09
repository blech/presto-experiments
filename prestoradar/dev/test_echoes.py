#!/usr/bin/env python3
"""Desktop test for render._echo_marks / _echo_radius -- the trailing-dot
selection and sizing for radar echoes. No device, no PicoGraphics."""

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
    from render import _echo_marks, _echo_radius

    # _echo_marks: last `count` fixes, oldest->newest, excluding the most recent.
    t = [(0.0, 0.0, 100), (1.0, 1.0, 200), (2.0, 2.0, 300),
         (3.0, 3.0, 400), (4.0, 4.0, 500)]
    _eq(_echo_marks(t, 3), [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)],
        "3 echoes are trail[-4:-1] as (e, n)")
    _eq(_echo_marks(t, 2), [(2.0, 2.0), (3.0, 3.0)], "2 echoes")
    _eq(_echo_marks([(0.0, 0.0, 1), (1.0, 1.0, 2)], 3), [(0.0, 0.0)],
        "len 2 -> one echo (the older fix)")
    _eq(_echo_marks([(0.0, 0.0, 1)], 3), [], "len 1 -> no echoes")
    _eq(_echo_marks([], 3), [], "empty trail -> no echoes")

    # _echo_radius: newest dot r2, the rest r1.
    _eq(_echo_radius(0, 3), 1, "oldest of 3 -> r1")
    _eq(_echo_radius(1, 3), 1, "middle of 3 -> r1")
    _eq(_echo_radius(2, 3), 2, "newest of 3 -> r2")
    _eq(_echo_radius(0, 1), 2, "single echo -> r2")

    print("render._echo_marks + _echo_radius: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
