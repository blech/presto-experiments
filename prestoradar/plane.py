import math

import geometry

_KNOT_TO_KM_S = 1.852 / 3600.0        # knots -> km travelled per second


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
                 "vrate", "squawk", "emergency", "dst", "dir")

    @classmethod
    def from_feed(cls, ac, level_rate_fpm):
        """Build a Plane from one adsb.lol `ac[]` entry, or return None to
        skip it (no position). This is the body of feed.py's old `for
        aircraft in ...` loop, moved verbatim -- the one place the feed's
        field vocabulary is decoded, now testable off-device against a
        canned fixture (see dev/test_plane.py)."""
        lat = ac.get("lat")
        lon = ac.get("lon")
        if lat is None or lon is None:
            return None

        p = cls()
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
        return p

    def __repr__(self):
        return "<Plane %s %s alt=%s>" % (self.callsign or "?", self.hex or "?", self.alt)

    def advance(self, dt):
        """Dead-reckon `dt` seconds along the last known velocity. Was the
        inline `p["e"] += p["ve"] * dt` loop in radar.py's _render_loop."""
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
        "?". Was spelled out at three call sites (panel title, tap log,
        route request)."""
        return self.callsign or self.hex or "?"

    @property
    def alt_sort_key(self):
        """Sort key for drawing: lowest altitude first, with "ground"/None
        (not a number) sorting below everything. Was geometry.alt_key(p)."""
        a = self.alt
        return a if isinstance(a, (int, float)) else -1
