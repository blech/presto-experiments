"""
Nearby-aircraft classification -- the model behind dev/nearby.py's textual
board, split out the way traces.py/routes.py sit below radar.py: this module
owns the data shapes and the heuristic, knows nothing about fetching,
queues, or the terminal; nearby.py is the thin CPython app wired around it.

Sorts a plane list into five independent sections -- departures and
landings for each nearby airport, plus everything close to the radar
centre -- and, for departures, into ground-sighted > route > trail >
geometry precedence: geometry always proposes (terminal-area radius +
altitude ceiling + vertical state + heading toward/away from the field);
each stronger signal, if it has an opinion, can only remove a proposal, or
(ground-sighted only) confirm one outright. See bucket()'s docstring for
the return shape.

Deliberately dependency-light and Plane-agnostic: every function here reads
only e/n/alt/vstate/heading/dst/dir/on_ground/callsign/hex/trail off
whatever it's given (proven by dev/test_board.py's plain-class fixtures),
so it runs unchanged whether fed by a live plane.Plane from feed.Feed or,
eventually, a reconstructed record from somewhere else (PLAN.md item 9).
"""

import collections
import csv
import math
import os
import sys

import geometry

_NM_PER_KM = 1.0 / 1.852

# One nearby airport: its position in the (e, n) km frame and the set of
# identifiers a resolved route's endpoint might name it by (its ICAO ident
# and, when it has one, its IATA code).
Airport = collections.namedtuple("Airport", "e n codes")

# One board row: the aircraft plus the range and bearing the section is keyed
# to -- from the field for an airport section, from the radar centre for
# "near". dist_nm is what the section is sorted by; bearing is degrees.
Row = collections.namedtuple("Row", "plane dist_nm bearing")

# Heuristic thresholds. Deliberately loose for a first cut -- a go-around or a
# vectored downwind leg will still be misfiled.
Config = collections.namedtuple(
    "Config",
    "terminal_nm phase_ceil_ft heading_tol near_nm include_ground "
    "trail_min_points trail_endpoint_nm ground_sight_nm",
)


def default_config():
    return Config(
        terminal_nm=12.0,     # how far out an arrival/departure still "belongs" to a field
        phase_ceil_ft=8000,   # above this it is an overflight, not a movement here
        heading_tol=70.0,     # track vs. bearing to/from the field, degrees
        near_nm=5.0,          # "near the centre point" radius
        include_ground=False,
        trail_min_points=3,   # below this the trail says nothing; geometry alone stands
        trail_endpoint_nm=5.0,  # how close a departure's oldest fix must be to the field
        ground_sight_nm=2.0,  # how close "on the ground" counts as "at this field"
    )


def _bearing(de, dn):
    """Compass bearing (deg) of the offset (de east, dn north) km."""
    return math.degrees(math.atan2(de, dn)) % 360.0


