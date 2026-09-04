"""
Font rendering report for the Presto at full res: what actually draws *legibly*
on THIS firmware?

    ../venv/bin/mpremote run prestoradar/font_test.py

Tries each backend in its own try/except and holds the sample on screen for a
few seconds. IMPORTANT: "OK" in the log only means "no exception" -- a font can
render as a scribble and still pass, so LOOK AT THE SCREEN (or photograph it).
The backend name is drawn in bitmap8 at the top of every frame so you always
know what you're looking at.

Backends:
  - PicoGraphics bitmap8 (the radar's current panel font)
  - PicoGraphics built-in vector fonts: sans / gothic / serif / ...
    NB on Presto firmware v2.0.0 these render as tangled strokes.
  - every *.af file on the device, via PicoVector
    NB Roboto-Medium.af threw `NotImplementedError: opcode` when
    set_font_letter_spacing/word_spacing were also set (radar.py e49dede).

If nothing vector is legible, the bitmap font stands. Otherwise: use whatever
renders here, or rebuild an .af from a TTF with curves flattened (../alright-fonts).
"""

import os
import time

from presto import Presto

HOLD_S = 4
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


def block(name, setup, draw_line):
    """setup() selects the font under test; draw_line(text, x, y) draws one row."""
    try:
        display.set_pen(BG)
        display.clear()
        display.set_font("bitmap8")          # header stays readable whatever else does
        display.set_pen(FG)
        display.text(name, 10, 8, WIDTH, 2)
        setup()
        t0 = time.ticks_ms()
        y = 60
        for r in ROWS:
            draw_line(r, 20, y)
            y += 36
        dt = since(t0)
        display.set_font("bitmap8")
        presto.update()
        log("OK    %-26s 10 lines %4d ms -- LOOK AT THE SCREEN" % (name, dt))
        time.sleep(HOLD_S)
        return True
    except Exception as e:  # noqa: BLE001
        display.set_font("bitmap8")
        log("FAIL  %-26s %r" % (name, e))
        return False


# --- 1. bitmap8 baseline -----------------------------------------------
block("bitmap8 scale 2",
      lambda: display.set_font("bitmap8"),
      lambda s, x, y: display.text(s, x, y, WIDTH, 2))

# --- 2. PicoGraphics built-in vector fonts --------------------------
_has_thick = hasattr(display, "set_thickness")
for f in ("sans", "gothic", "serif", "cursive", "serif_italic"):
    def setup(f=f):
        display.set_font(f)
        if _has_thick:
            display.set_thickness(2)
    block("PicoGraphics %s h24" % f, setup,
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
    log(".af files found:", afs or "(none)")

    for af in afs:
        try:
            vector.set_font(af, 22)
        except Exception as e:  # noqa: BLE001
            log("FAIL  set_font(%r) %r" % (af, e))
            continue
        # deliberately NOT calling set_font_letter_spacing / word_spacing here
        block("PicoVector %s" % af.rsplit("/", 1)[-1],
              lambda: vector.set_font_size(22),
              lambda s, x, y: vector.text(s, x, y))
except Exception as e:  # noqa: BLE001
    log("PicoVector unavailable:", repr(e))

log("done -- holding on the last sample")
while True:
    time.sleep(1)
