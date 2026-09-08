#!/usr/bin/env python3
"""Desktop test for render._card_corner -- which screen corner the detail
card goes in, given the selected blip's position. No device."""

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
    from render import _card_corner, _CARD_W, _CARD_H

    W = H = 480
    tl = (4, 4)
    tr = (W - _CARD_W - 4, 4)
    bl = (4, H - _CARD_H - 4)
    br = (W - _CARD_W - 4, H - _CARD_H - 4)

    _eq(_card_corner(400, 100), bl, "blip top-right -> card bottom-left")
    _eq(_card_corner(100, 100), br, "blip top-left -> card bottom-right")
    _eq(_card_corner(400, 400), tl, "blip bottom-right -> card top-left")
    _eq(_card_corner(100, 400), tr, "blip bottom-left -> card top-right")
    _eq(_card_corner(240, 240), br, "blip dead centre -> a defined corner")

    print("render._card_corner: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
