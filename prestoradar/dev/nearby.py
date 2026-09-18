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
state + heading toward/away from the field) that a ground sighting, a
resolved route, or a trail can then veto or confirm, strongest first -- see
board.py, the classification model this file is a thin wrapper around
(board.bucket()'s docstring has the full precedence chain). Routes and
traces are fetched through a fetchqueue.Queue each -- the same generic,
paced queue traces.py's on-device backfill uses -- so one flaky/slow lookup
can't stall the board; routes.request(), radar.py's immediate single-tap
path, is untouched.

    python3 prestoradar/dev/nearby.py
    python3 prestoradar/dev/nearby.py --once
    python3 prestoradar/dev/nearby.py --radius 50 --interval 20
"""

import argparse
import asyncio
import os
import sys
import time

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import board  # noqa: E402
import feed  # noqa: E402
import fetchqueue  # noqa: E402
import geometry  # noqa: E402
import routes  # noqa: E402
import traces  # noqa: E402
from settings import (  # noqa: E402
    CENTER_LAT, CENTER_LON, RADIUS_KM, USER_AGENT, LEVEL_RATE_FPM,
    FETCH_INTERVAL_MS, ROUTE_QUEUE_INTERVAL_MS, TRACE_QUEUE_INTERVAL_MS,
)

RADAR_HOST = "api.adsb.lol"

# ICAO idents of the airports the board reports movements for. Positions come
# from the OurAirports airports.csv that make_basemap.py caches; nothing here
# is hand-entered.
AIRPORTS = ("KSFO", "KOAK")
OURAIRPORTS_CSV = os.path.expanduser("~/.cache/ourairports/airports.csv")


def _make_feed(radius_nm):
    path = f"/v2/point/{CENTER_LAT}/{CENTER_LON}/{radius_nm}"
    return feed.Feed(RADAR_HOST, path, USER_AGENT, LEVEL_RATE_FPM, FETCH_INTERVAL_MS)


def _trace_eligible(p):
    """True if p is worth a trace-backfill fetch: new (traced is False),
    airborne. Mirrors ui.py's _enqueue_eligible -- same skip heuristic
    (DATA_TRACE.md / the trace-fetch-queue design's "takeoff vs. edge-entry"
    note): an aircraft first sighted on the ground has little trace_recent
    history to fetch yet, so it's left un-enqueued and re-checked next
    cycle, picked up once airborne."""
    return p.traced is False and not p.on_ground


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
    # One persistent Feed for the whole run (not a fresh one per fetch): its
    # hex -> Plane registry is what lets plane.trail and plane.traced survive
    # across fetches at all (feed.py's own doc on _by_hex), which the trail
    # veto and the trace-backfill queue both depend on.
    feed_obj = _make_feed(radius_nm)
    state = {"planes": [], "by_cs": {}, "route_pending": 0}
    route_queue = fetchqueue.Queue(ROUTE_QUEUE_INTERVAL_MS)
    trace_queue = fetchqueue.Queue(TRACE_QUEUE_INTERVAL_MS)

    async def process_route(cs):
        # Resolve against *this* cycle's live planes, not whichever one was
        # visible when cs was enqueued -- an aircraft that's left the board
        # by the time its turn comes up is simply not found here, and no
        # fetch happens (same pattern feed.py's resolve() gives the trace
        # queue). state["route_pending"] only ever reflects queue depth,
        # not fetches-in-flight, so it's decremented either way.
        try:
            plane = state["by_cs"].get(cs)
            if plane is not None:
                await routes.fetch_for(plane)
        finally:
            state["route_pending"] -= 1

    async def process_trace(hex_id):
        # Same resolve-or-noop pattern as radar.py's _process_traced_hex:
        # look the hex up in the Feed's *current* registry, not a reference
        # captured at enqueue time, so a departed aircraft is a free no-op.
        plane = feed_obj.resolve(hex_id)
        if plane is not None and plane.traced == "pending":
            await traces.backfill(plane)

    def current_result():
        # Re-bucket on every render, not just every fetch, so a plane leaves
        # the wrong section within a render tick of its route resolving --
        # bucket() is pure and cheap over ~30 planes.
        return board.bucket(state["planes"], airports, cfg,
                            _resolved_routes(state["planes"]))

    async def refresh():
        planes = await feed_obj._fetch()
        if planes is None:
            print("fetch failed -- see the log lines above")
            return
        state["planes"] = planes
        state["by_cs"] = {p.callsign: p for p in planes}

        # Ground-sighted departures: over the raw feed, since a grounded
        # aircraft never reaches a board section (see board.py).
        board.note_ground_sightings(planes, airports, cfg)

        # Trace backfill: every new, airborne aircraft (not just the ones
        # currently in a board section -- matches ui.py's own policy, and a
        # plane not yet in a section is exactly one the trail veto could
        # later place correctly once it grows one). Priority = dst, nearest
        # first.
        for p in planes:
            if _trace_eligible(p):
                p.traced = "pending"
                trace_queue.enqueue(p.hex, p.dst)

        # Route lookups stay scoped to what's actually on the board.
        # Priority = distance from centre, nearest first.
        for p in board.visible_planes(current_result()):
            if routes.eligible(p):
                route_queue.enqueue(p.callsign, p.dst)
                state["route_pending"] += 1

    await refresh()   # first paint has data

    if once:
        route_worker = asyncio.create_task(route_queue.run(process_route))
        trace_worker = asyncio.create_task(trace_queue.run(process_trace))
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 25.0
        while state["route_pending"] > 0 and loop.time() < deadline:
            await asyncio.sleep(0.5)
        for worker in (route_worker, trace_worker):
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

    await asyncio.gather(fetcher(), renderer(),
                         route_queue.run(process_route),
                         trace_queue.run(process_trace))


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
    airports = board.load_airports(AIRPORTS, args.airports_csv)
    cfg = board.default_config()._replace(include_ground=args.include_ground)

    asyncio.run(_run(radius_nm, cfg, airports, args.radius,
                     args.interval, args.render_interval, args.once))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
