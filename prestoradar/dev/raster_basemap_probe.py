"""
Raster basemap feasibility probe -- PLAN.md item 8 ("a map mode").

Question this answers: can a pre-rendered raster map be shown as the backdrop
of the "now flying" display at (or near) 480x480 on *this* Presto firmware,
cheaply enough to redraw the aircraft on top at the radar's ~2 fps, and with
enough RAM headroom left for the TLS fetch + JSON body?

    ../venv/bin/mpremote run prestoradar/dev/raster_basemap_probe.py

Pick ONE route per run by editing ROUTE below -- Presto()/the ST7701 driver can
only be brought up once per process, and the three routes need different Presto
constructor flags. Each route runs a 30-frame loop drawing ~24 fake aircraft
over the backdrop and logs, per frame: backdrop cost, plane-draw cost,
presto.update() cost, and gc.mem_free(). At the end it holds the last frame so
you can photograph it (alignment, colour, tearing).

--- the three routes ---

  A  full_res (480x480) + jpegdec, decode the JPEG into the framebuffer every
     frame. No resident copy, no raw-framebuffer access needed -- jpegdec
     writes through PicoGraphics, which works even though Presto(full_res=True)
     leaves .buffer = None. Viable only if the per-frame decode is well under
     the ~500 ms frame budget. This is the cleanest option if it's fast enough.

  B  240x240, non-full_res -> 2 layers. Decode the backdrop onto layer 0 ONCE
     at startup; each frame only clear layer 1 and draw planes on it;
     presto.update() composites. Zero per-frame backdrop cost, but half the
     linear resolution. PLAN item 8 calls this "the right first prototype".

  C  full_res + direct_to_fb=True, so presto.buffer is a writable
     memoryview of the 460 800-byte RGB565 framebuffer. Hold a resident
     bytearray copy of the rendered map and blit it with
     `presto.buffer[:] = bg` every frame. Full resolution, but ~460 KB
     resident on top of the framebuffer -- the RAM question. The probe reports
     mem_free before/after the alloc so you can weigh it against the ~60 KB
     JSON body + TLS buffers radar.py already carries.

  (A PEN_P8 palette variant of C would halve the resident copy to ~230 KB but
   turns every create_pen() in radar.py into a palette index -- noted, not
   probed here.)

--- test asset (routes A and B) ---

Routes A/B need a JPEG at /basemap.jpg or /prestoradar/basemap.jpg. Any
480x480-ish map export works; e.g. from a PNG on the Mac:

    sips -s format jpeg -Z 480 -s formatOptions 85 somemap.png --out /tmp/basemap.jpg
    ../venv/bin/mpremote cp /tmp/basemap.jpg :basemap.jpg

If no JPEG is found, A is skipped and B falls back to a synthesized gradient on
layer 0 so the compositing path is still measured. Route C always synthesizes
its backdrop (a gradient with a centre cross), so it needs no asset.
"""

import gc
import math
import sys
import time

if "/prestoradar" not in sys.path:
    sys.path.insert(0, "/prestoradar")

# ---- pick one: "A", "B", or "C" ----
ROUTE = "A"

FRAMES = 30
N_FAKE_PLANES = 24
JPEG_PATHS = ("/basemap.jpg", "/prestoradar/basemap.jpg", "basemap.jpg")


def log(*parts):
    print("[{:8.3f}] {}".format(time.ticks_ms() / 1000,
                                " ".join(str(p) for p in parts)))


def mem():
    gc.collect()
    return gc.mem_free()


def find_jpeg():
    import os
    for p in JPEG_PATHS:
        try:
            os.stat(p)
            return p
        except OSError:
            pass
    return None


def rgb565_be(r, g, b):
    """8-bit RGB -> RGB565 as (hi, lo) bytes. The Presto framebuffer wants the
    high byte first -- ../presto/tools/convert-image-rgb565.py byteswaps numpy's
    little-endian packing before writing, so match that here."""
    v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return v >> 8, v & 0xFF


def synth_rgb565(w, h):
    """A gradient backdrop with a bright centre cross, as a w*h*2 bytearray in
    the framebuffer's byte order. Row-at-a-time so it builds in a second or two
    even in MicroPython."""
    t0 = time.ticks_ms()
    buf = bytearray(w * h * 2)
    cx, cy = w // 2, h // 2
    for y in range(h):
        # vertical gradient blue->green, plus a horizontal band every 60 px
        g = 40 + (y * 160) // h
        b = 160 - (y * 120) // h
        band = (y % 60) < 2
        for x in range(w):
            if band or abs(x - cx) < 2 or abs(y - cy) < 2:
                hi, lo = 0xFF, 0xFF          # white grid / cross
            else:
                r = 20 + (x * 40) // w
                hi, lo = rgb565_be(r, g, b)
            o = (y * w + x) * 2
            buf[o] = hi
            buf[o + 1] = lo
    log("synth_rgb565 %dx%d -> %d bytes in %d ms"
        % (w, h, len(buf), time.ticks_diff(time.ticks_ms(), t0)))
    return buf


