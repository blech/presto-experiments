import asyncio
import gc
import json
import math
import sys
import time

import geometry
import net
from netlog import log

_KNOT_TO_KM_S = 1.852 / 3600.0        # knots -> km travelled per second


class Feed:
    """Owns the fetched aircraft list and its fetch lifecycle. `planes`/
    `fetch_count`/`fetch_ok` replace the module globals radar.py used to
    mutate directly (REFACTORING.md #2) -- any code holding this instance
    sees the same live state, no `global` needed.

    `on_update(planes)`, if set, is called with the fresh list after every
    successful fetch. It's the hook the caller (today radar.py, eventually
    ui.py) uses to react -- e.g. re-point a selection at the same aircraft
    in the new list -- without this module needing to know selection, or
    any other UI concept, exists.
    """

    def __init__(self, host, path, user_agent, level_rate_fpm, fetch_interval_ms):
        self.host = host
        self.path = path
        self.user_agent = user_agent
        self.level_rate_fpm = level_rate_fpm
        self.fetch_interval_ms = fetch_interval_ms
        self.planes = []
        self.fetch_count = 0     # completed fetch attempts, any outcome (0 == still loading)
        self.fetch_ok = False    # did the most recent attempt succeed?
        self.on_update = None

    async def _fetch(self):
        """Pull the current aircraft list from adsb.lol.

        Returns a list of plane dicts holding position in the metric frame
        (e, n) and a per-second velocity (ve, vn) for dead reckoning between
        fetches, or None if the fetch/parse failed (the caller keeps
        animating the old list).
        """
        gc.collect()
        try:
            status, body = await net.http_get(self.host, self.path, self.user_agent)
        except Exception as e:  # noqa: BLE001
            log("fetch: request failed:", repr(e))
            return None

        log("fetch: HTTP", status, len(body), "bytes")
        if status != 200:
            log("fetch: HTTP", status, body[:200])
            return None

        try:
            data = json.loads(body)
        except ValueError as e:
            log("fetch: bad JSON:", repr(e), len(body), "bytes")
            return None
        finally:
            body = None
            gc.collect()

        planes = []
        for aircraft in data.get("ac", []) or []:
            lat = aircraft.get("lat")
            lon = aircraft.get("lon")
            if lat is None or lon is None:
                continue

            altitude = aircraft.get("alt_baro")  # feet, or the string "ground"
            gs = aircraft.get("gs") or 0.0       # ground speed, knots
            # HIDE_ON_GROUND is a draw-time filter, applied by radar.py's
            # _hidden(), not here (REFACTORING.md #5) -- every aircraft the
            # feed returns is real data, not a presentation choice.

            callsign = (aircraft.get("flight") or aircraft.get("hex", "")).strip()

            # "track" is the direction of travel over the ground; it's absent for
            # stationary aircraft, so fall back to nose heading. ("dir" in the feed
            # is the bearing from the radar centre to the aircraft, not where it's
            # heading, so it isn't what we want here.)
            heading = aircraft.get("track")
            if heading is None:
                heading = aircraft.get("true_heading")

            # Vertical state from the reported climb/descent rate.
            vrate = aircraft.get("baro_rate")
            if vrate is None:
                vrate = aircraft.get("geom_rate")
            if vrate is None or abs(vrate) < self.level_rate_fpm:
                vstate = "level"
            elif vrate > 0:
                vstate = "climb"
            else:
                vstate = "descent"

            east, north = geometry.project(lat, lon)
            if heading is not None and gs:
                hr = math.radians(heading)
                speed = gs * _KNOT_TO_KM_S
                ve, vn = speed * math.sin(hr), speed * math.cos(hr)
            else:
                ve = vn = 0.0

            planes.append({
                "callsign": callsign, "e": east, "n": north,
                "ve": ve, "vn": vn, "heading": heading, "gs": gs, "vstate": vstate,
                "cat": aircraft.get("category"),   # ADS-B emitter category, e.g. "A5", "A7"
                # Detail fields for the tap-to-inspect panel (item 2a).
                "hex": aircraft.get("hex", ""),
                "reg": aircraft.get("r"),
                "type": aircraft.get("t"),
                "desc": aircraft.get("desc"),
                "alt": altitude,
                "vrate": vrate,
                "squawk": aircraft.get("squawk"),
                "emergency": aircraft.get("emergency"),
                "dst": aircraft.get("dst"),   # nm from centre
                "dir": aircraft.get("dir"),   # bearing from centre, degrees
            })
        return planes

    async def run(self):
        while True:
            log("fetch...")
            t = time.ticks_ms()
            try:
                fresh = await self._fetch()
            except Exception as e:  # noqa: BLE001
                log("FETCH ERROR:", repr(e))
                if hasattr(sys, "print_exception"):
                    sys.print_exception(e)
                fresh = None
            self.fetch_count += 1
            self.fetch_ok = fresh is not None
            if fresh is not None:
                self.planes = fresh
                if self.on_update:
                    self.on_update(fresh)
                log("fetch done:", len(self.planes), "planes",
                    time.ticks_diff(time.ticks_ms(), t), "ms  mem", gc.mem_free())
            await asyncio.sleep_ms(self.fetch_interval_ms)
