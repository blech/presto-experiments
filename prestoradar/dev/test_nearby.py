#!/usr/bin/env python3
"""
Desktop (CPython) test for nearby.py's classification core -- no WiFi, no device.

`nearby.bucket()` sorts a live plane list into five independent sections of
`Row(plane, dist_nm)` -- departures and landings for each nearby airport
(dist_nm is to that field), plus everything close to the radar centre
(dist_nm is to the centre). The heuristic (terminal-area radius + altitude
ceiling + vertical state + heading toward/away from the field) is the part
worth pinning down; this exercises it against hand-placed aircraft so a drift
in the thresholds or the bearing maths fails here rather than on a glance at
the board.

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


def _close(got, want, what, tol=1e-3):
    if abs(got - want) > tol:
        raise AssertionError("%s: got %r, want %r (tol %g)" % (what, got, want, tol))


def _labels(rows):
    return [row.plane.callsign for row in rows]


def _row(rows, callsign):
    for row in rows:
        if row.plane.callsign == callsign:
            return row
    raise AssertionError("%s not in section" % callsign)


class _FakePlane:
    """Just the fields nearby.bucket() reads -- e/n (km from centre), alt,
    vstate, heading, dst / dir (range and bearing from centre), on_ground."""

    def __init__(self, callsign, e, n, alt, vstate, heading, dst,
                 on_ground=False, direction=None):
        self.callsign = callsign
        self.e = e
        self.n = n
        self.alt = alt
        self.vstate = vstate
        self.heading = heading
        self.dst = dst
        self.dir = direction
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
    # Two descents tracking east onto OAK, one closer to the field than the
    # other -- listed nearest-the-runway first.
    arr_oak = _FakePlane("UAL200", oe - 5.0, on, 2500, "descent", 90.0, 9.0)
    arr_oak_far = _FakePlane("UAL201", oe - 9.0, on, 4200, "descent", 90.0, 12.0)
    # Directly over SFO but at cruise: an overflight, not a movement here.
    overflight = _FakePlane("ACA300", se, sn, 30000, "climb", 0.0, 8.0)
    # Sitting on the SFO ramp.
    parked = _FakePlane("N400", se + 1.0, sn, "ground", "level", 0.0, 7.6,
                        on_ground=True)
    # Two contacts near the centre, given out of order to check the sort.
    near_far = _FakePlane("HOP500", 2.0, 2.0, 4000, "level", 270.0, 2.4,
                          direction=123.0)
    near_close = _FakePlane("HOP600", 0.5, -0.5, 3800, "level", 270.0, 0.6)
    # Over the centre, climbing north: close enough to the centre AND inside
    # SFO's terminal area on the right heading -- must land in both sections.
    both = _FakePlane("JBU700", 0.0, 0.0, 3000, "climb", 0.0, 0.4)

    planes = [dep_sfo, arr_oak, arr_oak_far, overflight, parked,
              near_far, near_close, both]
    out = nearby.bucket(planes, airports, cfg)

    _eq(_labels(out["airports"]["KSFO"]["departures"]), ["JBU700", "SWA100"],
        "SFO departures are furthest-first -- a new takeoff drops in at the bottom")
    _eq(_labels(out["airports"]["KSFO"]["landings"]), [],
        "nothing is landing at SFO")
    _eq(_labels(out["airports"]["KOAK"]["departures"]), [],
        "nothing is departing OAK")
    _eq(_labels(out["airports"]["KOAK"]["landings"]), ["UAL200", "UAL201"],
        "OAK landings are nearest-the-runway first")
    _eq(_labels(out["near"]), ["JBU700", "HOP600", "HOP500"],
        "near-centre section is closest-first by dst")

    # Airport rows carry range and bearing to/from that field (SWA100 is 3 km
    # due north of SFO, UAL200 is 5 km due west of OAK); near-centre rows carry
    # the plane's own dst / dir from the centre.
    _close(_row(out["airports"]["KSFO"]["departures"], "SWA100").dist_nm,
           3.0 / 1.852, "SFO departure row distance is to the field, not the centre")
    _close(_row(out["airports"]["KSFO"]["departures"], "SWA100").bearing, 0.0,
           "SFO departure row bearing is from the field (plane due N of SFO)")
    _close(_row(out["airports"]["KOAK"]["landings"], "UAL200").bearing, 270.0,
           "OAK landing row bearing is from the field (plane due W of OAK)")
    _close(_row(out["near"], "HOP500").dist_nm, 2.4,
           "near-centre row distance is the plane's dst from the centre")
    _close(_row(out["near"], "HOP500").bearing, 123.0,
           "near-centre row bearing is the plane's dir from the centre")

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

    _test_fmt_route()

    print("nearby.py: all classification assertions passed")
    return 0


def _test_fmt_route():
    # _fmt_route turns a routes.get() state (+ retrying flag) into the column.
    _eq(nearby._fmt_route(("SFO", "JFK")), "SFO->JFK", "a resolved route")
    _eq(nearby._fmt_route(("?", "LHR")), "?->LHR",
        "an endpoint the source only half-knew")
    _eq(nearby._fmt_route(""), "...", "pending is an ellipsis")
    _eq(nearby._fmt_route(None, retrying=True), "...",
        "unknown but still retrying is an ellipsis")
    _eq(nearby._fmt_route(None, retrying=False), "?",
        "unknown with the retries spent is a question mark")
    _eq(nearby._fmt_route("absent"), "", "never requested is blank")


if __name__ == "__main__":
    sys.exit(main())
