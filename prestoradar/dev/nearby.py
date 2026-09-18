#!/usr/bin/env python3
"""
Desktop (CPython) "nearby aircraft" board -- a textual counterpart to the
radar scope (UI-TRAILS.md, "A third mode: list / board?").

Fetches the current aircraft list with feed.Feed._fetch() -- the same
coroutine radar.py runs on-device -- and sorts it into five independent
sections:

    * departures from each nearby airport
    * landings at each nearby airport
    * everything close to the radar centre

An aircraft can appear in more than one section. Airport association starts
as a geometric heuristic (terminal-area radius + altitude ceiling + vertical
state + heading toward/away from the field) that a resolved route then
vetoes when it disagrees (bucket()'s routes_by_cs). Routes for every visible
aircraft are fetched through a fetchqueue.Queue -- the same generic, paced
queue traces.py's backfill uses on-device -- so one flaky/slow lookup can't
stall the board; routes.request(), the immediate single-tap path radar.py
uses, is untouched.

    python3 prestoradar/dev/nearby.py
    python3 prestoradar/dev/nearby.py --once
    python3 prestoradar/dev/nearby.py --radius 50 --interval 20
"""

import argparse
import asyncio
import collections
import csv
import math
import os
import sys
import time

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import feed  # noqa: E402
import fetchqueue  # noqa: E402
import geometry  # noqa: E402
import routes  # noqa: E402
from settings import (  # noqa: E402
    CENTER_LAT, CENTER_LON, RADIUS_KM, USER_AGENT, LEVEL_RATE_FPM,
    FETCH_INTERVAL_MS, ROUTE_QUEUE_INTERVAL_MS,
)

RADAR_HOST = "api.adsb.lol"

# ICAO idents of the airports the board reports movements for. Positions come
# from the OurAirports airports.csv that make_basemap.py caches; nothing here
# is hand-entered.
AIRPORTS = ("KSFO", "KOAK")
OURAIRPORTS_CSV = os.path.expanduser("~/.cache/ourairports/airports.csv")

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
    "terminal_nm phase_ceil_ft heading_tol near_nm include_ground",
)


