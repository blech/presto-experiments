"""
Generate prestoradar/basemap_data.py -- a tiny coastline + airport basemap for
the flight radar -- from GSHHG (Global Self-consistent Hierarchical
High-resolution Geography, https://www.soest.hawaii.edu/pwessel/gshhg/).

Desktop tool, standard library only. It reads GSHHG's native binary shoreline
file, clips it to a box around the radar centre, projects it into the same flat
kilometres-east/north frame radar.py uses, simplifies it with Douglas-Peucker,
and writes it out as a Python module of coordinate lists. Airport marks come
from OurAirports' airports.csv, filtered to the clip box.

Typical use:

    python3 prestoradar/make_basemap.py --download            # first time
    python3 prestoradar/make_basemap.py                        # re-run

GSHHG data (~119 MB zip, all resolutions) is cached in ~/.cache/gshhg/. Pass
--gshhg to point at an existing gshhs_<res>.b file, a directory of them, or the
zip. If a download is blocked, fetch the zip by hand from
https://www.soest.hawaii.edu/pwessel/gshhg/gshhg-bin-2.3.7.zip and pass its path.

OurAirports' airports.csv (~13 MB) is cached in ~/.cache/ourairports/. --download
fetches it too; --airports-csv points at an existing copy.

Raster mode (PLAN item 8): also skips all of the above, in one of two ways --
producing prestoradar/basemap.jpg, which radar.py decodes onto a background
layer at boot for the "map" display mode.

    python3 prestoradar/make_basemap.py --raster-fetch             # fetch it
    python3 prestoradar/make_basemap.py --raster mymap.png         # or bring one

--raster-fetch needs no API key: it asks an ArcGIS World MapServer's public
`export` endpoint for exactly the box CENTER_LAT/LON +/- RADIUS_KM covers, in
the same flat lat/lon frame radar.py projects into (bboxSR=imageSR=4326), at
480x480, as a JPEG -- so the result lines up with the radar with no reprojection
needed on this end. Standard library only. --raster-style picks the basemap
(topo/street/imagery).

--raster SRC instead packages an image you already have: resized so its short
side is 480 px, centre-cropped to 480x480, saved as baseline JPEG. The source
must already cover CENTER_LAT/LON +/- RADIUS_KM in radar.py's frame -- this
tool only conforms it to size. Needs Pillow.
"""

import argparse
import csv
import json
import math
import os
import struct
import sys
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import CENTER_LAT, CENTER_LON, RADIUS_KM

try:
    from settings import BASEMAP_AIRPORTS  # airport size tier; --airports overrides
except ImportError:
    BASEMAP_AIRPORTS = "medium"

try:
    from settings import USER_AGENT  # sent with the ArcGIS --raster-fetch request
except ImportError:
    USER_AGENT = "presto-radar/1.0 (+https://github.com/blech/presto-experiments)"

KM_PER_DEG_LAT = 60.0 * 1.852
KM_PER_DEG_LON = KM_PER_DEG_LAT * math.cos(math.radians(CENTER_LAT))
PX_PER_KM = 230.0 / RADIUS_KM   # matches radar.py; used by --min-ring-px

# The clip box has to contain the whole display. radar.py puts the outer ring
# (RADIUS_KM) at 230 px; the screen corners reach ~1.47x that, so clip a little
# beyond -- RADIUS_KM * CLIP_MARGIN -- unless --clip-radius-km overrides it.
CLIP_MARGIN = 1.6

# Airport marks come from OurAirports. --airports picks how far down the size
# ladder to go: "large" keeps only large_airport, "medium" adds medium_airport,
# "small" adds small_airport. Heliports, seaplane bases and closed fields are
# never kept. Anything outside the clip box is dropped, so the filter is the
# only knob -- there is no per-centre list to maintain.
AIRPORT_TIERS = ("large", "medium", "small")
_AIRPORT_TYPE = {"large": "large_airport",
                 "medium": "medium_airport",
                 "small": "small_airport"}

GSHHG_ZIP_URL = "https://www.soest.hawaii.edu/pwessel/gshhg/gshhg-bin-2.3.7.zip"
CACHE_DIR = os.path.expanduser("~/.cache/gshhg")

OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
AIRPORTS_CACHE = os.path.expanduser("~/.cache/ourairports")

