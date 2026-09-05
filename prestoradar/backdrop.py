import gc

WIDTH, HEIGHT = 480, 480               # fixed: this hardware's full_res display size
RASTER_PATH = "/prestoradar/basemap.jpg"

# Cohen-Sutherland: clip a segment to [0, WIDTH) x [0, HEIGHT) before it reaches
# display.line(). The coastline rings run out to a 50 km clip box (~+/-620 px),
# and feeding coordinates that far off-screen into the graphics library is the
# suspected cause of a freeze seen during development.
_L, _R, _B, _T = 1, 2, 4, 8


def _outcode(x, y):
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


def _clip_segment(x0, y0, x1, y1):
    c0, c1 = _outcode(x0, y0), _outcode(x1, y1)
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
            x0, y0, c0 = x, y, _outcode(x, y)
        else:
            x1, y1, c1 = x, y, _outcode(x, y)


class Backdrop:
    """Owns what's drawn behind the aircraft each frame: either the vector
    scope grid + coastline/airports, or (in "map" mode, when the boot layer
    count allows it) a pre-rendered raster decoded onto PicoGraphics layer 0.

    Two things this deliberately does NOT own, both injected instead:
    - `view_cx`, the current horizontal registration (shifted while the
      detail panel is open), stays UI-owned state in radar.py until ui.py
      exists (REFACTORING.md #1/#2) -- every method that needs it takes it
      as an argument rather than storing it.
    - `draw_grid` (draw the scope rings/crosshairs) is a rendering concern,
      not basemap data, so it's a callback radar.py supplies rather than
      something this class draws itself -- it stays in radar.py until
      render.py exists.
    """

    def __init__(self, display, settings, raster_ok, draw_basemap_flag,
                 basemap_data, to_screen, draw_grid, bg_pen, coast_pen, airport_pen):
        self.display = display
        self.settings = settings
        self.raster_ok = raster_ok
        self.draw_basemap_flag = draw_basemap_flag  # DRAW_BASEMAP setting; fixed at boot
        self.basemap_data = basemap_data
        self.to_screen = to_screen
        self.draw_grid = draw_grid
        self.bg_pen = bg_pen
        self.coast_pen = coast_pen
        self.airport_pen = airport_pen

        self.map_layers = False     # True once boot committed to the 2-layer composite
        #                              (fixed at boot -- whether layer 0 exists at all,
        #                              not what's on it)
        self.showing_raster = False  # True only while layer 0 currently holds the
        #                              decoded raster. Tracks what redraw() actually put
        #                              there (can lag DISPLAY_MODE if the raster is
        #                              missing or fails to decode) -- callers read this,
        #                              not map_layers or DISPLAY_MODE, to pick
        #                              contrast-appropriate pens for whatever is
        #                              actually behind them.
        self.segs = None    # [(x0, y0, x1, y1), ...] ints, clipped to the viewport
        self.marks = ()     # [(x, y, name), ...] airports inside the viewport

    # --- Vector basemap (coastline/airports cache) --------------------------
    # The basemap never changes shape at runtime -- fixed centre, fixed
    # projection -- so project and viewport-clip every coastline/lake segment
    # ONCE, into screen-space integer endpoints. draw_vector() then just
    # replays a list of display.line() calls: no float maths, no
    # Cohen-Sutherland per segment, and off-screen geometry has already been
    # discarded. Doing this every frame (the NYC coastline alone is ~1800
    # vertices) was the bulk of the per-frame draw cost at ANIM_INTERVAL.

    def _cache_rings(self, rings, out):
        for r in rings:
            px, py = self.to_screen(*r[0])
            for point in r[1:]:
                cx, cy = self.to_screen(*point)
                seg = _clip_segment(px, py, cx, cy)
                if seg is not None:
                    out.append((int(seg[0]), int(seg[1]), int(seg[2]), int(seg[3])))
                px, py = cx, cy

    def build_vector_cache(self):
        try:
            if self.basemap_data is None:
                self.segs = []
                return
            segs = []
            self._cache_rings(self.basemap_data.COASTLINE, segs)
            self._cache_rings(getattr(self.basemap_data, "LAKES", ()), segs)
            marks = []
            for name, e, n in getattr(self.basemap_data, "AIRPORTS", ()):
                x, y = self.to_screen(e, n)
                if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                    marks.append((x, y, name))
            self.segs, self.marks = segs, marks
        except Exception as e:  # noqa: BLE001 -- the basemap is optional, don't die for it
            print("build_vector_cache failed:", repr(e))
            self.segs = []
        gc.collect()

    def draw_vector(self):
        if not self.draw_basemap_flag or not self.segs:
            return
        self.display.set_pen(self.coast_pen)
        for s in self.segs:
            self.display.line(s[0], s[1], s[2], s[3])
        if self.marks:
            self.display.set_pen(self.airport_pen)
            for x, y, name in self.marks:
                self.display.circle(x, y, 3)
                self.display.text(name, x + 5, y - 4, WIDTH, 1)

    # --- Raster basemap (PLAN item 8) ---------------------------------------
    # In map mode a pre-rendered 480x480 backdrop stands in for the vector
    # grid. make_basemap.py --raster bakes prestoradar/basemap.jpg in the
    # same kilometres-east/north frame to_screen() projects into,
    # radar_deploy.sh copies it, and here jpegdec decodes it onto layer 0.
    # draw_scene() then just clears layer 1 and draws the aircraft;
    # presto.update() composites the two. If basemap.jpg is missing, the
    # vector grid is drawn on layer 0 as the fallback.

    def redraw(self, view_cx):
        """(Re)draw layer 0 to match DISPLAY_MODE at the current view shift:
        the raster if "map" (falling back to the vector grid + coastline if
        basemap.jpg is missing or fails to decode), the vector grid +
        coastline if "radar" -- the same two components draw_scene()'s
        non-2-layer path draws every frame, so toggling between modes
        restores the *whole* look, not just the grid. Called once at boot
        (via load()), again from radar.py's _set_selected() whenever
        view_cx changes (the detail panel opening/closing, or the shift
        adjusting to keep the selected plane clear of it), and again from
        _toggle_setting() when DISPLAY_MODE itself changes, so the backdrop
        actually follows the on-device toggle instead of only the aircraft
        icons and pens. Only meaningful once the boot layer count is 2
        (map_layers) -- that's fixed by DISPLAY_MODE *at boot*, so toggling
        into "map" from a "radar" boot still can't get the raster (no layer
        0 to draw it onto); toggling between them after a "map" boot works
        both ways, using this same layer-0 redraw either direction. The
        raster path costs one ~380 ms jpegdec decode -- same as the
        panel-shift redraw, only on a mode/selection change, not per frame.
        """
        if not self.map_layers:
            self.showing_raster = False
            return
        offset_x = view_cx - WIDTH // 2
        self.display.set_layer(0)
        self.display.set_pen(self.bg_pen)
        self.display.clear()
        self.showing_raster = False
        if self.settings.DISPLAY_MODE == "map":
            try:
                import jpegdec
                j = jpegdec.JPEG(self.display)
                j.open_file(RASTER_PATH)
                j.decode(offset_x, 0, jpegdec.JPEG_SCALE_FULL)
                j = None
                gc.collect()
                self.showing_raster = True
                print("raster basemap: layer 0 <-", RASTER_PATH, " offset_x", offset_x,
                      " mem", gc.mem_free())
            except OSError:
                print("raster basemap:", RASTER_PATH, "missing -- vector grid on layer 0")
                self.draw_grid()
                self.draw_vector()
            except Exception as e:  # noqa: BLE001 -- optional, never fatal
                print("raster basemap: decode failed:", repr(e), "-- vector grid on layer 0")
                self.draw_grid()
                self.draw_vector()
        else:
            # DISPLAY_MODE == "radar": the same grid + coastline scope mode always
            # draws, just on layer 0 instead of redrawn fresh every frame.
            self.draw_grid()
            self.draw_vector()
        self.display.set_layer(1)

    def load(self, view_cx):
        if not self.raster_ok:
            return
        self.map_layers = True          # committed to the 2-layer composite
        self.redraw(view_cx)
