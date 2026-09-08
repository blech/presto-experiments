import math
import time

import geometry
import routes
import traces
from backdrop import _clip_segment   # Cohen-Sutherland viewport clip, shared with the
#                                      coastline draw -- a fast aircraft's ~5 min trace
#                                      runs well off a 30 km scope and the graphics lib
#                                      mustn't get coordinates hundreds of px out (see
#                                      backdrop.py's note on the same hazard)

WIDTH, HEIGHT = 480, 480               # fixed: this hardware's full_res display size

# Top-down airliner for "map" mode. Local coords: +x = right wing, +y = nose;
# ~14 px nose-to-tail. Every wing/tailplane root overlaps the fuselage quad, so
# the shape stays connected at any rotation. Convex triangles -- display.polygon()
# only fills convex reliably, display.triangle() is always exact.
_ICON_TRIS = (
    (-1.5, -6.0), (1.5, -6.0), (1.5, 5.5),     # fuselage
    (-1.5, -6.0), (1.5, 5.5), (-1.5, 5.5),
    (-1.5, 5.5), (1.5, 5.5), (0.0, 7.5),       # nose
    (1.4, 2.6), (1.4, -3.0), (8.8, -1.8),      # right wing (~30 deg sweep)
    (-1.4, 2.6), (-1.4, -3.0), (-8.8, -1.8),   # left wing
    (1.5, -3.5), (1.5, -6.0), (3.8, -7.0),     # right tailplane
    (-1.5, -3.5), (-1.5, -6.0), (-3.8, -7.0),  # left tailplane
)

# Emitter-category (ADS-B "category") -> fixed-wing icon scale. A1 light .. A5
# heavy; anything not listed (incl. not broadcast) draws at 1.0.
_CAT_SCALE = {"A1": 0.72, "A2": 0.88, "A3": 1.0, "A4": 1.2, "A5": 1.35}