# GSHHG binary polygon header: 11 big-endian int32.
#   id, n, flag, west, east, south, north, area, area_full, container, ancestor
_HEADER = struct.Struct(">11i")


def project(lat, lon):
    """Geographic degrees -> kilometres east / north of the centre."""
    return ((lon - CENTER_LON) * KM_PER_DEG_LON,
            (lat - CENTER_LAT) * KM_PER_DEG_LAT)


# --------------------------------------------------------------------------- #
# GSHHG reading
# --------------------------------------------------------------------------- #
def _micro_to_lon(microdeg):
    lon = microdeg / 1e6
    return lon - 360.0 if lon > 180.0 else lon


def iter_gshhg_polygons(path, wanted_levels, bbox):
    """Yield (level, [(lon, lat), ...]) for polygons whose header extent
    overlaps bbox = (min_lon, min_lat, max_lon, max_lat)."""
    min_lon, min_lat, max_lon, max_lat = bbox
    with open(path, "rb") as fh:
        while True:
            head = fh.read(_HEADER.size)
            if len(head) < _HEADER.size:
                return
            (_id, n, flag, west, east, south, north,
             _area, _area_full, _container, _ancestor) = _HEADER.unpack(head)
            level = flag & 0xFF
            point_bytes = n * 8

            if level not in wanted_levels:
                fh.seek(point_bytes, os.SEEK_CUR)
                continue

            pw, pe = _micro_to_lon(west), _micro_to_lon(east)
            ps, pn = south / 1e6, north / 1e6
            # Skip the fast-reject if the polygon wraps past the antimeridian.
            if pw <= pe and (pe < min_lon or pw > max_lon
                             or pn < min_lat or ps > max_lat):
                fh.seek(point_bytes, os.SEEK_CUR)
                continue

            raw = fh.read(point_bytes)
            pts = struct.unpack(">%di" % (2 * n), raw)
            ring = [(_micro_to_lon(pts[i]), pts[i + 1] / 1e6)
                    for i in range(0, len(pts), 2)]
            yield level, ring


# --------------------------------------------------------------------------- #
# Geometry: rectangle clip + Douglas-Peucker
# --------------------------------------------------------------------------- #
def _clip_to_halfplane(poly, keep, cross):
    """Sutherland-Hodgman against one infinite edge. keep(p) -> bool inside;
    cross(a, b) -> intersection point of segment a-b with the edge."""
    if not poly:
        return poly
    out = []
    prev = poly[-1]
    prev_in = keep(prev)
    for cur in poly:
        cur_in = keep(cur)
        if cur_in:
            if not prev_in:
                out.append(cross(prev, cur))
            out.append(cur)
        elif prev_in:
            out.append(cross(prev, cur))
        prev, prev_in = cur, cur_in
    return out


