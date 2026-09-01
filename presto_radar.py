import gc
import json
import math
import network
import time
import requests
from presto import Presto

# Centre of the radar (San Francisco).
CENTER_LAT = 37.74
CENTER_LON = -122.42

# The display works in a local flat metric frame: kilometres east / north of the
# centre. RADIUS_KM is the distance from the centre to the outer radar ring.
RADIUS_KM = 30

# adsb.lol still wants the query radius in nautical miles (1 nm = 1.852 km). Keep
# it small: the response is ~450 bytes per aircraft and the Presto buffers the
# whole body plus the parsed dict in RAM alongside the full-res framebuffer, so
# an oversized radius shows up as a JSON / memory error rather than an HTTP one.
# Tune with radar_debug.py while watching the byte count. ~16 nm over SF is
# ~15 KB / ~35 aircraft.
RADIUS_NM = round(RADIUS_KM / 1.852)
RADAR_URL = f"https://api.adsb.lol/v2/point/{CENTER_LAT}/{CENTER_LON}/{RADIUS_NM}"

# adsb.lol rejects generic user agents ("user-agent too generic; include valid
# contact info"), so identify the app and give a contact URL.
USER_AGENT = "presto-radar/1.0 (+https://github.com/blech/presto-experiments)"

FETCH_INTERVAL_MS = 30_000   # adsb.lol public endpoints allow ~1 request/second
ANIM_INTERVAL = 0.5          # seconds between dead-reckoning redraws (~2 fps)

# When 1, hide aircraft that are on the ground: altitude of 0 / "ground", or a
# ground speed of 0. When 0, show everything.
HIDE_ON_GROUND = 1

# Rather than plotting altitude, colour each aircraft by what it's doing
# vertically. "baro_rate" (feet/minute, quantised to 64) is the climb/descent
# rate; "geom_rate" is the GPS-derived fallback. Anything within this band of
# zero -- or with no rate reported -- counts as flying level.
LEVEL_RATE_FPM = 256

# --- FRAME CONVERSIONS ---
# 1 degree of latitude is 60 nm; a degree of longitude shrinks by cos(latitude).
KM_PER_DEG_LAT = 60.0 * 1.852
KM_PER_DEG_LON = KM_PER_DEG_LAT * math.cos(math.radians(CENTER_LAT))
KNOT_TO_KM_S = 1.852 / 3600.0        # knots -> km travelled per second
PX_PER_KM = 230.0 / RADIUS_KM        # outer ring sits at RADIUS_KM

def project(lat, lon):
    # Geographic position -> kilometres east / north of the centre.
    east = (lon - CENTER_LON) * KM_PER_DEG_LON
    north = (lat - CENTER_LAT) * KM_PER_DEG_LAT
    return east, north

def to_screen(east_km, north_km):
    # Metric frame -> 480x480 pixels, centre at (240, 240), north is up.
    return int(240 + east_km * PX_PER_KM), int(240 - north_km * PX_PER_KM)

# --- INITIALIZE PRESTO ---
presto = Presto(full_res=True, ambient_light=True)
display = presto.display
WIDTH, HEIGHT = 480, 480

# Pen Colors (RGB)
BG_COLOR = display.create_pen(10, 20, 10)
RADAR_GREEN = display.create_pen(0, 230, 70)
TEXT_COLOR = display.create_pen(200, 255, 200)

# Vertical-state colours: level / cruising, climbing (departing), descending
# (approaching). Keyed by the "vstate" string set in fetch_planes().
VSTATE_PENS = {
    "level": display.create_pen(235, 235, 235),   # white
    "climb": display.create_pen(60, 200, 255),    # cyan
    "descent": display.create_pen(255, 160, 40),  # amber
}

def draw_track_arrow(x, y, heading_deg, speed_kt, pen):
    # heading_deg is degrees clockwise from north (the aircraft's track over the
    # ground). Screen y grows downwards, so north maps to -y.
    a = math.radians(heading_deg)
    dx, dy = math.sin(a), -math.cos(a)
    length = min(60, max(12, speed_kt * 0.15))  # ~knots -> pixels, clamped
    tip_x, tip_y = int(x + dx * length), int(y + dy * length)
    display.set_pen(pen)
    display.line(int(x), int(y), tip_x, tip_y)
    # Arrowhead: two short barbs splayed back from the tip.
    for barb_deg in (heading_deg + 148, heading_deg - 148):
        b = math.radians(barb_deg)
        display.line(tip_x, tip_y,
                     int(tip_x + math.sin(b) * 7), int(tip_y - math.cos(b) * 7))

def ring(cx, cy, r, thickness=3):
    # PicoGraphics circles are filled, so draw an outline as an outer disc with
    # a background-coloured disc punched out of the middle.
    display.set_pen(RADAR_GREEN)
    display.circle(cx, cy, r)
    display.set_pen(BG_COLOR)
    display.circle(cx, cy, r - thickness)