def fake_planes(w, h, n):
    """Deterministic scatter of (x, y, heading) to redraw every frame."""
    out = []
    for i in range(n):
        a = i * 2.399963  # golden angle
        rad = (i / n) ** 0.5 * (min(w, h) * 0.45)
        x = int(w / 2 + rad * math.cos(a))
        y = int(h / 2 + rad * math.sin(a))
        out.append((x, y, a))
    return out


def draw_planes(display, planes, pen):
    display.set_pen(pen)
    for x, y, a in planes:
        display.circle(x, y, 3)
        display.line(x, y, int(x + 12 * math.cos(a)), int(y + 12 * math.sin(a)))


def report(label, frame_ms):
    frame_ms.sort()
    lo, hi = frame_ms[0], frame_ms[-1]
    avg = sum(frame_ms) / len(frame_ms)
    log("%s: frame total  min %d  avg %d  max %d ms   (~%.1f fps at avg)"
        % (label, lo, avg, hi, 1000.0 / avg if avg else 0))


# ------------------------------------------------------------------ route A
def route_a():
    from presto import Presto
    try:
        import jpegdec
    except ImportError as e:
        log("route A needs jpegdec, not available:", repr(e))
        return

    jpg = find_jpeg()
    if not jpg:
        log("route A skipped: no JPEG at", JPEG_PATHS)
        log("  make one:  sips -s format jpeg -Z 480 map.png --out /tmp/basemap.jpg")
        log("             mpremote cp /tmp/basemap.jpg :basemap.jpg")
        return

    log("mem before Presto:", mem())
    presto = Presto(full_res=True, ambient_light=True)
    display = presto.display
    w, h = display.get_bounds()
    log("Presto full_res", w, "x", h, " buffer =",
        "exposed" if presto.buffer is not None else "None (jpegdec draws via PicoGraphics)")
    log("mem after Presto:", mem())

    j = jpegdec.JPEG(display)
    j.open_file(jpg)
    log("JPEG opened:", jpg, j.get_width(), "x", j.get_height())

    plane_pen = display.create_pen(255, 80, 80)
    planes = fake_planes(w, h, N_FAKE_PLANES)
    scale = getattr(jpegdec, "JPEG_SCALE_FULL", 0)

    frame_ms = []
    for f in range(FRAMES):
        t0 = time.ticks_ms()
        j.decode(0, 0, scale)
        t_dec = time.ticks_diff(time.ticks_ms(), t0)
        t1 = time.ticks_ms()
        draw_planes(display, planes, plane_pen)
        t_pl = time.ticks_diff(time.ticks_ms(), t1)
        t2 = time.ticks_ms()
        presto.update()
        t_up = time.ticks_diff(time.ticks_ms(), t2)
        total = time.ticks_diff(time.ticks_ms(), t0)
        frame_ms.append(total)
        if f < 3 or f % 10 == 0:
            log("A frame %2d  decode %d  planes %d  update %d  total %d ms  mem %d"
                % (f, t_dec, t_pl, t_up, total, mem()))

    report("route A (full_res JPEG per frame)", frame_ms)
    log("verdict: viable per-frame if decode << 500 ms; else use route B "
        "(decode once to layer 0).")
    hold()


