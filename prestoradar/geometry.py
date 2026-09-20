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


def unproject(east, north):
    # Inverse of project() -- a plane dict only keeps the projected (e, n)
    # frame (routes.py needs real lat/lon for its plausibility check against
    # candidate routes' airports).
    lat = settings.CENTER_LAT + north / _KM_PER_DEG_LAT
    lon = settings.CENTER_LON + east / _KM_PER_DEG_LON
    return lat, lon


_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def compass(deg):
    if deg is None:
        return "?"
    return _COMPASS[int((deg % 360) / 45 + 0.5) % 8]


def near_airport(e, n, basemap_data, radius_km):
    """True if (e, n) -- km east/north of the radar centre, this module's own
    projection frame, the same one basemap_data.AIRPORTS is baked in -- is
    within radius_km of one of the airports this radar displays. False if
    basemap_data is None or has no AIRPORTS at all; the caller decides what
    that should mean (routes.py's route-plausibility waiver and radar.py's
    ground-detection refinement want different fallbacks -- see
    ground_hidden() below for the latter)."""
    airports = getattr(basemap_data, "AIRPORTS", None)
    if not airports:
        return False
    return any(math.sqrt((e - ax) ** 2 + (n - ay) ** 2) <= radius_km
               for _code, ax, ay in airports)


def ground_hidden(on_ground, hide_on_ground, e, n, basemap_data, radius_km):
    """Should a plane be hidden by the HIDE_ON_GROUND setting? Requires
    hide_on_ground and on_ground (Plane.on_ground's alt/gs guess); when a
    basemap with airport marks is loaded, also requires (e, n) to be within
    radius_km of one of them -- a hovering helicopter (or any stray
    alt=0/gs=0 report) far from every displayed airport is not actually
    landed (TODOS.md 2026-09-19). Falls back to trusting on_ground alone
    when there's no basemap/airport data to check proximity against."""
    if not (hide_on_ground and on_ground):
        return False
    airports = getattr(basemap_data, "AIRPORTS", None)
    if not airports:
        return True
    return near_airport(e, n, basemap_data, radius_km)