def clip_ring(ring, bbox):
    """Clip a lon/lat ring to bbox = (min_lon, min_lat, max_lon, max_lat)."""
    x0, y0, x1, y1 = bbox

    def lerp(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    ring = _clip_to_halfplane(ring, lambda p: p[0] >= x0,
                              lambda a, b: lerp(a, b, (x0 - a[0]) / (b[0] - a[0])))
    ring = _clip_to_halfplane(ring, lambda p: p[0] <= x1,
                              lambda a, b: lerp(a, b, (x1 - a[0]) / (b[0] - a[0])))
    ring = _clip_to_halfplane(ring, lambda p: p[1] >= y0,
                              lambda a, b: lerp(a, b, (y0 - a[1]) / (b[1] - a[1])))
    ring = _clip_to_halfplane(ring, lambda p: p[1] <= y1,
                              lambda a, b: lerp(a, b, (y1 - a[1]) / (b[1] - a[1])))
    return ring


def simplify(points, tol):
    """Iterative Douglas-Peucker. Keeps the first and last point."""
    if len(points) < 3:
        return points[:]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = tol * tol
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        far_i, far_d = -1, -1.0
        for i in range(lo + 1, hi):
            px, py = points[i]
            if seg2 == 0.0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / seg2
                t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
                cx, cy = ax + t * dx, ay + t * dy
                d = (px - cx) ** 2 + (py - cy) ** 2
            if d > far_d:
                far_i, far_d = i, d
        if far_d > tol2:
            keep[far_i] = True
            stack.append((lo, far_i))
            stack.append((far_i, hi))
    return [p for p, k in zip(points, keep) if k]


# --------------------------------------------------------------------------- #
# Data plumbing
# --------------------------------------------------------------------------- #
def resolve_gshhg(arg, resolution, allow_download):
    """Return a path to gshhs_<res>.b, downloading/extracting if asked."""
    name = "gshhs_%s.b" % resolution
    candidates = []
    if arg:
        if os.path.isdir(arg):
            candidates.append(os.path.join(arg, name))
        elif arg.endswith(".zip"):
            _extract_from_zip(arg, name, CACHE_DIR)
            candidates.append(os.path.join(CACHE_DIR, name))
        else:
            candidates.append(arg)
    candidates.append(os.path.join(CACHE_DIR, name))

    for path in candidates:
        if os.path.isfile(path):
            return path

    if not allow_download:
        sys.exit("%s not found. Re-run with --download, or pass --gshhg PATH "
                 "(a gshhs_<res>.b file, a directory of them, or the zip)." % name)

    os.makedirs(CACHE_DIR, exist_ok=True)
    zip_path = os.path.join(CACHE_DIR, "gshhg-bin-2.3.7.zip")
    if not os.path.isfile(zip_path):
        print("Downloading %s ..." % GSHHG_ZIP_URL)
        try:
            urllib.request.urlretrieve(GSHHG_ZIP_URL, zip_path)
        except Exception as e:
            sys.exit("Download failed (%s).\nFetch it by hand from\n  %s\n"
                     "and re-run with --gshhg <path to the zip>." % (e, GSHHG_ZIP_URL))
    _extract_from_zip(zip_path, name, CACHE_DIR)
    return os.path.join(CACHE_DIR, name)


def _extract_from_zip(zip_path, name, dest):
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.namelist() if os.path.basename(m) == name]
        if not members:
            sys.exit("%s not present in %s" % (name, zip_path))
        os.makedirs(dest, exist_ok=True)
        with zf.open(members[0]) as src, open(os.path.join(dest, name), "wb") as out:
            out.write(src.read())


def resolve_ourairports(arg, allow_download):
    """Return a path to an OurAirports airports.csv, caching a download under
    ~/.cache/ourairports/ if asked and no copy is present."""
    if arg:
        if os.path.isfile(arg):
            return arg
        sys.exit("--airports-csv %s not found" % arg)

    cached = os.path.join(AIRPORTS_CACHE, "airports.csv")
    if os.path.isfile(cached):
        return cached

    if not allow_download:
        sys.exit("airports.csv not found. Re-run with --download, or pass "
                 "--airports-csv PATH (fetch it from %s)." % OURAIRPORTS_URL)

    os.makedirs(AIRPORTS_CACHE, exist_ok=True)
    print("Downloading %s ..." % OURAIRPORTS_URL)
    try:
        urllib.request.urlretrieve(OURAIRPORTS_URL, cached)
    except Exception as e:
        sys.exit("Download failed (%s).\nFetch it by hand from\n  %s\n"
                 "and re-run with --airports-csv <path>." % (e, OURAIRPORTS_URL))
    return cached


def build_layer(gshhg_path, levels, bbox, tol_km, min_span_km=0.0):
    """Clip + project + simplify all polygons of the given levels.

    Rings whose projected bounding box is smaller than min_span_km in *both*
    axes are dropped -- islets that land as an unreadable speck on screen but
    still cost a per-frame draw call. The "both axes" test keeps thin features
    (a spit, a river mouth) that are small one way but long the other.
    """
    rings = []
    kept_pts = 0
    dropped_small = 0
    for _level, ll_ring in iter_gshhg_polygons(gshhg_path, set(levels), bbox):
        clipped = clip_ring(ll_ring, bbox)
        if len(clipped) < 3:
            continue
        projected = [project(lat, lon) for lon, lat in clipped]
        reduced = simplify(projected, tol_km)
        if len(reduced) < 3:
            continue
        if min_span_km > 0.0:
            xs = [p[0] for p in reduced]
            ys = [p[1] for p in reduced]
            if max(xs) - min(xs) < min_span_km and max(ys) - min(ys) < min_span_km:
                dropped_small += 1
                continue
        rings.append([(round(x, 2), round(y, 2)) for x, y in reduced])
        kept_pts += len(reduced)
    return rings, kept_pts, dropped_small


