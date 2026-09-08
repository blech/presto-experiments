import math

import aircraft_types
import airlines
import geometry

_KNOT_TO_KM_S = 1.852 / 3600.0        # knots -> km travelled per second

# How many past fixes each Plane keeps in `trail`. One point is appended per
# real feed fetch (~FETCH_INTERVAL_MS apart, not per dead-reckon tick), so 45
# is ~22 min of history at the 30 s poll -- more than a 30 km scope shows an
# aircraft for, and about the same point count traces.py downsamples the
# network trace_recent seed to, so the in-RAM fallback trail and the seeded
# trail draw a similar-length line. Cheap: ~45 short tuples per aircraft
# (DATA_TRACE.md item 6).
_TRAIL_MAX = 45


class Plane:
    """One aircraft from the feed: a typed replacement for the 19-key dict
    `feed.py` used to build (REFACTORING.md #10). `__slots__` both saves the
    per-instance `__dict__` (worth a `gc.mem_free()` check on-device -- the
    RAM benefit is build-dependent in MicroPython) and, more usefully, turns
    a mistyped field name into an immediate `AttributeError` at first touch
    instead of a `KeyError` on whichever frame that branch runs.

    Mutable, not a `namedtuple`: `_render_loop` dead-reckons `e`/`n` along
    `ve`/`vn` every animation tick. Behaviour that belongs with one aircraft
    lives here (`advance`, `on_ground`, `label`, `alt_sort_key`); the
    `HIDE_ON_GROUND` check itself stays in radar.py because it also reads a
    live setting, and `on_ground` is only its data half.
    """

    __slots__ = ("callsign", "e", "n", "ve", "vn", "heading", "gs",
                 "vstate", "cat", "hex", "reg", "type", "desc", "alt",
                 "vrate", "squawk", "emergency", "dst", "dir", "trail")

    @classmethod
    def from_feed(cls, ac, level_rate_fpm, into=None):
        """Build a Plane from one adsb.lol `ac[]` entry, or return None to
        skip it (no position). This is the body of feed.py's old `for
        aircraft in ...` loop, moved verbatim -- the one place the feed's
        field vocabulary is decoded, now testable off-device against a
        canned fixture (see dev/test_plane.py).

        `into` is the existing Plane for this aircraft's hex from the
        previous fetch, if any: feed.py keeps a `hex -> Plane` registry and
        passes it back here so the object is updated in place across fetches
        rather than rebuilt from scratch. That gives each aircraft a stable
        identity, which is what lets `trail` -- its recent (e, n, alt)
        fixes -- accumulate at all (DATA_TRACE.md item 6). A no-position
        entry still returns None and `into` is left untouched (the early
        return happens before any field is written)."""
        lat = ac.get("lat")
        lon = ac.get("lon")
        if lat is None or lon is None:
            return None

        p = into if into is not None else cls()
        if into is None:
            p.trail = []
        p.alt = ac.get("alt_baro")           # feet, or the string "ground"
        p.gs = ac.get("gs") or 0.0           # ground speed, knots
        p.callsign = (ac.get("flight") or ac.get("hex", "")).strip()

        # "track" is the direction of travel over the ground; it's absent for
        # stationary aircraft, so fall back to nose heading. ("dir" in the feed
        # is the bearing from the radar centre to the aircraft, not where it's
        # heading, so it isn't what we want here.)
        heading = ac.get("track")
        if heading is None:
            heading = ac.get("true_heading")
        p.heading = heading

        # Vertical state from the reported climb/descent rate.
        vrate = ac.get("baro_rate")
        if vrate is None:
            vrate = ac.get("geom_rate")
        p.vrate = vrate
        if vrate is None or abs(vrate) < level_rate_fpm:
            p.vstate = "level"
        elif vrate > 0:
            p.vstate = "climb"
        else:
            p.vstate = "descent"

        p.e, p.n = geometry.project(lat, lon)
        if heading is not None and p.gs:
            hr = math.radians(heading)
            speed = p.gs * _KNOT_TO_KM_S
            p.ve, p.vn = speed * math.sin(hr), speed * math.cos(hr)
        else:
            p.ve = p.vn = 0.0

        p.cat = ac.get("category")   # ADS-B emitter category, e.g. "A5", "A7"
        # Detail fields for the tap-to-inspect panel (item 2a).
        p.hex = ac.get("hex", "")
        p.reg = ac.get("r")
        p.type = ac.get("t")
        p.desc = ac.get("desc")
        p.squawk = ac.get("squawk")
        p.emergency = ac.get("emergency")
        p.dst = ac.get("dst")        # nm from centre
        p.dir = ac.get("dir")        # bearing from centre, degrees
        p._record_fix()
        return p

    def _record_fix(self):
        """Append this fetch's real position to `trail`, oldest->newest,
        capped at _TRAIL_MAX. Called once per feed fetch from from_feed()
        (never from advance() -- the dead-reckoned positions between fetches
        would just pad the trail with interpolation and no new information).
        This is the in-RAM history traces.points_for() falls back to when
        the network trace_recent seed isn't available."""
        self.trail.append((self.e, self.n, self.alt))
        if len(self.trail) > _TRAIL_MAX:
            del self.trail[:len(self.trail) - _TRAIL_MAX]

    def __repr__(self):
        return "<Plane %s %s alt=%s>" % (self.callsign or "?", self.hex or "?", self.alt)

    def advance(self, dt):
        """Dead-reckon `dt` seconds along the last known velocity. Was the
        inline `p.e += p.ve * dt` loop in radar.py's _render_loop."""
        self.e += self.ve * dt
        self.n += self.vn * dt

    @property
    def on_ground(self):
        """The data half of radar.py's _hidden(): altitude reads 0/"ground",
        or ground speed is 0. _hidden() ANDs this with SETTINGS.HIDE_ON_GROUND,
        which is why that check stays there and not here."""
        return self.alt in (0, "ground") or self.gs == 0

    @property
    def label(self):
        """Callsign if it's broadcasting one, else the ICAO hex id, else
        "?". Was spelled out at the panel title (render.py) and the
        tap log (ui.py)."""
        return self.callsign or self.hex or "?"

    @property
    def alt_sort_key(self):
        """Sort key for drawing: lowest altitude first, with "ground"/None
        (not a number) sorting below everything. Was geometry.alt_key(p)."""
        a = self.alt
        return a if isinstance(a, (int, float)) else -1

    @property
    def type_description(self):
        """Readable model -- "Boeing 737-800". The feed's own `desc` when it
        sent one, else a lookup on the ICAO type code (`type`) in
        aircraft_types_data.py, else None. Live value wins, per
        DATA-TODOS.md #2's `live > local` order. Zero-storage: the table and
        its cache live in aircraft_types.py, not on the instance (Plane is
        rebuilt every fetch; the table is shared across all of them)."""
        return self.desc or aircraft_types.describe(self.type)

    @property
    def operator(self):
        """Airline from the callsign's 3-letter ICAO prefix -- "SkyWest" for
        "SKW4397" -- or None for a registration or the bare hex-id fallback.
        Pure table lookup, no network."""
        if not self.callsign or self.callsign == self.hex:
            return None
        return airlines.operator(self.callsign)