def _angle_diff(a, b):
    """Smallest absolute difference between two bearings, 0..180."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _known(code):
    """A route endpoint the source actually resolved (not '' or '?')."""
    return bool(code) and code != "?"


def _route_vetoes(route, codes, arriving):
    """Should this resolved route (origin, dest) keep the plane OUT of the
    given field's arrivals (arriving=True) or departures (arriving=False)?

    Geometry proposes; the route only ever removes. A plane belongs in a
    field's departures only if its route starts there, and its arrivals only
    if its route ends there -- and never in both roles for one field. An
    unresolved endpoint ('?') says nothing and never vetoes.
    """
    origin, dest = route
    here, elsewhere = (dest, origin) if arriving else (origin, dest)
    if _known(elsewhere) and elsewhere in codes:
        return True                        # the other end is this field -> wrong role
    if _known(here) and here not in codes:
        return True                        # this end is a different, known airport
    return False


def _trail_vetoes(trail, ap, cfg, arriving):
    """Should this plane's trail (oldest->newest (e, n, alt) fixes -- see
    traces.points_for()) keep it OUT of the given field's arrivals
    (arriving=True) or departures? Only consulted when no resolved route
    settled the question (see bucket()); a real flight history is still
    better evidence than geometry alone (DATA_TRACE.md's altitude-trend /
    monotonic-progress signals, simplified to endpoint distance).

    A genuine departure starts at the field (its oldest fix within
    trail_endpoint_nm) and moves away from it; a genuine arrival moves
    toward it. d_old/d_new are the distance (nm) from the field to the
    trail's oldest and newest fix.
    """
    (old_e, old_n, _), (new_e, new_n, _) = trail[0], trail[-1]
    d_old = math.hypot(old_e - ap.e, old_n - ap.n) * _NM_PER_KM
    d_new = math.hypot(new_e - ap.e, new_n - ap.n) * _NM_PER_KM
    if arriving:
        return d_new >= d_old              # not getting closer -> not arriving here
    return d_old > cfg.trail_endpoint_nm or d_new <= d_old


# --- ground-sighted departures -----------------------------------------
#
# hex -> ICAO ident of the field a plane was directly observed on the
# ground within cfg.ground_sight_nm of. The strongest departure signal --
# we watched it sit there -- so it outranks even a resolved route. Kept
# outside plane.Plane (an LRU here, like routes._cache/traces._cache)
# because a grounded aircraft never reaches a board section, so nothing
# else would keep the sighting alive across the fetch where it later climbs
# out. No TTL: a sighting that's gone stale just never gets consulted again
# -- geometry alone won't later propose a departure for a plane that's
# flown far from every displayed field.

_ground_sighted = collections.OrderedDict()
_GROUND_SIGHTED_MAX = 64


def _remember_ground_sighting(hex_id, icao):
    _ground_sighted[hex_id] = icao
    _ground_sighted.move_to_end(hex_id)
    while len(_ground_sighted) > _GROUND_SIGHTED_MAX:
        _ground_sighted.popitem(last=False)


def note_ground_sightings(planes, airports, cfg):
    """Record which displayed field, if any, each grounded plane in
    `planes` is sitting at. Call this every fetch cycle over the *raw* feed
    -- grounded aircraft are filtered out of every board section (see
    bucket()), so this is the only place that ever sees them. Nearest
    field wins if more than one is within range (airports this close
    together isn't expected at ground_sight_nm's scale, but be correct
    about it anyway)."""
    for p in planes:
        if not getattr(p, "on_ground", False) or not getattr(p, "hex", None):
            continue
        best = None
        for icao, ap in airports.items():
            d_nm = math.hypot(p.e - ap.e, p.n - ap.n) * _NM_PER_KM
            if d_nm <= cfg.ground_sight_nm and (best is None or d_nm < best[1]):
                best = (icao, d_nm)
        if best is not None:
            _remember_ground_sighting(p.hex, best[0])


def bucket(planes, airports, cfg, routes_by_cs=None):
    """Sort `planes` into the five sections.

    `airports` is an ordered mapping ICAO -> Airport(e, n, codes) from the
    radar centre (see load_airports). `routes_by_cs`, when given, maps a
    callsign to its resolved (origin, dest) route codes. Returns lists of
    Row(plane, dist_nm, bearing):

        {
          "airports": {ICAO: {"departures": [Row, ...],
                              "landings":   [Row, ...]}, ...},
          "near": [Row, ...],
        }

    In an airport section dist_nm / bearing are the plane's range and
    bearing from that field; in "near" they are its range and bearing from
    the radar centre (the feed's own dst / dir). Departure lists are
    furthest-from-the-field first (a new takeoff enters at the bottom);
    landing lists and "near" are nearest-first. Sections are independent --
    a climbing aircraft over the centre can be both a departure and a
    "near centre" contact.

    Departures resolve through a precedence chain, strongest evidence
    first: a ground sighting (note_ground_sightings) confirms or vetoes
    outright; lacking one, a resolved route can veto; lacking that, a
    trail (>= cfg.trail_min_points) can veto. Landings only have the
    route/trail tiers -- a plane can't be ground-sighted at a field it
    hasn't reached yet.
    """
    routes_by_cs = routes_by_cs or {}
    result = {"airports": collections.OrderedDict(), "near": []}
    for icao in airports:
        result["airports"][icao] = {"departures": [], "landings": []}

    scored = {icao: {"departures": [], "landings": []} for icao in airports}
    near = []

    for p in planes:
        if getattr(p, "on_ground", False) and not cfg.include_ground:
            continue

        if p.dst is not None and p.dst <= cfg.near_nm:
            near.append(p)

        # Only aircraft low enough to be arriving or departing are candidates
        # for an airport section; a cruise-altitude contact over the field is
        # an overflight.
        if not isinstance(p.alt, (int, float)) or p.alt > cfg.phase_ceil_ft:
            continue
        if p.heading is None or p.vstate not in ("climb", "descent"):
            continue

        route = routes_by_cs.get(p.callsign)
        trail = p.trail if len(p.trail) >= cfg.trail_min_points else None
        sighted = _ground_sighted.get(getattr(p, "hex", None))

        for icao, ap in airports.items():
            de, dn = p.e - ap.e, p.n - ap.n
            dist_nm = math.hypot(de, dn) * _NM_PER_KM
            if dist_nm > cfg.terminal_nm:
                continue
            field_to_plane = _bearing(de, dn)   # where the plane sits from the field
            if p.vstate == "climb":
                # Departing: climbing and tracking away from the field.
                if _angle_diff(p.heading, field_to_plane) > cfg.heading_tol:
                    continue
                # ground-sighted > route > trail > geometry: each tier, if
                # it has an opinion, is trusted outright over the next.
                if sighted is not None:
                    if sighted != icao:
                        continue
                elif route:
                    if _route_vetoes(route, ap.codes, arriving=False):
                        continue
                elif trail and _trail_vetoes(trail, ap, cfg, arriving=False):
                    continue
                scored[icao]["departures"].append((dist_nm, p, field_to_plane))
            else:
                # Landing: descending and tracking toward the field.
                if _angle_diff(p.heading, _bearing(-de, -dn)) > cfg.heading_tol:
                    continue
                if route:
                    if _route_vetoes(route, ap.codes, arriving=True):
                        continue
                elif trail and _trail_vetoes(trail, ap, cfg, arriving=True):
                    continue
                scored[icao]["landings"].append((dist_nm, p, field_to_plane))

    for icao in airports:
        # Departures read furthest-first, so a fresh takeoff joins at the
        # bottom and climbs up the list as it leaves. Landings read
        # nearest-the-runway first -- next to touch down at the top.
        deps = sorted(scored[icao]["departures"], key=lambda t: t[0], reverse=True)
        lands = sorted(scored[icao]["landings"], key=lambda t: t[0])
        result["airports"][icao]["departures"] = [Row(p, d, b) for d, p, b in deps]
        result["airports"][icao]["landings"] = [Row(p, d, b) for d, p, b in lands]

    near.sort(key=lambda p: p.dst)
    result["near"] = [Row(p, p.dst, p.dir) for p in near]
    return result


def visible_planes(result):
    """One entry per callsign across every section of a bucket() result (a
    plane in several sections is still one route lookup)."""
    seen = collections.OrderedDict()
    for sec in result["airports"].values():
        for key in ("departures", "landings"):
            for row in sec[key]:
                seen[row.plane.callsign] = row.plane
    for row in result["near"]:
        seen[row.plane.callsign] = row.plane
    return list(seen.values())


def load_airports(idents, csv_path):
    """ICAO idents -> Airport(e, n, codes) from the radar centre, read from
    an OurAirports airports.csv (the one make_basemap.py caches). `codes`
    is the airport's ICAO ident plus its IATA code, for matching a route
    endpoint. Preserves `idents` order. Exits with a hint if csv_path is
    missing."""
    if not os.path.isfile(csv_path):
        sys.exit(
            "OurAirports data not found at %s\n"
            "Fetch it once with:  python3 prestoradar/make_basemap.py --download\n"
            "or pass --airports-csv PATH." % csv_path
        )

    want = set(idents)
    rows = {}
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("ident") in want:
                try:
                    lat = float(row["latitude_deg"])
                    lon = float(row["longitude_deg"])
                except (KeyError, ValueError):
                    continue
                iata = (row.get("iata_code") or "").strip()
                codes = frozenset(c for c in (row["ident"], iata) if c)
                rows[row["ident"]] = (lat, lon, codes)
            if len(rows) == len(want):
                break

    missing = [i for i in idents if i not in rows]
    if missing:
        sys.exit("not found in %s: %s" % (csv_path, ", ".join(missing)))

    return collections.OrderedDict(
        (i, Airport(*geometry.project(rows[i][0], rows[i][1]), rows[i][2]))
        for i in idents
    )