def draw_radar_grid():
    display.set_pen(BG_COLOR)
    display.clear()
    # Concentric rings at RADIUS_KM and half that
    ring(240, 240, int(RADIUS_KM * PX_PER_KM))
    ring(240, 240, int(RADIUS_KM * 0.5 * PX_PER_KM))
    # Crosshairs
    display.set_pen(RADAR_GREEN)
    display.line(240, 10, 240, 470)
    display.line(10, 240, 470, 240)

def show_message(text):
    display.set_pen(BG_COLOR)
    display.clear()
    display.set_pen(TEXT_COLOR)
    display.text(f"{text}", 5, 10, WIDTH, 2)
    presto.update()


def fetch_planes():
    """Pull the current aircraft list from adsb.lol.

    Returns a list of plane dicts holding position in the metric frame (e, n)
    and a per-second velocity (ve, vn) for dead reckoning between fetches, or
    None if the fetch/parse failed (the caller keeps animating the old list).
    """
    gc.collect()
    try:
        request = requests.get(RADAR_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        status = request.status_code
        body = request.text
        request.close()
    except Exception as e:
        show_message(f"Fetch failed: {e}")
        return None

    if status != 200:
        show_message(f"HTTP {status}\n{body[:200]}")
        return None

    try:
        data = json.loads(body)
    except ValueError as e:
        show_message(f"Bad JSON ({len(body)} bytes)\n{e}\n{body[:120]}")
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
        if HIDE_ON_GROUND and (altitude in (0, "ground") or gs == 0):
            continue

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
        if vrate is None or abs(vrate) < LEVEL_RATE_FPM:
            vstate = "level"
        elif vrate > 0:
            vstate = "climb"
        else:
            vstate = "descent"

        east, north = project(lat, lon)
        if heading is not None and gs:
            hr = math.radians(heading)
            speed = gs * KNOT_TO_KM_S
            ve, vn = speed * math.sin(hr), speed * math.cos(hr)
        else:
            ve = vn = 0.0

        planes.append({
            "callsign": callsign, "e": east, "n": north,
            "ve": ve, "vn": vn, "heading": heading, "gs": gs, "vstate": vstate,
        })
    return planes


def draw_legend():
    for i, (state, label) in enumerate((("level", "level"),
                                        ("climb", "climb / departing"),
                                        ("descent", "descent / approaching"))):
        row_y = 414 + i * 20
        display.set_pen(VSTATE_PENS[state])
        display.circle(14, row_y + 6, 3)
        display.set_pen(TEXT_COLOR)
        display.text(label, 24, row_y, WIDTH, 2)


def draw_scene(planes):
    draw_radar_grid()
    display.set_pen(TEXT_COLOR)
    display.text(f"Aircraft: {len(planes)}", 20, 20, WIDTH, 2)
    draw_legend()

    for p in planes:
        x, y = to_screen(p["e"], p["n"])
        if x < -40 or x > 520 or y < -40 or y > 520:
            continue  # drifted well off the display

        pen = VSTATE_PENS[p["vstate"]]
        display.set_pen(pen)
        display.circle(x, y, 3)
        if p["heading"] is not None and p["gs"] > 20:
            draw_track_arrow(x, y, p["heading"], p["gs"], pen)

        display.set_pen(TEXT_COLOR)
        display.text(p["callsign"], x + 8, y - 8, WIDTH, 2)

    presto.update()


# Initialisation
show_message("Connecting...")

try:
    wifi = presto.connect()
except ValueError as e:
    while True:
        show_message(str(e))
except ImportError as e:
    while True:
        show_message(str(e))


# --- MAIN LOOP ---
# Fetch every FETCH_INTERVAL_MS; in between, dead-reckon each aircraft forward
# along its last known track/speed and redraw every ANIM_INTERVAL seconds.
planes = []
next_fetch_ms = time.ticks_ms()   # fetch straight away
last_tick_ms = time.ticks_ms()

while True:
    now = time.ticks_ms()
    dt = time.ticks_diff(now, last_tick_ms) / 1000.0
    last_tick_ms = now

    # Advance the existing plane positions by dt seconds of their velocity.
    for p in planes:
        p["e"] += p["ve"] * dt
        p["n"] += p["vn"] * dt

    if time.ticks_diff(now, next_fetch_ms) >= 0:
        fresh = fetch_planes()
        if fresh is not None:
            planes = fresh
        next_fetch_ms = time.ticks_add(time.ticks_ms(), FETCH_INTERVAL_MS)
        last_tick_ms = time.ticks_ms()  # don't fast-forward across the fetch

    draw_scene(planes)

    # Check for touchscreen interaction to break loop/refresh manually
    if presto.touch.poll():
        pass

    time.sleep(ANIM_INTERVAL)
