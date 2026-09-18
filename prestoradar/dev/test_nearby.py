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
import shutil
import sys
import tempfile

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
    vstate, heading, dst / dir (range and bearing from centre), on_ground,
    trail (oldest->newest (e, n, alt) fixes, empty unless given)."""

    def __init__(self, callsign, e, n, alt, vstate, heading, dst,
                 on_ground=False, direction=None, trail=None):
        self.callsign = callsign
        self.e = e
        self.n = n
        self.alt = alt
        self.vstate = vstate
        self.heading = heading
        self.dst = dst
        self.dir = direction
        self.on_ground = on_ground
        self.trail = trail if trail is not None else []


def main():
    airports = {
        "KSFO": nearby.Airport(*geometry.project(*_SFO), frozenset(("KSFO", "SFO"))),
        "KOAK": nearby.Airport(*geometry.project(*_OAK), frozenset(("KOAK", "OAK"))),
    }
    se, sn = airports["KSFO"].e, airports["KSFO"].n
    oe, on = airports["KOAK"].e, airports["KOAK"].n
    cfg = nearby.Config(terminal_nm=12.0, phase_ceil_ft=8000,
                        heading_tol=70.0, near_nm=5.0, include_ground=False,
                        trail_min_points=3, trail_endpoint_nm=5.0)

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

    # --- route veto -----------------------------------------------------
    # A climb in OAK's terminal area tracking away from OAK: geometry alone
    # calls it an OAK departure (and, being inside SFO's area too, a SFO
    # departure). Its route is SFO->YYZ -- it left SFO and is passing over
    # OAK. A resolved route whose endpoints aren't a field removes it from
    # that field's section; it never adds a plane geometry didn't propose.
    poe = _FakePlane("POE672", oe + 0.4, on + 0.2, 7750, "climb", 60.0, 9.8)
    geom_only = nearby.bucket([poe], airports, cfg)
    _eq("POE672" in _labels(geom_only["airports"]["KOAK"]["departures"]), True,
        "with no resolved route, geometry alone still proposes the OAK departure")
    vetoed = nearby.bucket([poe], airports, cfg, {"POE672": ("SFO", "YYZ")})
    _eq(_labels(vetoed["airports"]["KOAK"]["departures"]), [],
        "a route starting and ending away from OAK vetoes the OAK departure")
    _eq(_labels(vetoed["airports"]["KSFO"]["departures"]), ["POE672"],
        "the same route confirms POE672 where geometry also proposed a SFO departure")

    # Symmetric for landings: a descent lined up on OAK from the east, but the
    # route is TUS->SFO -- it ends at SFO, not OAK.
    skw = _FakePlane("SKW5583", oe - 4.0, on, 2400, "descent", 90.0, 9.1)
    _eq(_labels(nearby.bucket([skw], airports, cfg)["airports"]["KOAK"]["landings"]),
        ["SKW5583"], "geometry alone proposes the OAK landing")
    v2 = nearby.bucket([skw], airports, cfg, {"SKW5583": ("TUS", "SFO")})
    _eq(_labels(v2["airports"]["KOAK"]["landings"]), [],
        "a route ending at SFO vetoes the OAK landing")

    # A '?' endpoint (the source didn't resolve that side) must not veto.
    q = nearby.bucket([skw], airports, cfg, {"SKW5583": ("?", "?")})
    _eq(_labels(q["airports"]["KOAK"]["landings"]), ["SKW5583"],
        "an unresolved ('?') route endpoint does not veto")

    # --- trail veto (route > trail > geometry) ---------------------------
    # Same OAK-departure geometry as POE672 above, but no route this time --
    # only a trail. Its oldest fix is near SFO, ~9.6nm from OAK (> the 5nm
    # trail_endpoint_nm), so the trail says it didn't start at OAK either.
    far_start_trail = [(se, sn, 3000), ((se + oe) / 2, (sn + on) / 2, 5000),
                       (oe + 0.4, on + 0.2, 7750)]
    poe_trail = _FakePlane("POE900", oe + 0.4, on + 0.2, 7750, "climb", 60.0, 9.8,
                           trail=far_start_trail)
    no_route = nearby.bucket([poe_trail], airports, cfg)
    _eq("POE900" in _labels(no_route["airports"]["KOAK"]["departures"]), False,
        "with no route, a trail starting far from OAK vetoes the OAK departure")

    # Same shape, but the trail starts *at* OAK and moves away -- confirms
    # the departure instead of vetoing it.
    at_field_trail = [(oe, on, 500), (oe + 0.2, on + 0.1, 3000),
                      (oe + 0.4, on + 0.2, 7750)]
    poe_confirm = _FakePlane("POE901", oe + 0.4, on + 0.2, 7750, "climb", 60.0, 9.8,
                             trail=at_field_trail)
    confirmed = nearby.bucket([poe_confirm], airports, cfg)
    _eq("POE901" in _labels(confirmed["airports"]["KOAK"]["departures"]), True,
        "a trail starting at the field confirms the OAK departure")

    # A trail shorter than trail_min_points is not consulted -- geometry
    # stands, same as no trail at all.
    poe_short = _FakePlane("POE902", oe + 0.4, on + 0.2, 7750, "climb", 60.0, 9.8,
                           trail=far_start_trail[:2])
    short = nearby.bucket([poe_short], airports, cfg)
    _eq("POE902" in _labels(short["airports"]["KOAK"]["departures"]), True,
        "a trail shorter than trail_min_points doesn't veto -- geometry stands")

    # Landings are symmetric: a trail moving away from the field (getting
    # farther, not closer) vetoes a proposed OAK landing.
    receding_trail = [(oe - 1.0, on, 3000), (oe - 2.5, on, 2700), (oe - 4.0, on, 2400)]
    skw_trail = _FakePlane("SKW901", oe - 4.0, on, 2400, "descent", 90.0, 9.1,
                           trail=receding_trail)
    receding = nearby.bucket([skw_trail], airports, cfg)
    _eq("SKW901" in _labels(receding["airports"]["KOAK"]["landings"]), False,
        "with no route, a trail moving away from OAK vetoes the OAK landing")

    # Precedence: a resolved route confirming the section is trusted even
    # when the trail alone would have vetoed it.
    poe_both = _FakePlane("POE903", oe + 0.4, on + 0.2, 7750, "climb", 60.0, 9.8,
                          trail=far_start_trail)
    trusted = nearby.bucket([poe_both], airports, cfg, {"POE903": ("OAK", "PDX")})
    _eq("POE903" in _labels(trusted["airports"]["KOAK"]["departures"]), True,
        "a resolved route confirming the section beats a trail that would veto it")

    _test_load_airports()
    _test_fmt_route()

    print("nearby.py: all classification assertions passed")
    return 0


def _test_load_airports():
    tmp = tempfile.mkdtemp()
    try:
        csv_path = os.path.join(tmp, "airports.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            fh.write("ident,type,latitude_deg,longitude_deg,iata_code\n")
            fh.write("KSFO,large_airport,37.6189,-122.3750,SFO\n")
            fh.write("KOAK,large_airport,37.7213,-122.2197,OAK\n")
        aps = nearby.load_airports(("KSFO", "KOAK"), csv_path)
        _eq(list(aps), ["KSFO", "KOAK"], "load_airports keys follow the requested order")
        _eq(aps["KSFO"].codes >= {"KSFO", "SFO"}, True,
            "an airport's codes hold both its ICAO ident and its IATA code")
        ex, ny = geometry.project(37.6189, -122.3750)
        _close(aps["KSFO"].e, ex, "load_airports projects to the (e, n) frame")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
