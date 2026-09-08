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

# Which airports make_basemap.py bakes into basemap_data.py, by size:
#   "large"  - large_airport only
#   "medium" - large + medium (default)
#   "small"  - large + medium + small
#   "none"   - no airport marks
# deploy.sh regenerates basemap_data.py when this changes; `make_basemap.py
# --airports <tier>` overrides it for a one-off build.
BASEMAP_AIRPORTS = "medium"

# How aircraft are drawn:
#   "radar" - scope style: a blip, a track arrow, and the callsign next to it.
#   "map"   - a small plane icon pointed along the track, no callsign. Better
#             for busy airspace where callsign tags pile up (e.g. a runway
#             approach). If a raster backdrop has been built
#             (`make_basemap.py --raster <image>` -> basemap.jpg, deployed) it
#             replaces the green grid, composited under the aircraft on a second
#             layer. The layer count is fixed at boot, so this only engages when
#             "map" is set *here* -- toggling to map from the on-device settings
#             overlay keeps the vector basemap.
DISPLAY_MODE = "radar"

# Aircraft colour:
#   "alt"  - by vertical state: white level, cyan climbing, amber descending,
#            with a legend.
#   "mono" - everything radar-green, for a purer scope look (no legend).
COLOUR_MODE = "alt"

# Logging: every log() line goes to the serial console, and -- once the network
# is up -- is also sent as a UDP multicast datagram (via lib/netlog.py) so
# another machine on the LAN can watch with `python3 prestoradar/radar_listen.py`.
# This is the multicast port; 0 disables the network side (serial only).
LOG_UDP_PORT = 32301

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

# Position-history trail for the selected aircraft (radar mode). When 1, tapping
# an aircraft seeds its trail from adsb.lol's trace_recent (the last ~5 min of
# real track). When 0, the trail is built live from the poll loop instead --
# one fix per fetch. The in-RAM live trail is always kept either way; this only
# toggles the network seed. See DATA_TRACE.md.
TRACE_SEED = 1
