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

# adsb.lol: no API key, query by point + radius so the response only holds
# nearby aircraft. Radius is in nautical miles, max 250. Keep it small: the
# response body is ~450 bytes per aircraft and the Presto has to buffer the
# whole thing plus the parsed dict in RAM (alongside the full-res framebuffer),
# so an oversized radius shows up as a JSON parse / memory error, not an HTTP
# error. Tune it with radar_debug.py on the desktop while watching the byte
# count. Over SF, 10 nm is ~11 KB / ~25 aircraft, 15 nm ~15 KB / ~35.
RADIUS_NM = 10

# Derive the bounding box used for the pixel mapping from the centre and radius,
# so the query circle inscribes the radar display. 1 degree of latitude is
# 60 nm; a degree of longitude shrinks by cos(latitude).
_LAT_SPAN = RADIUS_NM / 60.0
_LON_SPAN = RADIUS_NM / (60.0 * math.cos(math.radians(CENTER_LAT)))
MIN_LAT, MAX_LAT = CENTER_LAT - _LAT_SPAN, CENTER_LAT + _LAT_SPAN
MIN_LON, MAX_LON = CENTER_LON - _LON_SPAN, CENTER_LON + _LON_SPAN
RADAR_URL = f"https://api.adsb.lol/v2/point/{CENTER_LAT}/{CENTER_LON}/{RADIUS_NM}"

# adsb.lol rejects generic user agents ("user-agent too generic; include valid
# contact info"), so identify the app and give a contact URL.
USER_AGENT = "presto-radar/1.0 (+https://github.com/blech/presto-experiments)"

# --- INITIALIZE PRESTO ---
presto = Presto(full_res=True, ambient_light=True)
display = presto.display
WIDTH, HEIGHT = 480, 480

# Pen Colors (RGB)
BG_COLOR = display.create_pen(10, 20, 10)
RADAR_GREEN = display.create_pen(0, 230, 70)
PLANE_COLOR = display.create_pen(255, 255, 0)
TEXT_COLOR = display.create_pen(200, 255, 200)

def lat_lon_to_xy(lat, lon):
    # Map geographical bounding box to 480x480 pixel space
    x = int(((lon - MIN_LON) / (MAX_LON - MIN_LON)) * WIDTH)
    # Invert Y because pixel coordinates start at the top
    y = int((1.0 - ((lat - MIN_LAT) / (MAX_LAT - MIN_LAT))) * HEIGHT)
    return x, y

def draw_track_arrow(x, y, heading_deg, speed_kt):
    # heading_deg is degrees clockwise from north (the aircraft's track over the
    # ground). Screen y grows downwards, so north maps to -y.
    a = math.radians(heading_deg)
    dx, dy = math.sin(a), -math.cos(a)
    length = min(60, max(12, speed_kt * 0.15))  # ~knots -> pixels, clamped
    tip_x, tip_y = int(x + dx * length), int(y + dy * length)
    display.set_pen(PLANE_COLOR)
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
    # Two concentric radar rings
    ring(240, 240, 230)
    ring(240, 240, 120)
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
while True:
    draw_radar_grid()

    # Fetch flight data from adsb.lol. Free the previous loop's buffers first,
    # then read the body as text, close the socket, and only then parse -- this
    # keeps peak memory lower than request.json() and lets us report a parse
    # failure separately from a network failure.
    gc.collect()
    try:
        request = requests.get(RADAR_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        status = request.status_code
        body = request.text
        request.close()
    except Exception as e:
        show_message(f"Fetch failed: {e}")
        time.sleep(30)
        continue

    if status != 200:
        show_message(f"HTTP {status}\n{body[:200]}")
        time.sleep(30)
        continue

    try:
        data = json.loads(body)
    except ValueError as e:
        show_message(f"Bad JSON ({len(body)} bytes)\n{e}\n{body[:120]}")
        time.sleep(30)
        continue
    finally:
        body = None
        gc.collect()

    # adsb.lol returns {"ac": [ {aircraft}, ... ], "now": ..., "total": ...}
    flights = data.get("ac", [])
    if flights is None:
        flights = []

    display.set_pen(TEXT_COLOR)
    display.text(f"Airplanes Tracked: {len(flights)}", 20, 20, WIDTH, 2)

    for aircraft in flights:
        callsign = (aircraft.get("flight") or aircraft.get("hex", "")).strip()
        lat = aircraft.get("lat")
        lon = aircraft.get("lon")
        altitude = aircraft.get("alt_baro")  # feet, or the string "ground"

        if lat is not None and lon is not None:
            x, y = lat_lon_to_xy(lat, lon)

            # "track" is the direction of travel over the ground; it's absent for
            # stationary aircraft, so fall back to nose heading. ("dir" in the
            # feed is the bearing from the radar centre to the aircraft, not
            # where it's heading, so it isn't what we want here.)
            heading = aircraft.get("track")
            if heading is None:
                heading = aircraft.get("true_heading")
            gs = aircraft.get("gs")  # ground speed, knots

            # Position anchor, plus a velocity arrow when it's actually moving.
            display.set_pen(PLANE_COLOR)
            display.circle(x, y, 3)
            if heading is not None and gs and gs > 20:
                draw_track_arrow(x, y, heading, gs)

            # Draw short callsign snippet next to it if space permits
            display.set_pen(TEXT_COLOR)
            display.text(callsign, x + 8, y - 8, WIDTH, 2)


    presto.update()

    # Check for touchscreen interaction to break loop/refresh manually
    if presto.touch.poll():
        pass

    time.sleep(30)  # adsb.lol public endpoints are rate limited to ~1 req/sec
