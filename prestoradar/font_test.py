"""
Standalone PicoVector font test + example for the Presto at full res.

    mpremote run prestoradar/font_test.py

Two jobs:

  1. A worked example of loading and drawing a vector font (Roboto-Medium.af) --
     load once, set the size, measure, draw rows of label/value text like the
     radar detail panel does.
  2. Timing, to explain the hard lock when radar.py tried this (commit e49dede):
     is PicoVector just slow on the 480x480 buffer, is calling set_font_size()
     once per line the cost, does antialiasing matter, or does something hang?

Every phase prints before and after, so if it locks, the last line tells you
where. Needs Roboto-Medium.af on the device -- if it's missing:

    mpremote cp presto/examples/Roboto-Medium.af :Roboto-Medium.af

(and maybe also  :prestoradar/Roboto-Medium.af  -- PicoVector resolves the path
relative to the working directory).
"""

import time

from presto import Presto

FONT = "Roboto-Medium.af"
ROWS = [
    ("REG", "N29977"), ("TYPE", "B789"), ("RTE", "DEN-EWR"),
    ("ALT", "37000ft"), ("VS", "-1216"), ("SPEED", "334 kt"),
    ("TRACK", "140"), ("DIST", "12nm NW"), ("SQWK", "1466"),
    ("ICAO", "A1B2C3"),
]


def log(*a):
    print("[{:8.3f}] {}".format(time.ticks_ms() / 1000,
                                " ".join(str(x) for x in a)))


def since(t0):
    return time.ticks_diff(time.ticks_ms(), t0)


presto = Presto(full_res=True)
display = presto.display
WIDTH, HEIGHT = display.get_bounds()
log("Presto full_res ready", WIDTH, "x", HEIGHT)

BG = display.create_pen(10, 20, 10)
FG = display.create_pen(220, 230, 220)
DIM = display.create_pen(150, 150, 150)

# --- load PicoVector -------------------------------------------------------
log("import picovector ...")
t0 = time.ticks_ms()
import picovector
from picovector import PicoVector, Transform
log("  imported in", since(t0), "ms")

AA_MODES = [(n, getattr(picovector, "ANTIALIAS_" + n))
            for n in ("NONE", "FAST", "X4", "BEST", "X16")
            if hasattr(picovector, "ANTIALIAS_" + n)]
log("antialias modes available:", [n for n, _ in AA_MODES])

t0 = time.ticks_ms()
vector = PicoVector(display)
log("PicoVector(display) in", since(t0), "ms")

# Some builds need a transform set before text() will draw.
t0 = time.ticks_ms()
vector.set_transform(Transform())
log("set_transform(Transform()) in", since(t0), "ms")

t0 = time.ticks_ms()
vector.set_font(FONT, 22)
log("set_font(%r, 22) in" % FONT, since(t0), "ms")

for setter in ("set_font_letter_spacing", "set_font_word_spacing"):
    if hasattr(vector, setter):
        getattr(vector, setter)(100)
log("spacing set")

if AA_MODES:
    vector.set_antialiasing(AA_MODES[min(1, len(AA_MODES) - 1)][1])  # FAST if present


# --- the example: draw label/value rows ---------------------------------
def draw_block(resize_each_line=False, size=22):
    display.set_pen(BG)
    display.clear()
    vector.set_font_size(size)
    y = size + 8
    for label, value in ROWS:
        if resize_each_line:
            vector.set_font_size(size)     # the suspected per-call cost
        display.set_pen(DIM)
        vector.text(label, 20, y)
        display.set_pen(FG)
        vector.text(value, 150, y)
        y += size + 8


# --- phase 1: one block, font size set once ---------------------------
log("draw_block(size once) ...")
t0 = time.ticks_ms()
draw_block(resize_each_line=False)
log("  drawn in", since(t0), "ms")
t0 = time.ticks_ms()
presto.update()
log("  presto.update() in", since(t0), "ms")

# --- phase 2: one block, set_font_size() per line --------------------
log("draw_block(resize each line) ...")
t0 = time.ticks_ms()
draw_block(resize_each_line=True)
log("  drawn in", since(t0), "ms")
presto.update()

# --- phase 3: 20 redraws each way, like a running panel --------------
for label, resize in (("size once", False), ("resize/line", True)):
    log("20 redraws (%s) ..." % label)
    t0 = time.ticks_ms()
    for _ in range(20):
        draw_block(resize_each_line=resize)
        presto.update()
    total = since(t0)
    log("  %s: %d ms total => %d ms/frame" % (label, total, total // 20))

# --- phase 4: does antialiasing dominate? ---------------------------
for name, mode in AA_MODES:
    vector.set_antialiasing(mode)
    t0 = time.ticks_ms()
    for _ in range(10):
        draw_block()
        presto.update()
    log("AA=%s: 10 frames in %d ms" % (name, since(t0)))

# --- phase 5: one large word -------------------------------------------
vector.set_font_size(72)
t0 = time.ticks_ms()
display.set_pen(BG)
display.clear()
display.set_pen(FG)
vector.text("Roboto 72", 20, 120)
log("one 72 px word in", since(t0), "ms")
presto.update()

# --- phase 6: measure_text ------------------------------------------
if hasattr(vector, "measure_text"):
    vector.set_font_size(22)
    for s in ("SPEED", "DEN-EWR", "N29977"):
        log("measure_text(%r) =" % s, vector.measure_text(s))

log("done -- holding")
while True:
    time.sleep(1)
