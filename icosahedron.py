# ICON deployed-code
# NAME Icosahedron
# DESC One Gyron hedroid, coming up

# A MicroPython/Presto port of gyron_icosahedron.html, rendered through
# PicoVector instead of hand-rolled pixel plotting. The rotation/lighting/
# shading rules below are a direct translation of that HTML demo so the two
# should look and behave the same, modulo screen resolution.
import math

from picovector import ANTIALIAS_FAST, PicoVector, Polygon
from presto import Presto

# Presto() (rather than Presto(full_res=True)) renders at the doubled-pixel
# 240x240 resolution, which mirrors the HTML demo's low-res offscreen buffer
# and keeps the per-frame triangle fill cheap enough to animate smoothly.
presto = Presto()
display = presto.display
touch = presto.touch
WIDTH, HEIGHT = display.get_bounds()
CX, CY = WIDTH // 2, HEIGHT // 2

vector = PicoVector(display)
vector.set_antialiasing(ANTIALIAS_FAST)

# This is a recreation of the loading screen's red-on-grey palette; the
# in-game icosahedron icon was green-on-black instead, and used dithering
# to fake shading because the Spectrum could only show one foreground and
# one background colour per 8x8 attribute cell. Flip this to switch which
# one gyron_icosahedron.py reproduces.
MODE_SHADED = 1
MODE_DITHERED = 0
RENDER_MODE = MODE_SHADED

BLACK = display.create_pen(0, 0, 0)
WHITE = display.create_pen(216, 216, 216)
# Every non-black pixel sampled from a screenshot of the in-game icon and
# HUD (lives bar, icosahedron icon) came back as pure (0, 255, 0) - this
# loading/game screen appears to set the Spectrum's BRIGHT attribute
# throughout, so there's no dim/non-BRIGHT green sample to measure from it.
# The commonly documented non-BRIGHT Spectrum green is roughly (0, 215, 0)
# if you'd rather try the dimmer tone instead.
GREEN = display.create_pen(0, 255, 0)
BG = BLACK

# ---------- MODE_SHADED palette: continuous black -> green -> white ramp ----------
_GREEN = (0, 255, 0)
_WHITE = (255, 255, 255)


def _lerp(a, b, t):
    return a + (b - a) * t


def _ramp_rgb(brightness):
    if brightness < 0.5:
        t = brightness * 2
        return (int(_lerp(0, _GREEN[0], t)), int(_lerp(0, _GREEN[1], t)), int(_lerp(0, _GREEN[2], t)))
    t = (brightness - 0.5) * 2
    return (int(_lerp(_GREEN[0], _WHITE[0], t)), int(_lerp(_GREEN[1], _WHITE[1], t)), int(_lerp(_GREEN[2], _WHITE[2], t)))


# Precompute a lookup table of pens across the brightness range instead of
# creating a new pen for every triangle on every frame.
PALETTE_STEPS = 64
PALETTE = [display.create_pen(*_ramp_rgb(i / (PALETTE_STEPS - 1))) for i in range(PALETTE_STEPS)]


def pen_for_brightness(brightness):
    idx = int(brightness * (PALETTE_STEPS - 1) + 0.5)
    if idx < 0:
        idx = 0
    elif idx >= PALETTE_STEPS:
        idx = PALETTE_STEPS - 1
    return PALETTE[idx]


# ---------- MODE_DITHERED: two-colour ordered dither, Spectrum-style ----------
# A classic 4x4 Bayer matrix - thresholding a pixel's (x, y)-indexed matrix
# value against the face's brightness spreads "on" pixels out evenly rather
# than clumping them, the same trick 8-bit era games leaned on to fake more
# shades than their hardware could actually display at once.
_BAYER4 = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)


def fill_triangle_dithered(p0, p1, p2, brightness):
    minx = max(0, int(math.floor(min(p0[0], p1[0], p2[0]))))
    maxx = min(WIDTH - 1, int(math.ceil(max(p0[0], p1[0], p2[0]))))
    miny = max(0, int(math.floor(min(p0[1], p1[1], p2[1]))))
    maxy = min(HEIGHT - 1, int(math.ceil(max(p0[1], p1[1], p2[1]))))

    area = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p2[0] - p0[0]) * (p1[1] - p0[1])
    if area == 0:
        return

    # display.set_pen(GREEN) is set once by the caller; unlit ("off") dither
    # pixels are simply skipped since the frame was already cleared to black.
    for y in range(miny, maxy + 1):
        py = y + 0.5
        bayer_row = _BAYER4[y & 3]
        run_start = None
        for x in range(minx, maxx + 1):
            px = x + 0.5
            w0 = (p1[0] - px) * (p2[1] - py) - (p2[0] - px) * (p1[1] - py)
            w1 = (p2[0] - px) * (p0[1] - py) - (p0[0] - px) * (p2[1] - py)
            w2 = (p0[0] - px) * (p1[1] - py) - (p1[0] - px) * (p0[1] - py)
            inside = (w0 >= 0 and w1 >= 0 and w2 >= 0) or (w0 <= 0 and w1 <= 0 and w2 <= 0)
            on = inside and brightness > (bayer_row[x & 3] + 0.5) / 16.0
            if on:
                if run_start is None:
                    run_start = x
            elif run_start is not None:
                display.pixel_span(run_start, y, x - run_start)
                run_start = None
        if run_start is not None:
            display.pixel_span(run_start, y, maxx + 1 - run_start)