def build_airports(csv_path, bbox, keep_types):
    """Read OurAirports airports.csv; keep rows whose `type` is in keep_types
    and whose position falls inside bbox. Label with iata_code, else ident.
    Returns [(label, e_km, n_km), ...] sorted by label."""
    min_lon, min_lat, max_lon, max_lat = bbox
    out = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("type") not in keep_types:
                continue
            try:
                lat = float(row["latitude_deg"])
                lon = float(row["longitude_deg"])
            except (KeyError, ValueError):
                continue
            if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
                continue
            label = (row.get("iata_code") or row.get("ident") or "").strip()
            if not label:
                continue
            x, y = project(lat, lon)
            out.append((label, round(x, 2), round(y, 2)))
    out.sort()
    return out


def current_params(args, clip_radius_km):
    """Everything that, if changed, means basemap_data.py needs regenerating.

    RADIUS_KM is in here in its own right, not just via the clip box it sizes:
    it also scales PX_PER_KM, so --min-ring-px lands differently at a different
    radar radius.
    """
    return {
        "resolution": args.resolution,
        "center": [CENTER_LAT, CENTER_LON],
        "radius_km": RADIUS_KM,
        "clip_radius_km": clip_radius_km,
        "simplify_km": args.simplify_km,
        "min_ring_px": args.min_ring_px,
        "levels": args.levels,
        "airports": args.airports,
    }


def stamped_params(path):
    """Read the '# params:' line back out of an existing basemap_data.py."""
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith("# params: "):
                    return json.loads(line[len("# params: "):])
    except (OSError, ValueError):
        pass
    return None


def raster_params(src, out_px, quality):
    """Everything that, if changed, means basemap.jpg needs rebuilding: the
    centre and radar radius it is meant to match, plus the source file (path +
    mtime) and the output settings."""
    return {
        "center": [CENTER_LAT, CENTER_LON],
        "radius_km": RADIUS_KM,
        "src": os.path.abspath(src),
        "src_mtime": int(os.path.getmtime(src)),
        "px": out_px,
        "quality": quality,
    }


# --------------------------------------------------------------------------- #
# Raster mode: fetch (no key) from an ArcGIS World MapServer
# --------------------------------------------------------------------------- #
ARCGIS_STYLES = {
    "topo": "World_Topo_Map",
    "street": "World_Street_Map",
    "imagery": "World_Imagery",
}
ARCGIS_ATTRIBUTION = {
    "topo": "Esri, HERE, Garmin, FAO, NOAA, USGS, © OpenStreetMap contributors, "
            "and the GIS user community",
    "street": "Esri, HERE, Garmin, USGS, EPA, NPS, US Census Bureau, USDA",
    "imagery": "Esri, Maxar, Earthstar Geographics, and the GIS user community",
}


EARTH_RADIUS_M = 6378137.0   # WGS84 semi-major axis; also Web Mercator's sphere radius


def frame_bbox_deg():
    """The radar frame's lat/lon box -- (west, south, east, north) -- matching
    radar.py's flat projection exactly. to_screen() puts RADIUS_KM at 230 px of
    the 480x480 screen's 240 px half-width, so the screen edge (and this box)
    reaches HALF_KM = RADIUS_KM * 240/230, not RADIUS_KM itself. Degrees only,
    for display/sidecar purposes -- the actual fetch uses frame_bbox_3857()."""
    half_km = RADIUS_KM * 240.0 / 230.0
    dlat = half_km / KM_PER_DEG_LAT
    dlon = half_km / KM_PER_DEG_LON
    return (CENTER_LON - dlon, CENTER_LAT - dlat, CENTER_LON + dlon, CENTER_LAT + dlat)


def frame_bbox_3857():
    """The radar frame as a Web Mercator (EPSG:3857) bbox -- (west, south, east,
    north) in metres -- built to be square in true ground km around the centre.

    A first cut fetched bboxSR=imageSR=4326 (plain lat/lon) on the assumption
    that these ArcGIS World MapServer services would render it as a linear
    equirectangular raster, matching radar.py's own projection exactly. On
    device the result was visibly stretched vertically: the services are
    Web-Mercator-tiled, and reprojecting their cache to 4326 on the fly does
    its own aspect handling in the *native* SR, which doesn't line up with a
    bbox that was only made square in degrees-of-latitude-scaled-by-cos(lat).

    Fetching in the service's native SR sidesteps that reprojection entirely.
    Web Mercator is locally conformal (the same scale factor applies in both
    directions at a point), so a bbox built by applying the single local scale
    factor sec(lat) to a ground distance is square in Mercator metres too --
    and over a radar-sized extent (tens of km) that factor barely changes
    across the bbox, so the small-extent approximation this relies on holds.
    """
    half_km = RADIUS_KM * 240.0 / 230.0
    lat_rad = math.radians(CENTER_LAT)
    lon_rad = math.radians(CENTER_LON)
    cx = EARTH_RADIUS_M * lon_rad
    cy = EARTH_RADIUS_M * math.log(math.tan(math.pi / 4 + lat_rad / 2))
    half_m = half_km * 1000.0 / math.cos(lat_rad)   # local Mercator scale: sec(lat)
    return (cx - half_m, cy - half_m, cx + half_m, cy + half_m)


