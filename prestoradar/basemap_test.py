"""
Standalone basemap drawing test -- no WiFi, no fetch.

Draws the radar grid and the generated coastline / airports once, ring by ring,
logging the extent and draw time of each ring and calling presto.update() after
every one so a hang or crash can be pinned to a specific ring. Then it just
holds the image.

    mpremote run prestoradar/basemap_test.py

Every line segment is clipped to the 480x480 viewport with Cohen-Sutherland
before it reaches display.line(), so off-screen coordinates (the coastline
rings run out to a 50 km clip box, well beyond the screen) can't feed absurd
values into the graphics library.
"""

import sys
import time

if "/prestoradar" not in sys.path:
    sys.path.insert(0, "/prestoradar")

from presto import Presto
import basemap_data

WIDTH = HEIGHT = 480
RADIUS_KM = 30
PX_PER_KM = 230.0 / RADIUS_KM


def log(*parts):
    print("[{:7.2f}] {}".format(time.ticks_ms() / 1000,
                                " ".join(str(p) for p in parts)))


def to_screen(e_km, n_km):
    return int(240 + e_km * PX_PER_KM), int(240 - n_km * PX_PER_KM)


# --- Cohen-Sutherland clip to [0,WIDTH) x [0,HEIGHT) ---
_L, _R, _B, _T = 1, 2, 4, 8


def _code(x, y):
    c = 0
    if x < 0:
        c |= _L
    elif x > WIDTH - 1:
        c |= _R
    if y < 0:
        c |= _B
    elif y > HEIGHT - 1:
        c |= _T
    return c


def clip(x0, y0, x1, y1):
    c0, c1 = _code(x0, y0), _code(x1, y1)
    while True:
        if not (c0 | c1):
            return x0, y0, x1, y1
        if c0 & c1:
            return None
        c = c0 or c1
        if c & _T:
            x = x0 + (x1 - x0) * (HEIGHT - 1 - y0) / (y1 - y0)
            y = HEIGHT - 1
        elif c & _B:
            x = x0 + (x1 - x0) * (0 - y0) / (y1 - y0)
            y = 0
        elif c & _R:
            y = y0 + (y1 - y0) * (WIDTH - 1 - x0) / (x1 - x0)
            x = WIDTH - 1
        else:
            y = y0 + (y1 - y0) * (0 - x0) / (x1 - x0)
            x = 0
        if c == c0:
            x0, y0, c0 = x, y, _code(x, y)
        else:
            x1, y1, c1 = x, y, _code(x, y)


def main():
    log("Presto init...")
    presto = Presto(full_res=True, ambient_light=True)
    display = presto.display
    log("display ready")

    bg = display.create_pen(10, 20, 10)
    green = display.create_pen(0, 230, 70)
    coast = display.create_pen(80, 120, 160)
    airport = display.create_pen(150, 130, 170)

    display.set_pen(bg)
    display.clear()
    display.set_pen(green)
    for r in (230, 115):
        display.circle(240, 240, r)
        display.set_pen(bg)
        display.circle(240, 240, r - 3)
        display.set_pen(green)
    display.line(240, 10, 240, 470)
    display.line(10, 240, 470, 240)
    presto.update()
    log("grid shown")

    rings = basemap_data.COASTLINE
    log("coastline rings:", len(rings))
    display.set_pen(coast)
    for i, ring in enumerate(rings):
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        log("ring", i, "pts", len(ring),
            "e[%.0f..%.0f] n[%.0f..%.0f]" % (min(xs), max(xs), min(ys), max(ys)))
        t = time.ticks_ms()
        drawn = 0
        px, py = to_screen(*ring[0])
        for pt in ring[1:]:
            cx, cy = to_screen(pt[0], pt[1])
            seg = clip(px, py, cx, cy)
            if seg is not None:
                display.line(int(seg[0]), int(seg[1]), int(seg[2]), int(seg[3]))
                drawn += 1
            px, py = cx, cy
        log("  ring", i, "drawn", drawn, "segs in",
            time.ticks_diff(time.ticks_ms(), t), "ms")
        presto.update()

    for name, e, n in getattr(basemap_data, "AIRPORTS", ()):
        x, y = to_screen(e, n)
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            display.set_pen(airport)
            display.circle(x, y, 3)
            display.text(name, x + 5, y - 4, WIDTH, 1)
    presto.update()
    log("done -- holding")

    while True:
        time.sleep(1)


main()