def _trace_pen_index(seg_idx, n_segs, n_pens):
    """Pen index in [0, n_pens) for trail segment seg_idx (0 = oldest) of
    n_segs. Oldest segment -> 0 (dimmest), newest -> n_pens - 1, so the line
    fades in from where the aircraft has been to where it is now."""
    if n_segs <= 1:
        return n_pens - 1
    return min(n_pens - 1, seg_idx * n_pens // n_segs)


_TAG_SCALE = 2      # bitmap6 scale for callsign tags on the scope
_TAG_CH_W = 8       # eyeballed per-character advance for bitmap6 at _TAG_SCALE (2);
#                     used by BOTH _label_box (the ambient cull) and _tag (the
#                     backing box), so a wrong value under-culls and under-covers
#                     together -- keep the two in sync via this one constant.
_TAG_H = 16         # ~px tall


def _data_block(p):
    """Three short lines for the stage-2 on-scope ATC tag: callsign, then
    flight level + ground speed, then ICAO type. Altitude / 100 for FL
    ("GND" on the ground, "---" if unknown); speed to the nearest knot; "?"
    for an unknown type."""
    cs = p.callsign or p.hex or "?"
    a = p.alt
    if a in (0, "ground"):
        lvl = "GND"
    elif isinstance(a, (int, float)):
        lvl = "FL%03d" % (int(a) // 100)
    else:
        lvl = "---"
    return (cs, "%s %dkt" % (lvl, round(p.gs or 0)), p.type or "?")


_CARD_W, _CARD_H = 212, 200


def _card_corner(bx, by):
    """Top-left (x, y) of the detail card: the corner diagonally opposite
    the selected blip (bx, by), inset 4 px, so the card covers neither the
    blip nor (usually) its trail. Ties (blip on a mid-line) fall to the
    right / bottom."""
    x = 4 if bx > 240 else WIDTH - _CARD_W - 4
    y = 4 if by > 240 else HEIGHT - _CARD_H - 4
    return x, y


class Renderer:
    """Owns every pen and every draw_* routine -- everything that decides
    what the screen actually looks like each frame. Two kinds of state this
    deliberately does NOT own, both taken as arguments instead of stored,
    following the same precedent backdrop.py's extraction set (leave state
    with no stable owner yet where it is, rather than relocate the
    coupling):

    - `selected` / `settings_open` / `view_cx` are all UI-driven state that
      still lives in radar.py until ui.py exists -- every method that needs
      one takes it as an argument.
    - `to_screen` and `hidden` (the HIDE_ON_GROUND draw-time filter) are
      injected callbacks for the same reason: `to_screen` needs `view_cx`,
      `hidden` is also used by radar.py's own selection-repointing, so
      neither has a single natural owner yet.

    `self.backdrop` is set by radar.py right after constructing Backdrop,
    not passed to `__init__` -- Backdrop's constructor needs this Renderer's
    pens and its `draw_radar_grid` method, so Renderer has to exist first;
    Renderer only needs Backdrop once rendering actually starts (for
    `theme()`), by which point both objects exist. Two-phase init, same
    pattern as `_feed.on_update` being assigned after construction.
    """

    def __init__(self, display, presto, settings, feed, to_screen, hidden,
                 radius_km, px_per_km, settings_btn, spanel,
                 sp_row0, sp_rowh, sp_valdx):
        self.display = display
        # bitmap6 (PicoGraphics' default, made explicit) everywhere on the
        # scope -- status line, alt legend, map callsign tags, airport labels.
        # draw_scene() reasserts it each frame; _ptext() switches to the
        # chunkier bitmap8 just for the inspect / settings panels.
        display.set_font("bitmap6")
        self.presto = presto
        self.settings = settings
        self.feed = feed
        self.to_screen = to_screen
        self.hidden = hidden
        self.radius_km = radius_km
        self.px_per_km = px_per_km
        self.settings_btn = settings_btn
        self.spanel = spanel
        self.sp_row0 = sp_row0
        self.sp_rowh = sp_rowh
        self.sp_valdx = sp_valdx

        self.backdrop = None    # set by radar.py once Backdrop is constructed
        self.last_drawn = []    # [(x, y, plane), ...] from the last draw_planes()
        self.basemap_ms = 0

        # Pen Colors (RGB). RADAR_* / MAP_* name the same three roles for each
        # of the two backdrops this can draw over -- the dark scope grid
        # ("radar") and the raster basemap ("map", REFACTORING.md #3) -- so a
        # role and its counterpart read as a pair.
        self.BG_COLOR = display.create_pen(10, 20, 10)
        self.TRANSPARENT_PEN = display.create_pen(0, 0, 0)  # 0x0000 -- see-through on layer 1
        self.RADAR_ICON_COLOR = display.create_pen(0, 230, 70)  # mono-mode aircraft AND the
        #                                                          grid/crosshair pen -- same
        #                                                          value on purpose, both read
        #                                                          as "scope green"
        self.RADAR_TEXT_PEN = display.create_pen(200, 255, 200)
        self.COAST_PEN = display.create_pen(60, 90, 120)     # muted blue-grey coastline
        self.AIRPORT_PEN = display.create_pen(150, 130, 170)  # muted violet airport marks
        # RADAR_TEXT_PEN's pale green is tuned for the dark scope background and
        # washes out over the map-mode raster; use this near-black instead there.
        # NOT pure black -- that's TRANSPARENT_PEN's value (0x0000) on layer 1,
        # which layer 0 (the map) would show through instead of drawing over.
        self.MAP_TEXT_PEN = display.create_pen(20, 20, 20)
        # No separate raster mono-aircraft colour exists yet -- mono mode over
        # the raster just reuses MAP_TEXT_PEN. Named on its own anyway so a
        # future "give mono-on-raster its own colour" is a one-line change
        # here, not another pass through plane_pen().
        self.MAP_ICON_COLOR = self.MAP_TEXT_PEN

        # Vertical-state colours: level / cruising, climbing (departing),
        # descending (approaching). Keyed by the "vstate" string feed.py's
        # Feed sets per plane.
        self.RADAR_VSTATE_PENS = {
            "level": display.create_pen(235, 235, 235),   # white
            "climb": display.create_pen(60, 200, 255),    # cyan
            "descent": display.create_pen(255, 160, 40),  # amber
        }
        # In map mode, "level"'s near-white washes out over light map colours
        # the same way RADAR_TEXT_PEN did -- swap it for MAP_TEXT_PEN there
        # (see THEMES below). climb/descent stay put: cyan and amber read fine
        # on the basemap styles tried so far. Built as a copy + one item
        # assignment, not dict(self.RADAR_VSTATE_PENS, level=...) -- a
        # RENDER ERROR: KeyError('climb') seen on-device is consistent with
        # this firmware's dict() not handling a positional mapping plus
        # keyword args the way CPython's does, silently dropping the
        # positional dict's other entries (unconfirmed, but this form sidesteps
        # the question rather than depending on it).
        self.MAP_VSTATE_PENS = dict(self.RADAR_VSTATE_PENS)
        self.MAP_VSTATE_PENS["level"] = self.MAP_TEXT_PEN

        # What's actually behind the drawing decides which pens to use --
        # keyed by self.backdrop.showing_raster (whether the raster backdrop
        # is actually showing), NOT by DISPLAY_MODE: the vector-grid fallback
        # when a raster fails to decode is still the dark "radar" look even
        # while DISPLAY_MODE == "map". See theme().
        self.THEMES = {
            "radar": {"text": self.RADAR_TEXT_PEN, "icon": self.RADAR_ICON_COLOR,
                      "vstate": self.RADAR_VSTATE_PENS},
            "map":   {"text": self.MAP_TEXT_PEN, "icon": self.MAP_ICON_COLOR,
                      "vstate": self.MAP_VSTATE_PENS},
        }

        self.PANEL_BG = display.create_pen(16, 26, 16)
        self.PANEL_BORDER = display.create_pen(0, 150, 50)
        self.PANEL_LABEL = display.create_pen(192, 192, 192)  # row labels, dimmer than values
        self.SELECT_PEN = display.create_pen(255, 235, 90)    # ring: distinct from vstate pens
        self.EMERG_PEN = display.create_pen(255, 70, 70)
        # Trail ramp: dim -> bright, oldest -> newest, so the line reads
        # directionally. Keyed to SELECT_PEN's yellow (the ring around the
        # same target) -- clear of COAST_PEN's blue-grey and the vstate pens.
        self.TRACE_PENS = (
            display.create_pen(70, 65, 25),
            display.create_pen(120, 110, 45),
            display.create_pen(170, 155, 65),
            display.create_pen(215, 200, 90),
        )

    def theme(self):
        return self.THEMES["map"] if self.backdrop.showing_raster else self.THEMES["radar"]

    # --- Small drawing primitives --------------------------------------

    # Panel text stays on the bitmap font. Tried on this firmware and rejected:
    #   - PicoVector + Roboto-Medium.af (e49dede): NotImplementedError: opcode
    #   - PicoGraphics "sans" vector font (9edd5c4): renders as a scribble of strokes
    def _ptext(self, s, x, y_top, size, pen, clip=False, avail=None):
        # One line of panel text, top-left at (x, y_top); size is a pixel
        # height mapped to the nearest bitmap8 integer scale.
        #
        # clip=True trims s with measure_text() until it fits the available
        # width. display.text()'s width arg is a word-WRAP point, not a clip --
        # an overrunning line (a long operator or type name) would otherwise
        # flow onto a second line and draw over the next panel row.
        #
        # `avail` is the screen x just past the last usable pixel (the caller's
        # right edge); default None keeps the historic full-height-sidebar
        # behaviour of clipping to the screen edge. The corner card passes a
        # card-relative value so its rows clip inside the card border, not 250+
        # px past it (a left-corner card starts at x = 4).
        s = str(s)
        scale = max(1, size // 8)
        w = (avail if avail is not None else WIDTH - x) - 2
        self.display.set_font("bitmap8")   # panels only; draw_scene() resets to bitmap6
        if clip:
            while s and self.display.measure_text(s, scale) > w:
                s = s[:-1]
        self.display.set_pen(pen)
        self.display.text(s, x, y_top, w, scale)

    def draw_track_arrow(self, x, y, heading_deg, speed_kt, pen):
        # heading_deg is degrees clockwise from north (the aircraft's track over
        # the ground). Screen y grows downwards, so north maps to -y.
        d = self.display
        a = math.radians(heading_deg)
        dx, dy = math.sin(a), -math.cos(a)
        length = min(60, max(12, speed_kt * 0.15))  # ~knots -> pixels, clamped
        tip_x, tip_y = int(x + dx * length), int(y + dy * length)
        d.set_pen(pen)
        d.line(int(x), int(y), tip_x, tip_y)
        # Arrowhead: two short barbs splayed back from the tip.
        for barb_deg in (heading_deg + 148, heading_deg - 148):
            b = math.radians(barb_deg)
            d.line(tip_x, tip_y,
                   int(tip_x + math.sin(b) * 7), int(tip_y - math.cos(b) * 7))

    def _icon_pass(self, x, y, ca, sa, scale):
        # Fill the icon's triangles at the current pen, rotated by (ca, sa) =
        # (cos, sin) of the heading and multiplied by `scale`. Caller sets the pen.
        t = _ICON_TRIS
        d = self.display
        for i in range(0, len(t), 3):
            (ax, ay), (bx, by), (cx, cy) = t[i], t[i + 1], t[i + 2]
            d.triangle(
                int(x + (ax * ca + ay * sa) * scale), int(y + (ax * sa - ay * ca) * scale),
                int(x + (bx * ca + by * sa) * scale), int(y + (bx * sa - by * ca) * scale),
                int(x + (cx * ca + cy * sa) * scale), int(y + (cx * sa - cy * ca) * scale),
            )

    def _draw_rotor(self, x, y, heading_deg, scale):
        # Top-down helicopter for category A7: hub, a tail boom pointing aft, and
        # a two-blade rotor set 45 degrees off the heading so it doesn't read as
        # a fixed wing. Caller has set the pen.
        d = self.display
        d.circle(x, y, max(2, int(2 * scale)))
        ba = math.radians(heading_deg + 180)
        boom = int(8 * scale)
        d.line(x, y, int(x + math.sin(ba) * boom), int(y - math.cos(ba) * boom))
        blade = int(7 * scale)
        for off in (45, 135):
            a = math.radians(heading_deg + off)
            bx, by = math.sin(a) * blade, -math.cos(a) * blade
            d.line(int(x - bx), int(y - by), int(x + bx), int(y + by))

    def _ring(self, cx, cy, r, thickness=3):
        # PicoGraphics circles are filled, so draw an outline as an outer disc
        # with a background-coloured disc punched out of the middle.
        d = self.display
        d.set_pen(self.RADAR_ICON_COLOR)
        d.circle(cx, cy, r)
        d.set_pen(self.BG_COLOR)
        d.circle(cx, cy, r - thickness)

    def _label_box(self, x, y, text):
        # The rect a callsign tag for `text` occupies, anchored at the blip
        # the way _tag() draws it: top-left at (x + 8, y - 8).
        return (x + 8, y - 8, len(text) * _TAG_CH_W, _TAG_H)

    def _ambient_label_set(self, order):
        # Indices into `order` ([(x, y, plane), ...]) whose callsign tag
        # should be drawn: greedily placed nearest-the-centre first, skipping
        # any whose box overlaps one already placed, and skipping blanks.
        # O(n^2) over ~30 aircraft. Nearest-first is a stable relevance order
        # (the crosshair centre is what the display is "about"); flip the key
        # to q.alt_sort_key for lowest-altitude-first instead.
        ranked = sorted(
            range(len(order)),
            key=lambda i: (order[i][0] - 240) ** 2 + (order[i][1] - 240) ** 2)
        placed = []
        keep = set()
        for i in ranked:
            x, y, p = order[i]
            if not p.callsign:
                continue
            bx, by, bw, bh = self._label_box(x, y, p.callsign)
            if any(bx < ox + ow and ox < bx + bw and by < oy + oh and oy < by + bh
                   for (ox, oy, ow, oh) in placed):
                continue
            placed.append((bx, by, bw, bh))
            keep.add(i)
        return keep

    def _tag(self, x, y, text):
        # One callsign tag: a 1 px dark box (so it reads over the coastline)
        # then the text, at the standard (x + 8, y - 8) anchor.
        if not text:
            return
        d = self.display
        tx, ty = x + 8, y - 8
        w = len(text) * _TAG_CH_W
        d.set_pen(self.BG_COLOR)
        d.rectangle(tx - 1, ty - 1, w + 2, _TAG_H)
        d.set_pen(self.RADAR_TEXT_PEN)
        d.text(text, tx, ty, WIDTH, _TAG_SCALE)

    def _draw_data_block(self, x, y, p):
        # Stage-2 ATC tag: three lines stacked upward from the blip, each on
        # its own dark backing box. Anchored to the right of the blip like a
        # plain tag.
        d = self.display
        lines = _data_block(p)
        tx = x + 8
        for j, line in enumerate(lines):
            ty = y - 8 - (len(lines) - 1 - j) * _TAG_H
            w = len(line) * _TAG_CH_W
            d.set_pen(self.BG_COLOR)
            d.rectangle(tx - 1, ty - 1, w + 2, _TAG_H)
            d.set_pen(self.RADAR_TEXT_PEN)
            d.text(line, tx, ty, WIDTH, _TAG_SCALE)

    def draw_radar_grid(self, view_cx, selected):
        # selected: unused since the sidebar was removed; kept for backdrop.py's call
        d = self.display
        d.set_pen(self.BG_COLOR)
        d.clear()
        # Concentric rings at RADIUS_KM and half that
        self._ring(view_cx, 240, int(self.radius_km * self.px_per_km))
        self._ring(view_cx, 240, int(self.radius_km * 0.5 * self.px_per_km))
        # Full-width crosshair -- no sidebar to clear any more
        x_right = WIDTH - 10
        d.set_pen(self.RADAR_ICON_COLOR)
        d.line(view_cx, 10, view_cx, 470)
        d.line(10, 240, x_right, 240)

    def show_message(self, text):
        d = self.display
        d.set_pen(self.BG_COLOR)
        d.clear()
        d.set_pen(self.RADAR_TEXT_PEN)
        d.text(f"{text}", 5, 10, WIDTH, 2)
        self.presto.update()

    def draw_legend_alt(self):
        # Over the raster, RADAR_VSTATE_PENS' pale "level" dot and RADAR_TEXT_PEN's
        # pale green both lose contrast against light map colours; the "map" theme
        # swaps both (same pens plane_pen() draws aircraft with, so the legend
        # still matches), plus a dark halo behind each dot. theme() keys off
        # backdrop.showing_raster, not map_layers/DISPLAY_MODE: what's actually
        # behind this is what decides contrast, and the raster can be unavailable
        # even in "map" mode (see Backdrop.redraw()'s fallback).
        d = self.display
        theme = self.theme()
        for i, (state, label) in enumerate((("level", "level"),
                                            ("climb", "climb"),
                                            ("descent", "descent"))):
            row_y = 414 + i * 20
            if self.backdrop.showing_raster:
                d.set_pen(theme["text"])
                d.circle(14, row_y + 6, 4)      # halo so a light dot still reads
            # .get(), not [state]: a lookup failure here used to take down the
            # whole render_loop iteration (a RENDER ERROR caught in radar.py,
            # skipping presto.update() -- the display just freezes on the
            # last good frame, everything else keeps running underneath).
            # Cheap insurance against exactly that, whatever the actual cause
            # turns out to be.
            d.set_pen(theme["vstate"].get(state, self.RADAR_ICON_COLOR))
            d.circle(14, row_y + 6, 3)
            d.set_pen(theme["text"])
            d.text(label, 24, row_y, WIDTH, 2)

    def plane_pen(self, p):
        # Pen for an aircraft mark under the current COLOUR_MODE, themed by
        # theme(). "mono" keeps the scope look (everything RADAR_ICON_COLOR) --
        # except that reads fine on a dark scope but washes out on the raster,
        # so the "map" theme's icon colour instead. "alt" colours by vertical
        # state -- the "map" theme's vstate pens while the raster backdrop is
        # actually showing, so a "level" aircraft isn't drawn in the same
        # washed-out white the legend fix moved away from. Extra schemes go
        # here.
        theme = self.theme()
        if self.settings.COLOUR_MODE == "alt":
            # .get(), not [p.vstate] -- see draw_legend_alt()'s comment on
            # the same lookup; feed.py only ever sets one of the three known
            # strings, but a bad lookup here shouldn't be able to freeze the
            # whole display either way.
            return theme["vstate"].get(p.vstate, theme["icon"])
        return theme["icon"]

    def _draw_planes_radar(self, order, selected, detail_level, trace_active):
        # Scope style: blip, track arrow, callsign tag. Ambient tags are
        # culled to the nearest-centre non-overlapping set; while something
        # is selected, non-selected aircraft carry no tag at all and the
        # selected one shows a plain callsign (stage 1) or the ATC data
        # block (stage 2+). Its track arrow is suppressed once a trace shows.
        d = self.display
        labelled = set() if selected is not None else self._ambient_label_set(order)
        for i, (x, y, p) in enumerate(order):
            pen = self.plane_pen(p)
            d.set_pen(pen)
            d.circle(x, y, 3)
            is_sel = p is selected
            if p.heading is not None and p.gs > 20 and not (trace_active and is_sel):
                self.draw_track_arrow(x, y, p.heading, p.gs, pen)
            if is_sel:
                if detail_level >= 2:
                    self._draw_data_block(x, y, p)
                else:
                    self._tag(x, y, p.callsign)
            elif i in labelled:
                self._tag(x, y, p.callsign)

    def _draw_trace(self, trace, selected):
        # Polyline through the selected aircraft's recent fixes, oldest ->
        # newest, then on to its current dead-reckoned position so the line
        # meets the marker. `trace` is traces.points_for()'s pick: the network
        # trace_recent seed when it resolved, else the in-RAM trail feed.py
        # accumulates. Drawn before the markers so a marker sits on top; every
        # segment is viewport-clipped (a jet's 5 min trace reaches well past a
        # 30 km scope) and pen-ramped by age.
        d = self.display
        pts = [self.to_screen(e, n) for (e, n, _alt) in trace]
        if selected is not None:
            pts.append(self.to_screen(selected.e, selected.n))
        n_segs = len(pts) - 1
        if n_segs < 1:
            return
        pens = self.TRACE_PENS
        px, py = pts[0]
        for i in range(1, len(pts)):
            cx, cy = pts[i]
            seg = _clip_segment(px, py, cx, cy)
            if seg is not None:
                d.set_pen(pens[_trace_pen_index(i - 1, n_segs, len(pens))])
                d.line(int(seg[0]), int(seg[1]), int(seg[2]), int(seg[3]))
            px, py = cx, cy

    def _draw_planes_map(self, order, selected, detail_level):
        # Map style: an icon along the track, no label; a plain blip when there's
        # no usable heading. Shape/size come from the ADS-B emitter category --
        # A7 is a helicopter, A1..A5 scale the fixed-wing icon light..heavy.
        # Nearest is drawn last (order is pre-sorted) so a dense in-trail stream
        # reads as an overlapping line rather than a pile of text.
        #
        # The map-mode tap cycle is only 2 stages (UI-TRAILS.md "Map mode
        # notes"): at stage 1 the selected plane gets a plain callsign tag on
        # its icon; stage 2 is the corner card (drawn by draw_scene). No ATC
        # data block in map mode.
        d = self.display
        for x, y, p in order:
            d.set_pen(self.plane_pen(p))
            heading = p.heading
            if heading is None or p.gs <= 20:
                d.circle(x, y, 3)
            else:
                cat = p.cat
                if cat == "A7":
                    self._draw_rotor(x, y, heading, 1.0)
                else:
                    a = math.radians(heading)
                    self._icon_pass(x, y, math.cos(a), math.sin(a),
                                    _CAT_SCALE.get(cat, 1.0))
            if p is selected and detail_level == 1:
                self._tag(x, y, p.callsign)

    def draw_planes(self, planes, selected, detail_level, trace=None):
        # Lowest altitude first, so where two overlap the higher aircraft is
        # drawn on top -- it's the one nearer the viewer looking down.
        order = []
        for p in sorted(planes, key=lambda q: q.alt_sort_key):
            if self.hidden(p):
                continue
            x, y = self.to_screen(p.e, p.n)
            if -40 <= x <= 520 and -40 <= y <= 520:
                order.append((x, y, p))
        trace_active = trace is not None and len(trace) >= 2
        if trace_active:
            self._draw_trace(trace, selected)      # under the markers
        if self.settings.DISPLAY_MODE == "map":
            self._draw_planes_map(order, selected, detail_level)
        else:
            self._draw_planes_radar(order, selected, detail_level, trace_active)
        self.last_drawn = order

        # Ring the selected aircraft, on top of everything. Outer/inner discs
        # make an outline; kept small (r 7) so it doesn't reach the callsign
        # tag at (x+8, y-8). Caller (radar.py) is responsible for having
        # already cleared `selected` if it's hidden -- this only draws the
        # ring for whatever it's given, it never changes the selection.
        d = self.display
        for x, y, p in order:
            if p is selected:
                d.set_pen(self.SELECT_PEN)
                d.circle(x, y, 7)
                d.set_pen(self.BG_COLOR)
                d.circle(x, y, 5)
                d.set_pen(self.plane_pen(p))
                d.circle(x, y, 3)   # put the marker back inside the ring
                break

    def _fmt_alt(self, alt):
        if alt in (0, "ground"):
            return "ground"
        return "%sft" % alt

    def _fmt_route(self, cs):
        rc = routes.get(cs)
        if rc == "" or routes.retrying(cs):
            return "..."
        if isinstance(rc, tuple):
            return "%s-%s" % rc
        if cs and not routes.is_hex_id(cs):
            return "unknown"
        return "-"

    def _blip_xy(self, p):
        # Where p's marker was drawn this frame (last_drawn is set by
        # draw_planes, which runs before the card). Falls back to centre.
        for x, y, q in self.last_drawn:
            if q is p:
                return x, y
        return 240, 240

    def draw_card(self, p, corner_xy):
        # Compact detail card (decision 1): opaque, bordered, in a screen
        # corner rather than a full-height sidebar. Fields trimmed to what's
        # glanceable (decision 2): no SQWK / ICAO / TRACK.
        d = self.display
        x, y = corner_xy
        d.set_pen(self.PANEL_BG)
        d.rectangle(x, y, _CARD_W, _CARD_H)
        d.set_pen(self.PANEL_BORDER)
        d.rectangle(x, y, _CARD_W, 1)
        d.rectangle(x, y + _CARD_H - 1, _CARD_W, 1)
        d.rectangle(x, y, 1, _CARD_H)
        d.rectangle(x + _CARD_W - 1, y, 1, _CARD_H)

        tx = x + 8
        vx = tx + 78
        # Right inner edge of the card, less a small margin so text clears the
        # 1 px border. Passed to every _ptext below so clipping is card-
        # relative, not screen-relative (the card can start at x = 4).
        avail_tx = x + _CARD_W - tx - 6
        avail_vx = x + _CARD_W - vx - 6
        row = y + 8
        self._ptext(p.label, tx, row, 16, self.RADAR_TEXT_PEN, avail=avail_tx)
        row += 20
        op = p.operator
        if op:
            self._ptext(op, tx, row, 14, self.PANEL_LABEL, clip=True, avail=avail_tx)
            row += 17
        em = p.emergency
        if em and em != "none":
            self._ptext("! " + str(em).upper(), tx, row, 14, self.EMERG_PEN,
                        avail=avail_tx)
            row += 17
        td = p.type_description
        vr = p.vrate
        rows = (
            ("REG", p.reg or "-"),
            ("TYPE", (td or p.type or "-")),
            ("RTE", self._fmt_route((p.callsign or "").strip())),
            ("ALT", self._fmt_alt(p.alt)),
            ("VS", ("%+d" % vr) if vr else "level"),
            ("SPD", "%d kt" % (p.gs or 0)),
            ("DIST", ("%dnm %s" % (round(p.dst), geometry.compass(p.dir)))
                     if p.dst is not None else "-"),
        )
        for label, val in rows:
            self._ptext(label, tx, row, 14, self.PANEL_LABEL, avail=avail_tx)
            self._ptext(val, vx, row, 14, self.RADAR_TEXT_PEN, clip=True, avail=avail_vx)
            row += 17

    def _status_text(self, planes):
        if self.feed.fetch_count == 0:
            return "Connecting..."          # nothing fetched yet
        # `planes` (== feed.planes) holds every fetched aircraft, ground-hidden
        # ones included (see hidden(), REFACTORING.md #5) -- count only what's
        # actually shown, same as what draw_planes() puts on-screen.
        visible = sum(1 for p in planes if not self.hidden(p))
        if not self.feed.fetch_ok:           # last fetch failed -- planes may be stale
            return ("Aircraft: %d (stale)" % visible) if planes else "Fetch failed"
        return "Aircraft: %d" % visible  # 0 is legitimate: a quiet sky

    def draw_settings_btn(self):
        d = self.display
        bx, by, bw, bh = self.settings_btn
        d.set_pen(self.PANEL_BG)
        d.rectangle(bx, by, bw, bh)
        d.set_pen(self.PANEL_BORDER)
        for i in range(3):                    # hamburger glyph
            ly = by + 10 + i * 6
            d.line(bx + 8, ly, bx + bw - 8, ly)

    def draw_settings_panel(self):
        d = self.display
        px, py, pw, ph = self.spanel
        d.set_pen(self.PANEL_BG)
        d.rectangle(px, py, pw, ph)
        d.set_pen(self.PANEL_BORDER)
        d.line(px, py, px + pw, py)
        d.line(px, py + ph, px + pw, py + ph)
        d.line(px, py, px, py + ph)
        d.line(px + pw, py, px + pw, py + ph)

        self._ptext("SETTINGS", px + 10, py + 10, 16, self.RADAR_TEXT_PEN)
        d.set_pen(self.PANEL_BORDER)
        d.line(px + 8, py + 36, px + pw - 8, py + 36)

        rows = (("mode", self.settings.DISPLAY_MODE),
                ("colour", self.settings.COLOUR_MODE),
                ("ground", "hide" if self.settings.HIDE_ON_GROUND else "show"))
        y = self.sp_row0
        for label, value in rows:
            self._ptext(label, px + 10, y, 16, self.PANEL_LABEL)
            self._ptext(str(value).upper(), px + 10 + self.sp_valdx, y, 16, self.RADAR_TEXT_PEN)
            y += self.sp_rowh
        self._ptext("tap away to close", px + 10, y + 6, 8, self.PANEL_LABEL)

    def draw_scene(self, planes, selected, settings_open, detail_level):
        d = self.display
        d.set_font("bitmap6")             # _ptext() flips to bitmap8 for the panels
        t = time.ticks_ms()
        if self.backdrop.map_layers:
            d.set_layer(1)             # aircraft layer; layer 0 holds the backdrop
            d.set_pen(self.TRANSPARENT_PEN)
            d.clear()
        else:
            # backdrop.display_view_cx, not a live target: draw_vector() below
            # replays a coastline cache projected for whatever view_cx it was
            # last rebuilt at, and to_screen() (aircraft, below) reads the same
            # value -- so the grid, coastline, and aircraft all move together
            # exactly when UI._rebuild_backdrop() advances it, never one ahead
            # of another (REFACTORING.md #4).
            self.draw_radar_grid(self.backdrop.display_view_cx, selected)
            self.backdrop.draw_vector()
        self.basemap_ms = time.ticks_diff(time.ticks_ms(), t)
        d.set_pen(self.theme()["text"])
        d.text(self._status_text(planes), 5, 10, WIDTH, 2)
        if self.settings.COLOUR_MODE == "alt" and selected is None:
            self.draw_legend_alt()
        # Trace behind the selected aircraft: radar mode only (the user's
        # scope), and only when traces.points_for() has something -- the
        # network trace_recent seed or the in-RAM live trail. Reaching into
        # traces here mirrors how _fmt_route() already reaches into routes.
        trace = None
        if selected is not None and self.settings.DISPLAY_MODE == "radar":
            trace = traces.points_for(selected)
        self.draw_planes(planes, selected, detail_level, trace)
        card_stage = 2 if self.settings.DISPLAY_MODE == "map" else 3
        if selected is not None and detail_level >= card_stage:
            self.draw_card(selected, _card_corner(*self._blip_xy(selected)))
        elif selected is None and not settings_open:
            self.draw_settings_btn()
        if settings_open:
            self.draw_settings_panel()
        self.presto.update()
