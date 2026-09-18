"""
Font-at-full-res probe for the Presto -- one step per invocation, hang-safe.

Background
----------
prestoradar has repeatedly tried to put a nicer-than-bitmap font on the
480x480 (full_res) display and backed out every time (render.py's note:
"PicoVector + Roboto-Medium.af -> NotImplementedError: opcode", and the
built-in "sans" vector font "renders as a scribble of strokes").  Meanwhile
presto/examples/co2.py runs PicoVector text on osansb.af at full_res quite
happily.  This probe works out, empirically and on *this* firmware, exactly
which (display config x text backend x font file) combinations work, fail
with a catchable exception, or hard-hang the board.

    firmware seen while writing this: 3.4.0 / MicroPython 1.29.0
    "feature/presto-wireless-august-2026", _mpy=7942

Why stepped, and why every step hard-resets the board
----------------------------------------------------
Two separate wedge hazards on this firmware:

  1. Once Presto(full_res=True) has been brought up in a process, mpremote
     can no longer soft-reset the board to start the next one -- the raw
     REPL stops responding ("could not enter raw repl") and only a hard
     reset clears it.  (Seen even for a full_res step that only used the
     bitmap font -- nothing to do with PicoVector.)  So every step ends by
     calling machine.reset() itself: it records its result to flash first,
     then hard-resets.  mpremote sees the connection drop; the board
     re-enumerates in a second or two, clean, ready for the next step.

  2. A bad .af file at full_res doesn't raise -- it hangs the RP2350 in
     native code before machine.reset() is ever reached.  That step's line
     in /font_probe.results is left stuck at "START", which is exactly how
     the driver and summary() identify the culprit.  Only a physical
     RESET clears this one.

Each *step* is therefore a separate process:

  * this module's main(n) runs ONE combination
  * it appends "n|name|START" to /font_probe.results BEFORE the risky call,
    then "n|name|OK|<ms>" or "n|name|FAIL|<repr>" after
  * then it hard-resets the board (hazard 1)
  * a line stuck at "START" == the combination that hung the board (hazard 2)

font_probe.sh drives the whole matrix: it runs the steps in order, waits
for the board to re-enumerate after each self-reset, and prints a table at
the end.  Run it; if it says the board is wedged, press RESET and run
it again -- it resumes after the last recorded step.

    prestoradar/dev/font_probe.sh              # whole matrix
    prestoradar/dev/font_probe.sh --fresh      # wipe results first
    prestoradar/dev/font_probe.sh --hold       # pause after each step to look
    FROM=6 TO=9 prestoradar/dev/font_probe.sh  # just those steps

Run one step by hand (the board hard-resets itself the moment it's done,
so mpremote will report a lost connection -- that's expected):

    ../venv/bin/mpremote cp prestoradar/dev/font_probe.py :font_probe.py
    ../venv/bin/mpremote exec "import font_probe; font_probe.main(6)"
    # board resets; reconnect for the next:
    ../venv/bin/mpremote exec "import font_probe; font_probe.main(7)"
    ../venv/bin/mpremote exec "import font_probe; font_probe.summary()"

Pass hard_reset=False to inspect the board after a step (only safe for the
half-res steps -- a full_res step will then wedge the next mpremote call):

    ../venv/bin/mpremote exec "import font_probe; font_probe.main(20, hard_reset=False)"

IMPORTANT: "OK" means "no exception and it drew something" -- it does NOT
mean the text was legible.  A vector font can render as tangled strokes and
still pass.  LOOK AT THE SCREEN (or photograph it) for every OK step; the
config + backend + font are drawn across the top in bitmap8 so a photo is
self-labelling.
"""

import gc
import os
import time

RESULTS = "/font_probe.results"
SAMPLE = ["REG N29977", "TYPE B789 / A320", "ALT 37000  VS -1216",
          "Sphinx of black quartz", "0123456789  ()[]{}<>", "the quick brown fox"]

