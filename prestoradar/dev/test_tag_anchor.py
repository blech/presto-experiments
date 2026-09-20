#!/usr/bin/env python3
"""Desktop test for render._tag_anchor_dy -- flips a radar-mode tag/data-
block anchor below the blip when its direction tick would otherwise run
through it (TODOS.md 2026-09-19: collision depends on heading, not screen
position). No device."""

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
    from render import _tag_anchor_dy

    # The 0-150 arc is treated as a block, not pixel-fitted to the tick's
    # exact 18px reach at every angle -- over-flipping at the arc's edges is
    # harmless (the flipped anchor is an equally valid, still-clear spot),
    # so simplicity wins over chasing the precise geometric boundary. 150 is
    # a deliberate margin past the ~134 deg the geometry alone requires (18px
    # tick, 8px box offset, +-8px box height) -- confirmed too narrow
    # on-device at 90 (radar-20260919-2005.png, LOT37).
    _eq(_tag_anchor_dy(True, 0), 8, "heading due north: within the arc -> flip below")
    _eq(_tag_anchor_dy(True, 45), 8, "heading NE: tick would run through the default anchor -> flip below")
    _eq(_tag_anchor_dy(True, 90), 8, "heading due east: still in the colliding arc")
    _eq(_tag_anchor_dy(True, 100), 8, "heading ESE (the on-device LOT37 case): still colliding")
    _eq(_tag_anchor_dy(True, 150), 8, "heading 150: still within the widened arc")
    _eq(_tag_anchor_dy(True, 151), -8, "just past the widened arc -> default anchor")
    _eq(_tag_anchor_dy(True, 180), -8, "heading south: default anchor")
    _eq(_tag_anchor_dy(True, 270), -8, "heading west: default anchor")
    _eq(_tag_anchor_dy(False, 45), -8, "no tick drawn -- nothing to collide with, default anchor")
    _eq(_tag_anchor_dy(True, None), -8, "no known heading -- no tick drawn, default anchor")

    print("render._tag_anchor_dy: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