def fetch_raster_params(style, out_px):
    return {
        "center": [CENTER_LAT, CENTER_LON],
        "radius_km": RADIUS_KM,
        "provider": "arcgis",
        "style": style,
        "px": out_px,
    }


def build_raster_fetch(args):
    """Fetch prestoradar/basemap.jpg from an ArcGIS World MapServer's public
    export endpoint -- no API key, one HTTP GET, standard library only. Fetches
    in the service's native Web Mercator SR (see frame_bbox_3857()) rather than
    asking it to reproject to plain lat/lon, which visibly distorted the image."""
    service = ARCGIS_STYLES[args.raster_style]
    out = args.raster_out or os.path.join(os.path.dirname(__file__), "basemap.jpg")
    side = args.raster_px
    sidecar = out + ".json"
    params = fetch_raster_params(args.raster_style, side)

    if args.if_stale:
        try:
            with open(sidecar) as fh:
                if json.load(fh) == params and os.path.isfile(out):
                    print("basemap.jpg already current for %.2f, %.2f  radius %g km "
                          "-- skipping" % (CENTER_LAT, CENTER_LON, RADIUS_KM))
                    return
        except (OSError, ValueError):
            pass

    w, s, e, n = frame_bbox_3857()
    url = ("https://services.arcgisonline.com/arcgis/rest/services/%s/MapServer/export"
          "?bbox=%.3f,%.3f,%.3f,%.3f&bboxSR=3857&imageSR=3857&size=%d,%d&format=jpg&f=image"
          % (service, w, s, e, n, side, side))
    dw, ds, de, dn = frame_bbox_deg()
    print("centre  : %.2f, %.2f   radar radius %g km" % (CENTER_LAT, CENTER_LON, RADIUS_KM))
    print("bbox    : W %.4f  S %.4f  E %.4f  N %.4f  (deg, for reference)"
          % (dw, ds, de, dn))
    print("GET %s" % url)

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.URLError as e:
        sys.exit("fetch failed: %r" % e)

    if not content_type.startswith("image/"):
        # ArcGIS answers a bad request with a 200 OK and a JSON error body.
        sys.exit("ArcGIS did not return an image (Content-Type: %s):\n%s"
                 % (content_type, body[:500].decode("utf-8", "replace")))

    with open(out, "wb") as fh:
        fh.write(body)
    with open(sidecar, "w") as fh:
        json.dump(params, fh, separators=(",", ":"))
    print("wrote %s  (%dx%d, %d bytes)  + %s"
          % (out, side, side, len(body), os.path.basename(sidecar)))
    print("attribution: %s" % ARCGIS_ATTRIBUTION[args.raster_style])


