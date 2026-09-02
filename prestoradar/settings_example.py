"""
User-tunable settings for the flight radar -- template.

Copy this to `settings.py` (which is gitignored) and edit it for your location:

    cp prestoradar/settings_example.py prestoradar/settings.py

radar.py does `from settings import *`; make_basemap.py, radar_debug.py and
radar_listen.py import the specific names they need. Everything here is a plain
constant -- no imports, safe to pull in from anywhere. Derived values
(RADAR_URL, the km/degree factors, the pixel scale) live in radar.py.

The values below centre the radar on San Francisco, matching
example_sanfrancisco.png.
"""

# Centre of the radar. Deliberately low precision -- enough to place the display,
# not enough to pin down a building. make_basemap.py must be re-run after a
# change so the coastline is re-clipped and re-projected around the new centre.
CENTER_LAT = 37.74
CENTER_LON = -122.42

# Distance from the centre to the outer radar ring, in kilometres. The internal
# frame is kilometres east / north of the centre; the query radius sent to
# adsb.lol is derived from this (1 nm = 1.852 km). Keep it modest: the response
# is ~450 bytes per aircraft and the Presto buffers the whole body plus the
# parsed dict in RAM alongside the full-res framebuffer, so an oversized radius
# shows up as a JSON / memory error rather than an HTTP one. ~30 km over SF is
# ~15 KB / ~35 aircraft; tune with radar_debug.py while watching the byte count.
RADIUS_KM = 30

# adsb.lol rejects generic user agents ("user-agent too generic; include valid
# contact info"), so identify the app and give a contact URL.
USER_AGENT = "presto-radar/1.0 (+https://github.com/blech/presto-experiments)"

FETCH_INTERVAL_MS = 30_000   # adsb.lol public endpoints allow ~1 request/second
ANIM_INTERVAL = 0.5          # seconds between dead-reckoning redraws (~2 fps)

DRAW_BASEMAP = 1             # 0 to skip the coastline layer entirely
SKIP_NETWORK = 0             # 1 = don't connect or fetch, just draw grid + basemap

# How aircraft are drawn:
#   "radar" - scope style: a blip, a track arrow, and the callsign next to it.
#   "map"   - a small plane icon pointed along the track, no callsign. Better
#             for busy airspace where callsign tags pile up (e.g. a runway
#             approach). A richer basemap for this mode is planned -- PLAN item 8.
DISPLAY_MODE = "radar"

# Logging: every log() line goes to the serial console, and -- once the network
# is up -- is also broadcast as a UDP packet so another machine on the LAN can
# watch with `python3 prestoradar/radar_listen.py`. Set LOG_UDP_PORT to 0 to
# disable the broadcast.
LOG_UDP_PORT = 47269
LOG_UDP_ADDR = ("255.255.255.255", LOG_UDP_PORT)

# When 1, hide aircraft that are on the ground: altitude of 0 / "ground", or a
# ground speed of 0. When 0, show everything.
HIDE_ON_GROUND = 1

# Screenshots (PLAN item 4). Writing the framebuffer to flash deadlocks on the
# current firmware, so the radar runs a small TCP server on this port instead:
#   python3 prestoradar/screenshot_pull.py <presto-ip>
SCREENSHOT_PORT = 8011

# Rather than plotting altitude, colour each aircraft by what it's doing
# vertically. "baro_rate" (feet/minute, quantised to 64) is the climb/descent
# rate; "geom_rate" is the GPS-derived fallback. Anything within this band of
# zero -- or with no rate reported -- counts as flying level.
LEVEL_RATE_FPM = 256
