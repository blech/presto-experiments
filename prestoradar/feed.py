import asyncio
import gc
import json
import sys
import time

import net
from netlog import log
from plane import Plane


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
        # hex -> Plane from the previous fetch. Rebuilt every fetch to hold
        # only the aircraft that fetch actually returned, so it stays bounded
        # (no accumulation of long-gone contacts). Its point is object
        # identity: an aircraft still in range keeps the same Plane instance
        # across fetches, so its `trail` of past fixes survives (DATA_TRACE.md
        # item 6). An aircraft that drops out loses its object and its trail;
        # carry-forward for a one-fetch gap (DATA-TODOS.md #4) is separate and
        # not done here.
        self._by_hex = {}

    async def _fetch(self):
        """Pull the current aircraft list from adsb.lol.

        Returns a list of Plane objects (see plane.py) holding position in
        the metric frame (e, n) and a per-second velocity (ve, vn) for dead
        reckoning between fetches, or None if the fetch/parse failed (the
        caller keeps animating the old list).
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

        # Per-aircraft decode lives in Plane.from_feed() (REFACTORING.md
        # #10) -- it returns None for an entry with no position, which used
        # to be a `continue` here. HIDE_ON_GROUND is still a draw-time
        # filter in radar.py's _hidden() (REFACTORING.md #5), not applied
        # here: every aircraft the feed returns is real data.
        planes = []
        by_hex = {}
        for aircraft in data.get("ac", []) or []:
            h = aircraft.get("hex") or ""
            # Reuse last fetch's Plane for this hex so its trail carries over
            # (Plane.from_feed updates it in place); a first sighting gets a
            # fresh object with an empty trail.
            p = Plane.from_feed(aircraft, self.level_rate_fpm,
                                into=self._by_hex.get(h) if h else None)
            if p is not None:
                planes.append(p)
                if h:
                    by_hex[h] = p
        self._by_hex = by_hex
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