def build_raster(args):
    """Package an already-rendered map image into prestoradar/basemap.jpg."""
    try:
        from PIL import Image
    except ImportError:
        sys.exit("--raster needs Pillow:  uv sync --extra raster   "
                 "(or: pip install pillow)")

    src = args.raster
    if not os.path.isfile(src):
        sys.exit("--raster %s: not found" % src)
    out = args.raster_out or os.path.join(os.path.dirname(__file__), "basemap.jpg")
    side = args.raster_px
    sidecar = out + ".json"
    params = raster_params(src, side, args.raster_quality)

    if args.if_stale:
        try:
            with open(sidecar) as fh:
                if json.load(fh) == params and os.path.isfile(out):
                    print("basemap.jpg already current for %.2f, %.2f  radius %g km "
                          "-- skipping" % (CENTER_LAT, CENTER_LON, RADIUS_KM))
                    return
        except (OSError, ValueError):
            pass

    resample = getattr(Image, "Resampling", Image).LANCZOS
    im = Image.open(src).convert("RGB")
    src_w, src_h = im.size
    w, h = im.size
    scale = side / min(w, h)
    if scale != 1.0:
        im = im.resize((max(round(w * scale), side), max(round(h * scale), side)),
                       resample)
    w, h = im.size
    left, top = (w - side) // 2, (h - side) // 2
    im = im.crop((left, top, left + side, top + side))
    # Baseline (non-progressive) JPEG: the device's jpegdec needs it.
    im.save(out, "JPEG", quality=args.raster_quality, optimize=True, progressive=False)

    with open(sidecar, "w") as fh:
        json.dump(params, fh, separators=(",", ":"))
    print("centre  : %.2f, %.2f   radar radius %g km" % (CENTER_LAT, CENTER_LON, RADIUS_KM))
    print("source  : %s  (%dx%d)" % (src, src_w, src_h))
    print("wrote %s  (%dx%d, %d bytes)  + %s"
          % (out, side, side, os.path.getsize(out), os.path.basename(sidecar)))


