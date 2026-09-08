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


def test_card_corner():
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


def test_blip_xy():
    # _blip_xy looks p up in last_drawn (set by draw_planes before the card)
    # and falls back to screen centre. Exercise it without constructing a
    # Renderer (that needs a display).
    import render

    r = object.__new__(render.Renderer)
    obj_a, obj_b, obj_c = object(), object(), object()
    r.last_drawn = [(100, 120, obj_a), (300, 40, obj_b)]
    _eq(r._blip_xy(obj_b), (300, 40), "blip found in last_drawn")
    _eq(r._blip_xy(obj_c), (240, 240), "blip not drawn -> centre fallback")


def main():
    test_card_corner()
    test_blip_xy()
    print("render._card_corner + _blip_xy: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