# ---------- icosahedron geometry ----------
PHI = (1 + math.sqrt(5)) / 2
_RAW_VERTS = [
    (-1, PHI, 0), (1, PHI, 0), (-1, -PHI, 0), (1, -PHI, 0),
    (0, -1, PHI), (0, 1, PHI), (0, -1, -PHI), (0, 1, -PHI),
    (PHI, 0, -1), (PHI, 0, 1), (-PHI, 0, -1), (-PHI, 0, 1),
]

FACES = [
    (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
    (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
    (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
    (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
]

# Scale geometry/camera relative to screen size, keeping the same
# proportions as the HTML demo's 224px-wide offscreen buffer.
_SCALE = WIDTH / 224.0
RADIUS = 64 * _SCALE
CAM_DIST = 256 * _SCALE
FOCAL = 256 * _SCALE


def normalize3(v):
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / length, v[1] / length, v[2] / length)


VERTS = [tuple(c * RADIUS for c in normalize3(v)) for v in _RAW_VERTS]
# Screen y grows downward, so a negative y component here puts the
# highlight towards the top of the screen (x stays positive for "right").
LIGHT = normalize3((0.45, -0.6, -1))


def rotate_x(v, a):
    c, s = math.cos(a), math.sin(a)
    return (v[0], v[1] * c - v[2] * s, v[1] * s + v[2] * c)


def rotate_y(v, a):
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c + v[2] * s, v[1], -v[0] * s + v[2] * c)


def project(v):
    z = v[2] + CAM_DIST
    return (v[0] * FOCAL / z + CX, v[1] * FOCAL / z + CY)


# ---------- rotation / drag-inertia state (mirrors the HTML pointer rules) ----------
rx, ry = 0.5, 0.35
IDLE_VRX, IDLE_VRY = 0.004, 0.008
vrx, vry = IDLE_VRX, IDLE_VRY

dragging = False
last_x = last_y = 0

while True:
    # ----- drag to rotate -----
    if touch.state:
        x, y = touch.x, touch.y
        if not dragging:
            dragging = True
        else:
            # Presto's touch panel reads x mirrored relative to the display,
            # so flip dx to keep drag direction matching the HTML original.
            dx, dy = last_x - x, y - last_y
            ry += dx * 0.01
            rx += dy * 0.01
            vry = dx * 0.0016
            vrx = dy * 0.0016
        last_x, last_y = x, y
    else:
        dragging = False

    if not dragging:
        rx += vrx
        ry += vry
        vrx += (IDLE_VRX - vrx) * 0.003
        vry += (IDLE_VRY - vry) * 0.003

    # ----- transform + project -----
    tverts = [rotate_y(rotate_x(v, rx), ry) for v in VERTS]
    pverts = [project(v) for v in tverts]

    visible_faces = []
    for f in FACES:
        a, b, c = tverts[f[0]], tverts[f[1]], tverts[f[2]]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        wx, wy, wz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx = uy * wz - uz * wy
        ny = uz * wx - ux * wz
        nz = ux * wy - uy * wx
        cenx = (a[0] + b[0] + c[0]) / 3
        ceny = (a[1] + b[1] + c[1]) / 3
        cenz = (a[2] + b[2] + c[2]) / 3

        # True perspective view vector from the camera to this face's centre,
        # not a constant (0, 0, 1) axis - that shortcut only holds near screen
        # centre and lets back faces flicker into view at the silhouette.
        viewx, viewy, viewz = cenx, ceny, cenz + CAM_DIST
        facing = nx * viewx + ny * viewy + nz * viewz
        if facing >= 0:
            continue

        nlen = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        diff = max(0.0, (nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]) / nlen)
        # Weaker diffuse term than the HTML original (0.88) - that let a
        # directly-lit face hit brightness 1.0, i.e. pure white. Capping it
        # around 0.5 keeps the brightest face a vivid green with only a
        # slight white tint, at the cost of dimming every other face too.
        brightness = 0.12 + diff * 0.5
        visible_faces.append((f, brightness))

    display.set_pen(BG)
    display.clear()

    # Convex solid + backface culling means visible faces never overlap in
    # screen space, so they can be filled in any order with no depth sort.
    if RENDER_MODE == MODE_DITHERED:
        display.set_pen(GREEN)
        for f, brightness in visible_faces:
            p0, p1, p2 = pverts[f[0]], pverts[f[1]], pverts[f[2]]
            fill_triangle_dithered(p0, p1, p2, brightness)
    else:
        for f, brightness in visible_faces:
            p0, p1, p2 = pverts[f[0]], pverts[f[1]], pverts[f[2]]
            tri = Polygon()
            tri.path(p0, p1, p2)
            display.set_pen(pen_for_brightness(brightness))
            vector.draw(tri)

    visible_edges = set()
    for f, _ in visible_faces:
        for i in range(3):
            a, b = f[i], f[(i + 1) % 3]
            visible_edges.add((a, b) if a < b else (b, a))

    display.set_pen(WHITE)
    for a, b in visible_edges:
        p0, p1 = pverts[a], pverts[b]
        display.line(int(p0[0]), int(p0[1]), int(p1[0]), int(p1[1]))

    presto.update()