def format_rings(name, rings, per_line=8):
    """One ring per list, coordinates wrapped every `per_line` pairs -- long
    single-line list literals can choke MicroPython's compiler on the device."""
    lines = ["%s = [" % name]
    for ring in rings:
        coords = ["(%g, %g)" % (x, y) for x, y in ring]
        lines.append("    [")
        for i in range(0, len(coords), per_line):
            lines.append("        " + ", ".join(coords[i:i + per_line]) + ",")
        lines.append("    ],")
    lines.append("]")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolution", choices=list("clihf"), default="f",
                    help="GSHHG resolution: crude/low/intermediate/high/full (default f; "
                         "the simplify tolerance matters far more at this zoom)")
    ap.add_argument("--clip-radius-km", type=float, default=None,
                    help="half-size of the clip box around the centre, km "
                         "(default: RADIUS_KM * %g, which covers the screen "
                         "corners; pass a value to override)" % CLIP_MARGIN)
    ap.add_argument("--simplify-km", type=float, default=0.25,
                    help="Douglas-Peucker tolerance in km (default 0.25, ~2 px at "
                         "the radar's zoom; the dominant control on vertex count)")
    ap.add_argument("--min-ring-px", type=float, default=6.0,
                    help="drop rings whose projected bounding box is under this many "
                         "pixels in both axes (default 6; unreadable islet specks)")
    ap.add_argument("--levels", default="1,2",
                    help="GSHHG levels to keep: 1 land, 2 lake, 3 island-in-lake, "
                         "4 pond (default '1,2')")
    ap.add_argument("--airports", choices=("none",) + AIRPORT_TIERS, default=BASEMAP_AIRPORTS,
                    help="airport marks from OurAirports by size: 'large' = "
                         "large_airport only, 'medium' adds medium_airport, 'small' "
                         "adds small_airport, 'none' skips the layer (default from "
                         "settings.BASEMAP_AIRPORTS, currently %r)" % BASEMAP_AIRPORTS)
    ap.add_argument("--gshhg", help="path to gshhs_<res>.b, a directory of them, or the zip")
    ap.add_argument("--airports-csv", help="path to an OurAirports airports.csv "
                    "(default: cached download under ~/.cache/ourairports/)")
    ap.add_argument("--download", action="store_true",
                    help="download + cache the GSHHG zip and airports.csv if missing")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "basemap_data.py"),
                    help="output module path (default prestoradar/basemap_data.py)")
    ap.add_argument("--raster", metavar="SRC",
                    help="raster mode: package the already-rendered map image SRC "
                         "into basemap.jpg for radar.py's map-mode backdrop, then "
                         "exit (ignores every option above; needs Pillow)")
    ap.add_argument("--raster-fetch", action="store_true",
                    help="raster mode: fetch basemap.jpg from an ArcGIS World "
                         "MapServer's public export endpoint -- no API key, exact "
                         "radar-frame bbox, then exit (ignores every option above)")
    ap.add_argument("--raster-style", choices=tuple(ARCGIS_STYLES), default="topo",
                    help="--raster-fetch basemap style (default topo)")
    ap.add_argument("--raster-out", metavar="PATH",
                    help="raster mode output (default prestoradar/basemap.jpg)")
    ap.add_argument("--raster-px", type=int, default=480,
                    help="raster mode: output side length in px (default 480)")
    ap.add_argument("--raster-quality", type=int, default=85,
                    help="--raster (not --raster-fetch): JPEG quality 1..95 (default 85)")
    ap.add_argument("--if-stale", action="store_true",
                    help="do nothing if --out already matches settings + these args "
                         "(centre, radar radius, clip radius, simplify, min-ring, "
                         "resolution, levels, airports)")
    args = ap.parse_args()

    if args.raster and args.raster_fetch:
        ap.error("--raster and --raster-fetch are mutually exclusive")
    if args.raster:
        return build_raster(args)
    if args.raster_fetch:
        return build_raster_fetch(args)

    clip_radius_km = (args.clip_radius_km if args.clip_radius_km is not None
                      else round(RADIUS_KM * CLIP_MARGIN, 1))

    params = current_params(args, clip_radius_km)
    if args.if_stale and stamped_params(args.out) == params:
        print("basemap_data.py already current for %.2f, %.2f  radius %g km -- skipping"
              % (CENTER_LAT, CENTER_LON, RADIUS_KM))
        return

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    gshhg_path = resolve_gshhg(args.gshhg, args.resolution, args.download)

    r = clip_radius_km
    dlat = r / KM_PER_DEG_LAT
    dlon = r / KM_PER_DEG_LON
    bbox = (CENTER_LON - dlon, CENTER_LAT - dlat,
            CENTER_LON + dlon, CENTER_LAT + dlat)

    print("GSHHG   : %s" % gshhg_path)
    print("centre  : %.2f, %.2f   radar radius %g km" % (CENTER_LAT, CENTER_LON, RADIUS_KM))
    print("clip box: lon %.3f..%.3f  lat %.3f..%.3f  (+/- %g km)"
          % (bbox[0], bbox[2], bbox[1], bbox[3], r))

    min_span_km = args.min_ring_px / PX_PER_KM
    coast, coast_pts, coast_drop = build_layer(
        gshhg_path, [l for l in levels if l == 1], bbox, args.simplify_km, min_span_km)
    lakes, lake_pts, lake_drop = build_layer(
        gshhg_path, [l for l in levels if l >= 2], bbox, args.simplify_km, min_span_km)

    if args.airports == "none":
        airports = []
    else:
        cut = AIRPORT_TIERS.index(args.airports) + 1
        keep_types = {_AIRPORT_TYPE[t] for t in AIRPORT_TIERS[:cut]}
        csv_path = resolve_ourairports(args.airports_csv, args.download)
        print("airports: %s  (%s)" % (args.airports, csv_path))
        airports = build_airports(csv_path, bbox, keep_types)

    header = (
        '"""Generated by prestoradar/make_basemap.py -- do not edit by hand.\n\n'
        "Source     : GSHHG %s (v2.3.7); airports from OurAirports\n"
        "Centre     : %.2f, %.2f   Radar radius: %g km\n"
        "Clip radius: %g km   Simplify: %g km   Min ring: %g px   Levels: %s   Airports: %s\n\n"
        "Coordinates are kilometres east / north of the centre, matching\n"
        "radar.py's project(). COASTLINE and LAKES are closed rings -- draw them\n"
        "as polylines for an outline or as filled polygons for land / water.\n"
        '"""\n'
        "# params: %s\n"
    ) % (args.resolution, CENTER_LAT, CENTER_LON, RADIUS_KM,
         clip_radius_km, args.simplify_km, args.min_ring_px, args.levels,
         args.airports, json.dumps(params, separators=(",", ":")))

    parts = [header,
             format_rings("COASTLINE", coast),
             "",
             format_rings("LAKES", lakes),
             "",
             "AIRPORTS = [",
             *("    (%r, %g, %g)," % a for a in airports),
             "]",
             ""]
    text = "\n".join(parts)
    with open(args.out, "w") as fh:
        fh.write(text)

    print("\ncoastline rings: %4d  (%d vertices, %d small rings dropped)"
          % (len(coast), coast_pts, coast_drop))
    print("lake rings     : %4d  (%d vertices, %d small rings dropped)"
          % (len(lakes), lake_pts, lake_drop))
    print("airports       : %4d  %s" % (len(airports), [a[0] for a in airports]))
    print("wrote %s  (%d bytes)" % (args.out, len(text.encode())))


if __name__ == "__main__":
    main()