def default_config():
    return Config(
        terminal_nm=12.0,     # how far out an arrival/departure still "belongs" to a field
        phase_ceil_ft=8000,   # above this it is an overflight, not a movement here
        heading_tol=70.0,     # track vs. bearing to/from the field, degrees
        near_nm=5.0,          # "near the centre point" radius
        include_ground=False,
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


def bucket(planes, airports, cfg, routes_by_cs=None):
    """Sort `planes` into the five sections.

    `airports` is an ordered mapping ICAO -> Airport(e, n, codes) from the
    radar centre (see load_airports). `routes_by_cs`, when given, maps a
    callsign to its resolved (origin, dest) route codes; a route whose
    endpoints don't fit a proposed section vetoes it (see _route_vetoes).
    Returns lists of Row(plane, dist_nm, bearing):

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
                if route and _route_vetoes(route, ap.codes, arriving=False):
                    continue
                scored[icao]["departures"].append((dist_nm, p, field_to_plane))
            else:
                # Landing: descending and tracking toward the field.
                if _angle_diff(p.heading, _bearing(-de, -dn)) > cfg.heading_tol:
                    continue
                if route and _route_vetoes(route, ap.codes, arriving=True):
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


def load_airports(idents, csv_path=OURAIRPORTS_CSV):
    """ICAO idents -> Airport(e, n, codes) from the radar centre, read from
    the OurAirports airports.csv make_basemap.py caches. `codes` is the
    airport's ICAO ident plus its IATA code, for matching a route endpoint.
    Preserves `idents` order. Exits with a hint if the cache is missing."""
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


async def _fetch(radius_nm):
    path = f"/v2/point/{CENTER_LAT}/{CENTER_LON}/{radius_nm}"
    f = feed.Feed(RADAR_HOST, path, USER_AGENT, LEVEL_RATE_FPM, FETCH_INTERVAL_MS)
    return await f._fetch()


_ARROW = {"climb": "^", "descent": "v", "level": "-"}


def _fmt_alt(alt):
    if isinstance(alt, (int, float)):
        return "%6d" % alt
    return "  grnd" if alt in (0, "ground") else "     ?"


def _fmt_route(state, retrying=False):
    """The route column from a routes.get() state: "SFO->JFK" once resolved,
    "..." while a lookup is in flight or still has retries left, "?" once it
    has given up, blank for a callsign that was never a route to ask about."""
    if isinstance(state, tuple):
        return "%s->%s" % state
    if state == "" or (state is None and retrying):
        return "..."
    if state is None:
        return "?"
    return ""                                   # "absent"


def _fmt_row(row):
    p = row.plane
    op = (p.operator or "")[:13]
    typ = (p.type or "")[:4]
    gs = "%4.0f" % p.gs if p.gs else "   -"
    dst = "%5.1f" % row.dist_nm if row.dist_nm is not None else "    -"
    brg = geometry.compass(row.bearing)   # from the field, or from centre for "near"
    route = _fmt_route(routes.get(p.callsign), routes.retrying(p.callsign))
    return ("  %-8s %-13s %-4s %s%s %skt %snm %-2s  %-9s"
            % (p.label[:8], op, typ, _fmt_alt(p.alt),
               _ARROW.get(p.vstate, "?"), gs, dst, brg, route))


def _print_section(title, rows):
    print(title)
    if not rows:
        print("  (none)")
    else:
        for row in rows:
            print(_fmt_row(row))
    print()


def render(result, radius_km):
    print("\033[2J\033[H", end="")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    total = sum(len(v["departures"]) + len(v["landings"])
                for v in result["airports"].values()) + len(result["near"])
    print("NEARBY AIRCRAFT  %s  centre %.4f,%.4f  r=%gkm"
          % (stamp, CENTER_LAT, CENTER_LON, radius_km))
    print("=" * 72)
    print()
    for icao, sec in result["airports"].items():
        _print_section("Departures from %s  (nm to field)" % icao, sec["departures"])
        _print_section("Landings at %s  (nm to field)" % icao, sec["landings"])
    _print_section("Near the centre point  (nm to centre)", result["near"])
    print("%d rows across %d sections" % (total, 2 * len(result["airports"]) + 1),
          flush=True)   # stdout is block-buffered to a pipe; show each refresh


def _visible_planes(result):
    """One entry per callsign across every section (a plane in several
    sections is still one route lookup)."""
    seen = collections.OrderedDict()
    for sec in result["airports"].values():
        for key in ("departures", "landings"):
            for row in sec[key]:
                seen[row.plane.callsign] = row.plane
    for row in result["near"]:
        seen[row.plane.callsign] = row.plane
    return list(seen.values())


def _resolved_routes(planes):
    """{callsign: (origin, dest)} for the planes whose route has resolved --
    the veto input to bucket()."""
    out = {}
    for p in planes:
        st = routes.get(p.callsign)
        if isinstance(st, tuple):
            out[p.callsign] = st
    return out


async def _run(radius_nm, cfg, airports, radius_km, fetch_interval,
               render_interval, once):
    state = {"planes": [], "by_cs": {}, "route_pending": 0}
    route_queue = fetchqueue.Queue(ROUTE_QUEUE_INTERVAL_MS)

    async def process_route(cs):
        # Resolve against *this* cycle's live planes, not whichever one was
        # visible when cs was enqueued -- an aircraft that's left the board
        # by the time its turn comes up is simply not found here, and no
        # fetch happens (same pattern as radar.py's Feed.resolve() for trace
        # backfill). state["route_pending"] only ever reflects queue depth,
        # not fetches-in-flight, so it's decremented either way.
        try:
            plane = state["by_cs"].get(cs)
            if plane is not None:
                await routes.fetch_for(plane)
        finally:
            state["route_pending"] -= 1

    def current_result():
        # Re-bucket on every render, not just every fetch, so a plane leaves
        # the wrong section within a render tick of its route resolving --
        # bucket() is pure and cheap over ~30 planes.
        return bucket(state["planes"], airports, cfg,
                      _resolved_routes(state["planes"]))

    async def refresh():
        planes = await _fetch(radius_nm)
        if planes is None:
            print("fetch failed -- see the log lines above")
            return
        state["planes"] = planes
        state["by_cs"] = {p.callsign: p for p in planes}
        # Priority = distance from centre, nearest first -- the same "most
        # likely to matter soon" signal the trace-backfill queue uses.
        for p in _visible_planes(current_result()):
            if routes.eligible(p):
                route_queue.enqueue(p.callsign, p.dst)
                state["route_pending"] += 1

    await refresh()   # first paint has data

    if once:
        worker = asyncio.create_task(route_queue.run(process_route))
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 25.0
        while state["route_pending"] > 0 and loop.time() < deadline:
            await asyncio.sleep(0.5)
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        render(current_result(), radius_km)
        return

    async def fetcher():
        while True:
            await asyncio.sleep(fetch_interval)
            await refresh()

    async def renderer():
        while True:
            render(current_result(), radius_km)
            await asyncio.sleep(render_interval)

    await asyncio.gather(fetcher(), renderer(), route_queue.run(process_route))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--radius", type=float, default=RADIUS_KM, metavar="KM",
                    help="km from settings.CENTER_LAT/LON (default: settings.RADIUS_KM)")
    ap.add_argument("--interval", type=float, default=FETCH_INTERVAL_MS / 1000.0,
                    metavar="S", help="seconds between feed fetches (default: settings.FETCH_INTERVAL_MS)")
    ap.add_argument("--render-interval", type=float, default=5.0, metavar="S",
                    help="seconds between board reprints while routes fill in (default: 5)")
    ap.add_argument("--once", action="store_true",
                    help="one fetch, drain routes briefly, print once, exit")
    ap.add_argument("--include-ground", action="store_true",
                    help="keep aircraft on the ground (default: hide them)")
    ap.add_argument("--airports-csv", metavar="PATH", default=OURAIRPORTS_CSV,
                    help="OurAirports airports.csv (default: the make_basemap.py cache)")
    args = ap.parse_args()

    radius_nm = round(args.radius / 1.852)
    airports = load_airports(AIRPORTS, args.airports_csv)
    cfg = default_config()._replace(include_ground=args.include_ground)

    asyncio.run(_run(radius_nm, cfg, airports, args.radius,
                     args.interval, args.render_interval, args.once))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