# .af font files to try, in ascending order of "how likely to be trouble".
# osansb is the one Pimoroni ship co2.py against; the Roboto pair carry the
# FLAG_16BIT_POINT_COUNT header flag (byte 5 == 0x01) that the simpler fonts
# do not, and Roboto-Medium.af is the exact file render.py caught the opcode
# error on.
AF_FONTS = ["osansb.af", "cherry-hq.af", "Roboto-Medium.af",
            "Roboto-Medium-With-Material-Symbols.af"]

# Display configs. Presto() can only really be brought up once per process,
# so each is exercised in its own step (its own fresh VM).
#   (True, 1)  -- what co2.py / vector_clock_full.py use: full_res, 1 layer
#   (True, 2)  -- what radar.py forces: full_res + a compositing 2nd layer
#   (False, 2) -- half-res 240x240 default, as a known-good control
CONFIGS = [(True, 1), (True, 2), (False, 2)]


def _steps():
    """Build the (config, backend, font, spacing) matrix as a flat list.
    Kept in a function so main() and summary() agree on the numbering."""
    steps = []
    for full_res, layers in CONFIGS:
        cfg = "fr{}L{}".format(1 if full_res else 0, layers)
        # 1. bitmap control -- must always work
        steps.append((full_res, layers, "bitmap8", None, False, cfg))
        # 2. PicoGraphics built-in hershey vector font
        steps.append((full_res, layers, "hershey:sans", None, False, cfg))
        # 3. PicoVector + each .af, without then with letter/word spacing.
        #    The full matrix of fonts only for the two full_res configs (the
        #    half-res control just needs one to prove the backend itself is
        #    fine); spacing on/off for every one because render.py's note
        #    fingered set_font_letter_spacing as part of the opcode repro.
        fonts = AF_FONTS if full_res else AF_FONTS[:1]
        for font in fonts:
            steps.append((full_res, layers, "vector", font, False, cfg))
            steps.append((full_res, layers, "vector", font, True, cfg))
    return steps


def _log(line):
    print(line)
    try:
        with open(RESULTS, "a") as f:
            f.write(line + "\n")
        try:
            os.sync()          # make sure it's on flash before any hard reset
        except (AttributeError, OSError):
            pass
    except OSError as e:
        print("(could not append to %s: %r)" % (RESULTS, e))


def _mark(n, name, status, detail=""):
    _log("%d|%s|%s|%s" % (n, name, status, detail))


def main(n, hard_reset=True):
    steps = _steps()
    if n < 0 or n >= len(steps):
        print("step %d out of range 0..%d" % (n, len(steps) - 1))
        return
    try:
        _run_step(n, steps[n])
    finally:
        if hard_reset:
            print("hard-resetting the board (see module docstring, hazard 1)")
            try:
                os.sync()
            except (AttributeError, OSError):
                pass
            time.sleep(0.2)
            import machine
            machine.reset()


