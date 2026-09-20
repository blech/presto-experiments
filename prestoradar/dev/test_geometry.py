#!/usr/bin/env python3
"""Desktop test for geometry.near_airport / geometry.ground_hidden -- the
shared airport-vicinity check (TODOS.md 2026-09-19: "on ground" incorrectly
catches a helicopter at FL0 not actually at an airport). No device."""

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


class _Basemap:
    def __init__(self, airports):
        self.AIRPORTS = airports


def test_near_airport():
    from geometry import near_airport

    bm = _Basemap([("SFO", 0.0, 0.0), ("OAK", 10.0, 5.0)])
    _eq(near_airport(1.0, 1.0, bm, radius_km=3), True, "within radius of SFO")
    _eq(near_airport(20.0, 20.0, bm, radius_km=3), False, "far from every airport")
    _eq(near_airport(0.0, 0.0, bm, radius_km=3), True, "exactly on an airport mark")

    _eq(near_airport(1.0, 1.0, _Basemap([]), radius_km=3), False,
        "no airports in the basemap -> False")
    _eq(near_airport(1.0, 1.0, None, radius_km=3), False,
        "no basemap at all -> False")


def test_ground_hidden_requires_both_flags():
    from geometry import ground_hidden

    bm = _Basemap([("SFO", 0.0, 0.0)])
    _eq(ground_hidden(on_ground=False, hide_on_ground=True, e=0.0, n=0.0,
                       basemap_data=bm, radius_km=3), False,
        "airborne -> never hidden regardless of the setting")
    _eq(ground_hidden(on_ground=True, hide_on_ground=False, e=0.0, n=0.0,
                       basemap_data=bm, radius_km=3), False,
        "setting off -> never hidden regardless of on_ground")


def test_ground_hidden_checks_airport_proximity_when_basemap_available():
    from geometry import ground_hidden

    bm = _Basemap([("SFO", 0.0, 0.0)])
    _eq(ground_hidden(on_ground=True, hide_on_ground=True, e=0.5, n=0.5,
                       basemap_data=bm, radius_km=3), True,
        "on_ground near a displayed airport -> hidden")
    _eq(ground_hidden(on_ground=True, hide_on_ground=True, e=50.0, n=50.0,
                       basemap_data=bm, radius_km=3), False,
        "on_ground far from every displayed airport (the hovering-helicopter "
        "case) -> not hidden")


def test_ground_hidden_falls_back_when_no_airports_to_check():
    from geometry import ground_hidden

    _eq(ground_hidden(on_ground=True, hide_on_ground=True, e=50.0, n=50.0,
                       basemap_data=None, radius_km=3), True,
        "no basemap loaded -> trust on_ground as before this fix")
    _eq(ground_hidden(on_ground=True, hide_on_ground=True, e=50.0, n=50.0,
                       basemap_data=_Basemap([]), radius_km=3), True,
        "basemap loaded but no airport marks -> trust on_ground as before")


def main():
    test_near_airport()
    test_ground_hidden_requires_both_flags()
    test_ground_hidden_checks_airport_proximity_when_basemap_available()
    test_ground_hidden_falls_back_when_no_airports_to_check()
    print("geometry.near_airport + geometry.ground_hidden: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
