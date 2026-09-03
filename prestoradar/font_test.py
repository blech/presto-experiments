"""
Font rendering report for the Presto at full res: what actually draws on THIS
firmware?

    mpremote run prestoradar/font_test.py

Tries, each in its own try/except and timed:

  - PicoGraphics bitmap8            -- the baseline the radar uses now
  - PicoGraphics built-in vector fonts (sans / gothic / serif ...), if this
    build still has them, via display.set_font() + display.text(size)
  - every *.af file on the device, via PicoVector

Background: radar.py's Roboto-Medium.af attempt (commit e49dede) threw
`NotImplementedError: opcode` -- the font uses an Alright-Fonts drawing opcode
the firmware's .af interpreter doesn't implement. The font loads; the first
glyph draw fails. In the radar that landed mid-frame (before presto.update()),
so the screen froze on the previous frame and every retry threw again.

If no vector option works, the bitmap font is the answer. Otherwise: regenerate
the .af from a TTF with curves flattened (see ../alright-fonts), or use whichever
built-in vector font renders here.
"""

import os
import time

from presto import Presto

ROWS = ["REG N29977", "TYPE B789", "RTE DEN-EWR", "ALT 37000ft",
        "VS -1216", "SPEED 334 kt", "TRACK 140", "DIST 12nm NW",
        "SQWK 1466", "ICAO A1B2C3"]


def log(*a):
    print("[{:8.3f}] {}".format(time.ticks_ms() / 1000,
                                " ".join(str(x) for x in a)))


def since(t0):
    return time.ticks_diff(time.ticks_ms(), t0)


presto = Presto(full_res=True)
display = presto.display
WIDTH, HEIGHT = display.get_bounds()
BG = display.create_pen(10, 20, 10)
FG = display.create_pen(225, 235, 225)
log("Presto full_res", WIDTH, "x", HEIGHT)


def block(name, draw_line):
    """draw_line(text, x, y) draws one line. Clears, draws 10 rows, updates,
    times it, and reports OK / FAIL without stopping the run."""
    try:
        display.set_pen(BG)
        display.clear()
        display.set_pen(FG)
        t0 = time.ticks_ms()
        y = 44
        for r in ROWS:
            draw_line(r, 20, y)
            y += 36
        dt = since(t0)
        presto.update()
        log("OK    %-24s  10 lines in %4d ms" % (name, dt))
        time.sleep(1.5)
        return True
    except Exception as e:  # noqa: BLE001
        log("FAIL  %-24s  %r" % (name, e))
        return False


# --- 1. bitmap8 baseline -----------------------------------------------
display.set_font("bitmap8")
block("bitmap8 (scale 2)", lambda s, x, y: display.text(s, x, y, WIDTH, 2))

# --- 2. PicoGraphics built-in vector fonts --------------------------
for f in ("sans", "gothic", "serif", "cursive", "serif_italic"):
    try:
        display.set_font(f)
    except Exception as e:  # noqa: BLE001
        log("FAIL  set_font(%r)             %r" % (f, e))
        continue
    if hasattr(display, "set_thickness"):
        display.set_thickness(2)
    block("PicoGraphics %s (h 24)" % f,
          lambda s, x, y: display.text(s, x, y, WIDTH, 24))
display.set_font("bitmap8")

# --- 3. every .af on the device, via PicoVector --------------------
try:
    import picovector
    from picovector import PicoVector, Transform

    vector = PicoVector(display)
    vector.set_transform(Transform())
    vector.set_antialiasing(getattr(picovector, "ANTIALIAS_FAST", 1))

    afs = []
    for d in (".", "/", "/prestoradar", "/fonts"):
        try:
            for n in os.listdir(d):
                if n.endswith(".af"):
                    afs.append(n if d in (".", "/") else d + "/" + n)
        except OSError:
            pass
    afs = sorted(set(afs))
    log(".af files found:", afs or "(none -- mpremote cp one to the device)")

    for af in afs:
        try:
            t0 = time.ticks_ms()
            vector.set_font(af, 22)
            log("      set_font(%r) in %d ms" % (af, since(t0)))
        except Exception as e:  # noqa: BLE001
            log("FAIL  set_font(%r)  %r" % (af, e))
            continue
        vector.set_font_size(22)
        block("PicoVector %s" % af.rsplit("/", 1)[-1],
              lambda s, x, y: vector.text(s, x, y))
except Exception as e:  # noqa: BLE001
    log("PicoVector unavailable:", repr(e))

log("done -- holding")
while True:
    time.sleep(1)