def _run_step(n, step):
    full_res, layers, backend, font, spacing, cfg = step
    name = "%s %s%s%s" % (cfg, backend,
                          "/" + font if font else "",
                          " +spacing" if spacing else "")
    print("=== step %d: %s ===" % (n, name))

    # Record that this step was *started* before touching anything that can
    # hang.  If the board wedges here, summary() / font_probe.sh sees the
    # dangling START and names this combination as the culprit.
    _mark(n, name, "START")

    try:
        from presto import Presto
        presto = Presto(full_res=full_res, layers=layers)
        display = presto.display
        W, H = display.get_bounds()
        BG = display.create_pen(8, 12, 20)
        FG = display.create_pen(235, 240, 245)
        HDR = display.create_pen(255, 210, 90)

        display.set_pen(BG)
        display.clear()
        display.set_font("bitmap8")
        display.set_pen(HDR)
        display.text("step %d  %s" % (n, name), 6, 6, W, 2)
        display.text("%dx%d" % (W, H), 6, 26, W, 2)

        t0 = time.ticks_ms()

        if backend == "bitmap8":
            display.set_font("bitmap8")
            display.set_pen(FG)
            y = 70
            for s in SAMPLE:
                display.text(s, 16, y, W - 20, 3)
                y += 48

        elif backend.startswith("hershey:"):
            fname = backend.split(":", 1)[1]
            display.set_font(fname)
            if hasattr(display, "set_thickness"):
                display.set_thickness(2)
            display.set_pen(FG)
            y = 80
            for s in SAMPLE:
                display.text(s, 16, y, W - 20, 28)  # 28 px target height
                y += 56
            display.set_font("bitmap8")

        elif backend == "vector":
            import picovector
            from picovector import PicoVector, Transform
            vector = PicoVector(display)
            vector.set_transform(Transform())
            vector.set_antialiasing(getattr(picovector, "ANTIALIAS_FAST", 1))
            ok = vector.set_font(font, 34)
            if spacing:
                vector.set_font_letter_spacing(95)
                vector.set_font_word_spacing(100)
                vector.set_font_line_height(110)
            display.set_pen(FG)
            # measure_text first -- it walks the same glyph tables text()
            # does, so if the font is going to blow up it usually does it
            # here, before anything is on screen.
            mx, my, mw, mh = vector.measure_text(SAMPLE[0])
            y = 90
            for s in SAMPLE:
                vector.text(s, 16, y)
                y += 52
            display.set_font("bitmap8")
            _mark(n, name, "note", "set_font=%r measure=(%d,%d,%d,%d)" %
                  (ok, mx, my, mw, mh))

        else:
            _mark(n, name, "FAIL", "unknown backend %r" % backend)
            return

        dt = time.ticks_diff(time.ticks_ms(), t0)
        presto.update()
        gc.collect()
        _mark(n, name, "OK", "%d ms  mem_free=%d  LOOK AT SCREEN" %
              (dt, gc.mem_free()))

    except Exception as e:  # noqa: BLE001 -- want everything, incl. MemoryError
        _mark(n, name, "FAIL", repr(e))


def show(full_res=True, layers=1, backend="vector", font="osansb.af",
         spacing=False, size=34, mask_top=0):
    """Draw ONE combination big, hold it on screen forever, and print a
    one-line verdict.  Does NOT touch /font_probe.results and does NOT reset
    -- meant to be launched detached so the frame stays up for a photo:

        mask_top=N repaints the top N px in the background colour AFTER the
        text pass.  vector.text() at full_res leaves a thin band of
        uninitialised pixels along the top edge (~y 0-30); mask_top=34 hides
        it.  Confirmed on 3.4.0 -- see FONT-FINDINGS.md.


        ../venv/bin/mpremote cp prestoradar/dev/font_probe.py :font_probe.py
        printf 'import font_probe\\nfont_probe.show(full_res=True, layers=2,
          backend="vector", font="osansb.af")\\n' > /tmp/s.py
        ../venv/bin/mpremote run --no-follow /tmp/s.py
        # look at / photograph the screen, then physically RESET for the next

    --no-follow matters: it detaches instead of soft-resetting the board on
    exit, which is the operation that wedges this firmware after full_res.
    """
    label = "fr%dL%d %s%s%s" % (1 if full_res else 0, layers, backend,
                                "/" + font if font else "",
                                " +sp" if spacing else "")
    print("show:", label)
    try:
        from presto import Presto
        presto = Presto(full_res=full_res, layers=layers)
        display = presto.display
        W, H = display.get_bounds()
        BG = display.create_pen(8, 12, 20)
        FG = display.create_pen(235, 240, 245)
        HDR = display.create_pen(255, 210, 90)
        display.set_pen(BG)
        display.clear()
        display.set_font("bitmap8")
        display.set_pen(HDR)
        display.text(label, 6, 6, W, 2)
        display.text("%dx%d" % (W, H), 6, 26, W, 2)

        t0 = time.ticks_ms()
        if backend == "bitmap8":
            display.set_pen(FG)
            y = 70
            for s in SAMPLE:
                display.text(s, 16, y, W - 20, 3)
                y += 48
        elif backend.startswith("hershey:"):
            display.set_font(backend.split(":", 1)[1])
            if hasattr(display, "set_thickness"):
                display.set_thickness(2)
            display.set_pen(FG)
            y = 80
            for s in SAMPLE:
                display.text(s, 16, y, W - 20, 28)
                y += 56
            display.set_font("bitmap8")
        elif backend == "vector":
            import picovector
            from picovector import PicoVector, Transform
            vector = PicoVector(display)
            vector.set_transform(Transform())
            vector.set_antialiasing(getattr(picovector, "ANTIALIAS_FAST", 1))
            ok = vector.set_font(font, size)
            if spacing:
                vector.set_font_letter_spacing(95)
                vector.set_font_word_spacing(100)
                vector.set_font_line_height(110)
            mx, my, mw, mh = vector.measure_text(SAMPLE[0])
            print("  set_font=%r measure=(%d,%d,%d,%d)" % (ok, mx, my, mw, mh))
            display.set_pen(FG)
            y = 96
            for s in SAMPLE:
                vector.text(s, 16, y)
                y += 54
            display.set_font("bitmap8")
        if mask_top:
            display.set_pen(BG)
            display.rectangle(0, 0, W, mask_top)
            display.set_pen(HDR)
            display.text(label, 6, 6, W, 2)
        dt = time.ticks_diff(time.ticks_ms(), t0)
        presto.update()
        gc.collect()
        print("  drew in %d ms, mem_free=%d -- holding; RESET when done" %
              (dt, gc.mem_free()))
    except Exception as e:  # noqa: BLE001
        print("  FAIL", repr(e))
        try:
            presto.update()
        except Exception:
            pass
    while True:
        time.sleep(60)


