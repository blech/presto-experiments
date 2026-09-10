#!/usr/bin/env python3
"""
Desktop (CPython) test for nearby.py's classification core -- no WiFi, no device.

`nearby.bucket()` sorts a live plane list into five independent sections:
departures and landings for each nearby airport, plus everything close to the
radar centre. The heuristic (terminal-area radius + altitude ceiling + vertical
state + heading toward/away from the field) is the part worth pinning down;
this exercises it against hand-placed aircraft so a drift in the thresholds or
the bearing maths fails here rather than on a glance at the board.

    python3 prestoradar/dev/test_nearby.py
"""

import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import geometry  # noqa: E402
import nearby  # noqa: E402

# KSFO and KOAK, roughly. settings_example.py centres on 37.74 / -122.42, so
# both fields sit inside a 30 km radar frame and inside each other's terminal
# area -- the case the heuristic has to disentangle.
_SFO = (37.6189, -122.3750)
_OAK = (37.7213, -122.2197)


def _eq(got, want, what):
    if got != want:
        raise AssertionError("%s: got %r, want %r" % (what, got, want))


def _labels(planes):
    return [p.callsign for p in planes]


class _FakePlane:
    """Just the fields nearby.bucket() reads -- e/n (km from centre), alt,
    vstate, heading, dst (nm from centre), on_ground."""

    def __init__(self, callsign, e, n, alt, vstate, heading, dst, on_ground=False):
        self.callsign = callsign
        self.e = e
        self.n = n
        self.alt = alt
        self.vstate = vstate
        self.heading = heading
        self.dst = dst
        self.on_ground = on_ground


def main():
    airports = {
        "KSFO": geometry.project(*_SFO),
        "KOAK": geometry.project(*_OAK),
    }
    se, sn = airports["KSFO"]
    oe, on = airports["KOAK"]
    cfg = nearby.Config(terminal_nm=12.0, phase_ceil_ft=8000,
                        heading_tol=70.0, near_nm=5.0, include_ground=False)

    # A climb 3 km due north of SFO, tracking north -- straight off the field
    # and pulling away from it.
    dep_sfo = _FakePlane("SWA100", se, sn + 3.0, 3500, "climb", 0.0, 8.0)
    # A descent 5 km due west of OAK, tracking east -- lined up on the field.
    arr_oak = _FakePlane("UAL200", oe - 5.0, on, 2500, "descent", 90.0, 9.0)
    # Directly over SFO but at cruise: an overflight, not a movement here.
    overflight = _FakePlane("ACA300", se, sn, 30000, "climb", 0.0, 8.0)
    # Sitting on the SFO ramp.
    parked = _FakePlane("N400", se + 1.0, sn, "ground", "level", 0.0, 7.6,
                        on_ground=True)
    # Two contacts near the centre, given out of order to check the sort.
    near_far = _FakePlane("HOP500", 2.0, 2.0, 4000, "level", 270.0, 2.4)
    near_close = _FakePlane("HOP600", 0.5, -0.5, 3800, "level", 270.0, 0.6)
    # Over the centre, climbing north: close enough to the centre AND inside
    # SFO's terminal area on the right heading -- must land in both sections.
    both = _FakePlane("JBU700", 0.0, 0.0, 3000, "climb", 0.0, 0.4)

    planes = [dep_sfo, arr_oak, overflight, parked, near_far, near_close, both]
    out = nearby.bucket(planes, airports, cfg)

    _eq(_labels(out["airports"]["KSFO"]["departures"]), ["SWA100", "JBU700"],
        "SFO departures: the climb off the field and the one over the centre")
    _eq(_labels(out["airports"]["KSFO"]["landings"]), [],
        "nothing is landing at SFO")
    _eq(_labels(out["airports"]["KOAK"]["departures"]), [],
        "nothing is departing OAK")
    _eq(_labels(out["airports"]["KOAK"]["landings"]), ["UAL200"],
        "OAK landings: the descent lined up from the west")
    _eq(_labels(out["near"]), ["JBU700", "HOP600", "HOP500"],
        "near-centre section is closest-first by dst")

    # Independence: the over-the-centre climb is in two sections at once.
    _eq("JBU700" in _labels(out["airports"]["KSFO"]["departures"])
        and "JBU700" in _labels(out["near"]), True,
        "a plane can appear in more than one section")

    # The cruise overflight and the parked aircraft are in none of them.
    for section in (out["airports"]["KSFO"]["departures"],
                    out["airports"]["KSFO"]["landings"],
                    out["airports"]["KOAK"]["departures"],
                    out["airports"]["KOAK"]["landings"],
                    out["near"]):
        _eq("ACA300" in _labels(section), False, "overflight is filtered out")
        _eq("N400" in _labels(section), False, "parked aircraft is filtered out")

    # include_ground lets ramp traffic through the ground filter (it still
    # needs a numeric altitude to count as a movement, so "ground" alt keeps
    # it out of the airport buckets, but a low taxiing-out contact would show).
    taxi = _FakePlane("N800", se + 1.0, sn + 1.0, 50, "climb", 20.0, 7.6,
                      on_ground=True)
    cfg_ground = cfg._replace(include_ground=True)
    out2 = nearby.bucket([taxi], airports, cfg_ground)
    _eq(_labels(out2["airports"]["KSFO"]["departures"]), ["N800"],
        "include_ground admits a low climbing contact at the field")
    out3 = nearby.bucket([taxi], airports, cfg)
    _eq(_labels(out3["airports"]["KSFO"]["departures"]), [],
        "default config keeps ground contacts out")

    # A descent close to SFO but tracking away from it is not a landing there.
    wrong_way = _FakePlane("DAL900", se, sn + 4.0, 3000, "descent", 0.0, 8.0)
    out4 = nearby.bucket([wrong_way], airports, cfg)
    _eq(_labels(out4["airports"]["KSFO"]["landings"]), [],
        "a descent heading away from the field is not a landing there")

    print("nearby.py: all classification assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
