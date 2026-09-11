#!/usr/bin/env python3
"""
Desktop (CPython) test for plane.py's feed parsing -- no WiFi, no device.

`Plane.from_feed()` is the one place adsb.lol's `ac[]` field vocabulary is
decoded. Before plane.py it lived inside `feed.Feed._fetch()` and could only
be exercised by flashing the Presto and reading the serial log. This asserts
its output field by field against a fixed fixture, so a change to the parse
(or a drifted field name) fails here.

    python3 prestoradar/dev/test_plane.py
"""

import math
import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import geometry  # noqa: E402
from plane import Plane  # noqa: E402

_LEVEL_RATE_FPM = 200   # matches settings_example.py

# One representative `ac[]` slice from adsb.lol's /v2/point response: a
# climbing airliner, a descending one, a stationary aircraft on the ground,
# a sparse military contact (no flight/r/t), and an entry with no position
# (which from_feed() must skip).
_FIXTURE = [
    {"hex": "406a3d", "flight": "BAW123  ", "lat": 51.9, "lon": -0.3,
     "alt_baro": 12000, "gs": 280.0, "track": 90.0, "baro_rate": 1800,
     "category": "A3", "r": "G-EUUU", "t": "A320", "desc": "AIRBUS A-320",
     "squawk": "1234", "emergency": "none", "dst": 8.4, "dir": 172.0},
    {"hex": "4ca9b1", "flight": "RYR8AH", "lat": 52.1, "lon": -0.5,
     "alt_baro": 8000, "gs": 250.0, "track": 270.0, "geom_rate": -1200,
     "category": "A3", "r": "EI-DYK", "t": "B738"},
    {"hex": "40aabb", "flight": "", "lat": 52.0, "lon": -0.4,
     "alt_baro": "ground", "gs": 0.0, "true_heading": 45.0},
    {"hex": "43c123", "lat": 52.05, "lon": -0.45, "alt_baro": 25000,
     "gs": 420.0, "track": 10.0, "baro_rate": 50},
    {"hex": "abc999", "flight": "NOPOS1"},   # no lat/lon -> skipped
]

_KNOT_TO_KM_S = 1.852 / 3600.0


def _eq(got, want, what):
    if got != want:
        raise AssertionError("%s: got %r, want %r" % (what, got, want))


def _close(got, want, what, tol=1e-6):
    if abs(got - want) > tol:
        raise AssertionError("%s: got %r, want %r (tol %g)" % (what, got, want, tol))


