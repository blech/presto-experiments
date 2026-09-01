import network
import time
import requests
from presto import Presto

# Define your bounding box coordinates (approx 0.5 to 1 degree radius around your spot)
MIN_LAT = 37.0
MAX_LAT = 38.0
MIN_LON = -122.5
MAX_LON = -121.5

CENTER_LAT = (MIN_LAT + MAX_LAT) / 2
CENTER_LON = (MIN_LON + MAX_LON) / 2
OPENSKY_URL = f"https://opensky-network.org{MIN_LAT}&lamin={MIN_LON}&lamax={MAX_LAT}&lamax={MAX_LON}"

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

def draw_radar_grid():
    display.set_pen(BG_COLOR)
    display.clear()
    display.set_pen(RADAR_GREEN)
    # Concentric radar rings
    display.circle(240, 240, 230)
    display.circle(240, 240, 150)
    display.circle(240, 240, 70)
    # Crosshairs
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
        show_message(e)
except ImportError as e:
    while True:
        show_message(e)


# --- MAIN LOOP ---
while True:
    draw_radar_grid()

    # Fetch flight data
    request = requests.get(OPENSKY_URL)
    data = request.json()

    flights = data.get("states", [])
    if flights is None:
        flights = []

    display.set_pen(TEXT_COLOR)
    display.text(f"Airplanes Tracked: {len(flights)}", 20, 20, WIDTH, 2)

    for flight in flights:
        callsign = flight[1].strip()
        lon = flight[5]
        lat = flight[6]
        altitude = flight[7] # in meters

        if lat and lon:
            x, y = lat_lon_to_xy(lat, lon)

            # Draw the aircraft indicator
            display.set_pen(PLANE_COLOR)
            display.circle(x, y, 6)

            # Draw short callsign snippet next to it if space permits
            display.set_pen(TEXT_COLOR)
            display.text(callsign, x + 8, y - 6, WIDTH, 1)


    presto.update()

    # Check for touchscreen interaction to break loop/refresh manually
    if presto.touch.poll():
        pass

    time.sleep(30) # OpenSky public API allows requests every 10-15 seconds