def count():
    return len(_steps())


def summary():
    steps = _steps()
    seen = {}
    try:
        with open(RESULTS) as f:
            for ln in f:
                parts = ln.rstrip("\n").split("|", 3)
                if len(parts) < 3:
                    continue
                i = int(parts[0])
                seen.setdefault(i, []).append(parts[1:])
    except OSError:
        print("no %s on the board yet" % RESULTS)
        return

    print()
    print("step  result   name")
    print("----  -------  ----------------------------------------")
    for i in range(len(steps)):
        rows = seen.get(i)
        if not rows:
            verdict = "-"
            name = "%s %s" % (steps[i][5], steps[i][2])
        else:
            name = rows[-1][0]
            statuses = [r[1] for r in rows]
            if "OK" in statuses:
                verdict = "OK"
            elif "FAIL" in statuses:
                verdict = "FAIL"
            elif "START" in statuses:
                verdict = "HANG?"   # started, never finished -> wedged here
            else:
                verdict = "?"
        print("%3d   %-7s  %s" % (i, verdict, name))
        if rows:
            for r in rows:
                if r[1] in ("FAIL", "note", "OK"):
                    print("               %s: %s" % (r[1], r[2] if len(r) > 2 else ""))
    print()
    print("OK = no exception AND drew -- still eyeball the screen for legibility")
    print("HANG? = board wedged on this step; RESET and re-run to continue")


def reset_results():
    try:
        os.remove(RESULTS)
        print("removed", RESULTS)
    except OSError as e:
        print("nothing to remove:", e)


if __name__ == "__main__":
    # `mpremote run` with no way to pass an index: advance a saved counter so
    # repeated runs walk the matrix.  Each run does ONE step then hard-resets
    # the board itself, so just keep running this file until summary() prints.
    # font_probe.sh calls main(n) explicitly and is the easier path.
    STEP_FILE = "/font_probe.step"
    try:
        with open(STEP_FILE) as f:
            _n = int(f.read().strip() or "0")
    except (OSError, ValueError):
        _n = 0
    if _n == 0:
        reset_results()
    if _n >= count():
        print("all %d steps done" % count())
        summary()
    else:
        with open(STEP_FILE, "w") as f:
            f.write(str(_n + 1))     # advance BEFORE running the risky step
        print("running step %d/%d; board will hard-reset when done -- "
              "`mpremote run` this again for the next one" % (_n, count() - 1))
        main(_n)
