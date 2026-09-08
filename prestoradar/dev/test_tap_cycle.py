#!/usr/bin/env python3
"""Desktop test for ui._advance_selection and render._data_block -- the
tap-cycle state machine and the ATC data-block formatter. No device."""

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
    def __init__(self, callsign="", hex="", alt=None, gs=None, type=None):
        self.callsign = callsign
        self.hex = hex
        self.alt = alt
        self.gs = gs
        self.type = type


def test_cycle():
    from ui import _advance_selection
    a, b = _P("AAA"), _P("BBB")

    _eq(_advance_selection(None, 1, None), (None, 1), "empty tap with nothing selected")
    _eq(_advance_selection(None, 1, a), (a, 1), "first select -> stage 1")
    _eq(_advance_selection(a, 1, a), (a, 2), "same plane -> stage 2")
    _eq(_advance_selection(a, 2, a), (a, 3), "same plane -> stage 3")
    _eq(_advance_selection(a, 3, a), (a, 1), "same plane wraps 3 -> 1")
    _eq(_advance_selection(a, 2, b), (b, 1), "different plane -> fresh stage 1")
    _eq(_advance_selection(a, 3, None), (None, 1), "empty tap dismisses, resets level")


def test_data_block():
    from render import _data_block

    _eq(_data_block(_P("BAW123", alt=12000, gs=280.0, type="A320")),
        ("BAW123", "FL120 280kt", "A320"), "typical airliner")
    _eq(_data_block(_P("N1", alt="ground", gs=0.0, type="C172")),
        ("N1", "GND 0kt", "C172"), "on the ground")
    _eq(_data_block(_P("MIL1", alt=None, gs=None, type=None)),
        ("MIL1", "--- 0kt", "?"), "missing altitude / speed / type")
    _eq(_data_block(_P(callsign="", hex="a0cb1c", alt=3000, gs=120.0, type="PA32")),
        ("a0cb1c", "FL030 120kt", "PA32"), "no callsign -> hex")


def main():
    test_cycle()
    test_data_block()
    print("ui._advance_selection + render._data_block: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
