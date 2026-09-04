import math

import settings

# Flat local frame: 1 degree of latitude is 60 nm; a degree of longitude
# shrinks by cos(latitude). Fixed for the process's lifetime -- CENTER_LAT
# only ever changes via a settings.py edit + reboot, never at runtime.
_KM_PER_DEG_LAT = 60.0 * 1.852
_KM_PER_DEG_LON = _KM_PER_DEG_LAT * math.cos(math.radians(settings.CENTER_LAT))


def project(lat, lon):
    # Geographic position -> kilometres east / north of the centre.
    east = (lon - settings.CENTER_LON) * _KM_PER_DEG_LON
    north = (lat - settings.CENTER_LAT) * _KM_PER_DEG_LAT
    return east, north


_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def compass(deg):
    if deg is None:
        return "?"
    return _COMPASS[int((deg % 360) / 45 + 0.5) % 8]


def alt_key(p):
    # Sort key: lowest altitude first. "ground"/None (not a number) sorts
    # lowest of all.
    a = p["alt"]
    return a if isinstance(a, (int, float)) else -1