def main():
    planes = [p for p in (Plane.from_feed(ac, _LEVEL_RATE_FPM) for ac in _FIXTURE)
              if p is not None]
    _eq(len(planes), 4, "row without lat/lon is skipped")

    a, b, ground, mil = planes

    # Climbing airliner: all fields populated, callsign stripped, vstate from
    # a positive baro_rate above the level threshold.
    _eq(a.callsign, "BAW123", "callsign is stripped")
    _eq(a.hex, "406a3d", "hex")
    _eq(a.vstate, "climb", "positive baro_rate -> climb")
    _eq(a.alt, 12000, "alt passes through")
    _eq(a.gs, 280.0, "gs")
    _eq(a.heading, 90.0, "track becomes heading")
    _eq(a.cat, "A3", "category -> cat")
    _eq((a.reg, a.type, a.desc), ("G-EUUU", "A320", "AIRBUS A-320"), "detail fields")
    _eq((a.squawk, a.emergency), ("1234", "none"), "squawk/emergency")
    _eq((a.dst, a.dir), (8.4, 172.0), "dst/dir")
    ex, ny = geometry.project(51.9, -0.3)
    _close(a.e, ex, "e = geometry.project east")
    _close(a.n, ny, "n = geometry.project north")
    speed = 280.0 * _KNOT_TO_KM_S
    _close(a.ve, speed * math.sin(math.radians(90.0)), "ve from track+gs")
    _close(a.vn, speed * math.cos(math.radians(90.0)), "vn from track+gs")

    # Descending: vstate from geom_rate when baro_rate is absent.
    _eq(b.vstate, "descent", "negative geom_rate -> descent")
    _eq(b.vrate, -1200, "geom_rate fallback fills vrate")
    _eq((b.reg, b.type, b.desc), ("EI-DYK", "B738", None), "missing desc -> None")

    # On the ground: no callsign -> falls back to hex; true_heading used when
    # track is absent; zero gs -> zero velocity; on_ground / label helpers.
    _eq(ground.callsign, "40aabb", "empty flight -> hex as callsign")
    _eq(ground.label, "40aabb", "label falls back to hex")
    _eq(ground.heading, 45.0, "true_heading fallback")
    _eq((ground.ve, ground.vn), (0.0, 0.0), "gs 0 -> no dead-reckon velocity")
    _eq(ground.on_ground, True, 'alt "ground" -> on_ground')
    _eq(a.on_ground, False, "airborne -> not on_ground")

    # Sparse contact: optional fields default to None, not missing.
    _eq((mil.callsign, mil.label), ("43c123", "43c123"), "no flight -> hex")
    _eq((mil.reg, mil.type, mil.cat, mil.squawk), (None, None, None, None),
        "absent optional fields are None")
    _eq(mil.vstate, "level", "small baro_rate under threshold -> level")

    # alt_sort_key: numeric altitude passes through, "ground"/None sink to -1.
    _eq(a.alt_sort_key, 12000, "numeric alt_sort_key")
    _eq(ground.alt_sort_key, -1, '"ground" alt_sort_key sinks')

    # advance() dead-reckons in place.
    e0, n0 = a.e, a.n
    a.advance(2.0)
    _close(a.e, e0 + a.ve * 2.0, "advance moves e")
    _close(a.n, n0 + a.vn * 2.0, "advance moves n")

    # __slots__ rejects an unknown field (the typo guard).
    try:
        a.headnig = 90
    except AttributeError:
        pass
    else:
        raise AssertionError("__slots__ should reject an unknown attribute")

    # --- object reuse + trail (feed.py's hex -> Plane registry, DATA_TRACE.md) ---
    # feed.py passes last fetch's Plane back as `into=` so the object -- and its
    # `trail` of past fixes -- persists across fetches instead of being rebuilt.
    from plane import _TRAIL_MAX

    ac0 = {"hex": "abc123", "flight": "TEST1", "lat": 52.0, "lon": -0.2,
           "alt_baro": 10000, "gs": 300.0, "track": 90.0, "baro_rate": 0}
    r = Plane.from_feed(ac0, _LEVEL_RATE_FPM)
    _eq(len(r.trail), 1, "a new Plane's trail is seeded with its first fix")
    _eq(r.trail[0], (r.e, r.n, r.alt), "a trail fix is (e, n, alt)")

    ac1 = dict(ac0, lat=52.1, lon=-0.1, alt_baro=11000)
    r2 = Plane.from_feed(ac1, _LEVEL_RATE_FPM, into=r)
    _eq(r2 is r, True, "from_feed(into=p) updates and returns the same object")
    _eq(r.alt, 11000, "the reused object's fields are updated in place")
    _eq(len(r.trail), 2, "each fetch appends one trail fix")
    ex1, ny1 = geometry.project(52.1, -0.1)
    _close(r.trail[-1][0], ex1, "the newest trail fix is the new projected position")

    skipped = Plane.from_feed({"hex": "abc123"}, _LEVEL_RATE_FPM, into=r)
    _eq(skipped, None, "a no-position entry returns None even with into= set")
    _eq(len(r.trail), 2, "a skipped entry leaves the trail alone")

    for _ in range(_TRAIL_MAX + 5):
        Plane.from_feed(ac1, _LEVEL_RATE_FPM, into=r)
    _eq(len(r.trail), _TRAIL_MAX, "the trail is capped at _TRAIL_MAX")

    print("plane.py: all parse assertions passed (%d aircraft)" % len(planes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