# ------------------------------------------------------------------ route B
def route_b():
    from presto import Presto
    jpg = find_jpeg()

    log("mem before Presto:", mem())
    presto = Presto(ambient_light=True)          # 240x240, 2 layers
    display = presto.display
    w, h = display.get_bounds()
    log("Presto non-full_res", w, "x", h, " (2 layers)")
    log("mem after Presto:", mem())

    # --- layer 0: the backdrop, drawn ONCE ---
    display.set_layer(0)
    t0 = time.ticks_ms()
    if jpg:
        try:
            import jpegdec
            j = jpegdec.JPEG(display)
            j.open_file(jpg)
            scale = getattr(jpegdec, "JPEG_SCALE_FULL", 0)
            # scale down if the asset is bigger than the 240 panel
            if j.get_width() > w * 1.5:
                scale = getattr(jpegdec, "JPEG_SCALE_HALF", scale)
            j.decode(0, 0, scale)
            log("layer 0: JPEG %s decoded in %d ms"
                % (jpg, time.ticks_diff(time.ticks_ms(), t0)))
        except Exception as e:  # noqa: BLE001
            log("layer 0: JPEG decode failed, using gradient:", repr(e))
            jpg = None
    if not jpg:
        for y in range(h):
            display.set_pen(display.create_pen(20 + y // 4, 40 + y // 3, 150 - y // 3))
            display.line(0, y, w, y)
        log("layer 0: synthesized gradient in %d ms"
            % time.ticks_diff(time.ticks_ms(), t0))

    plane_pen = display.create_pen(255, 80, 80)
    clear_pen = display.create_pen(0, 0, 0)      # index 0 on layer 1 == transparent
    planes = fake_planes(w, h, N_FAKE_PLANES)

    frame_ms = []
    for f in range(FRAMES):
        t0 = time.ticks_ms()
        display.set_layer(1)
        display.set_pen(clear_pen)
        display.clear()
        draw_planes(display, planes, plane_pen)
        t_draw = time.ticks_diff(time.ticks_ms(), t0)
        t2 = time.ticks_ms()
        presto.update()
        t_up = time.ticks_diff(time.ticks_ms(), t2)
        total = time.ticks_diff(time.ticks_ms(), t0)
        frame_ms.append(total)
        if f < 3 or f % 10 == 0:
            log("B frame %2d  clearL1+planes %d  update %d  total %d ms  mem %d"
                % (f, t_draw, t_up, total, mem()))

    report("route B (240x240, backdrop on layer 0)", frame_ms)
    log("verdict: per-frame cost is just planes + composite; the tradeoff is "
        "240x240 resolution.")
    hold()


# ------------------------------------------------------------------ route C
def route_c():
    from presto import Presto

    log("mem before Presto:", mem())
    presto = Presto(full_res=True, ambient_light=True, direct_to_fb=True)
    display = presto.display
    w, h = display.get_bounds()
    fb = presto.buffer
    log("Presto full_res", w, "x", h, " direct_to_fb -> buffer:",
        "writable, %d bytes" % len(fb) if fb is not None else "None (FAILED)")
    if fb is None:
        log("route C not possible: direct_to_fb did not expose the framebuffer.")
        return
    m_after_init = mem()
    log("mem after Presto:", m_after_init)

    # --- resident backdrop copy ---
    try:
        bg = synth_rgb565(w, h)
    except MemoryError:
        log("route C FAILED: MemoryError allocating the %d-byte resident copy."
            % (w * h * 2))
        log("  mem_free was %d -- not enough headroom for a full_res RGB565 copy."
            % m_after_init)
        log("  next: try PEN_P8 (palette=True, ~%d bytes) or route B." % (w * h))
        return
    m_after_bg = mem()
    log("mem after resident copy: %d  (copy cost %d bytes, %d left)"
        % (m_after_bg, len(bg), m_after_bg))
    if len(fb) != len(bg):
        log("WARNING: framebuffer %d != backdrop %d bytes; slice blit will fail"
            % (len(fb), len(bg)))

    plane_pen = display.create_pen(255, 80, 80)
    planes = fake_planes(w, h, N_FAKE_PLANES)

    frame_ms = []
    for f in range(FRAMES):
        t0 = time.ticks_ms()
        fb[:] = bg                              # the blit
        t_blit = time.ticks_diff(time.ticks_ms(), t0)
        t1 = time.ticks_ms()
        draw_planes(display, planes, plane_pen)
        t_pl = time.ticks_diff(time.ticks_ms(), t1)
        t2 = time.ticks_ms()
        presto.update()
        t_up = time.ticks_diff(time.ticks_ms(), t2)
        total = time.ticks_diff(time.ticks_ms(), t0)
        frame_ms.append(total)
        if f < 3 or f % 10 == 0:
            log("C frame %2d  blit %d  planes %d  update %d  total %d ms  mem %d"
                % (f, t_blit, t_pl, t_up, total, mem()))

    report("route C (full_res, resident RGB565 blit)", frame_ms)
    log("headroom check: radar.py also needs ~60 KB JSON body + TLS buffers "
        "during a fetch. mem_free here is %d." % mem())
    hold()


def hold():
    log("done -- holding last frame. Ctrl-C to exit.")
    while True:
        time.sleep(1)


def main():
    log("raster basemap probe: ROUTE =", ROUTE)
    log("initial mem_free:", mem())
    if ROUTE == "A":
        route_a()
    elif ROUTE == "B":
        route_b()
    elif ROUTE == "C":
        route_c()
    else:
        log("set ROUTE to 'A', 'B', or 'C' at the top of the file")


main()
