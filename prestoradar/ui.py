import routes
from netlog import log

WIDTH, HEIGHT = 480, 480               # fixed: this hardware's full_res display size


class UI:
    """Owns touch handling, the selected-aircraft state, the view shift that
    follows it, and the settings overlay -- the last piece of
    REFACTORING.md #1's file split, and the first module state like
    `selected`/`view_cx`/`settings_open` gets an actual home in rather than
    being passed around as an argument: `Backdrop` and `Renderer` both took
    `view_cx`/`selected` as method arguments because neither of them was the
    right owner; this class is.

    `backdrop` and `renderer` are held by reference (constructed before this
    class, in radar.py) -- normal composition, not a global reach-around.
    `hidden` (the HIDE_ON_GROUND draw-time filter) stays injected rather than
    owned here even though this class uses it too: it's also `Renderer`'s,
    and neither owns the underlying settings check.
    """

    def __init__(self, settings, backdrop, renderer, hidden,
                 px_per_km, panel_x, hit_radius, panel_margin, max_shift, min_view_cx,
                 settings_btn, spanel, sp_row0, sp_rowh):
        self.settings = settings
        self.backdrop = backdrop
        self.renderer = renderer
        self.hidden = hidden
        self.px_per_km = px_per_km
        self.panel_x = panel_x
        self.hit_radius = hit_radius
        self.panel_margin = panel_margin
        self.max_shift = max_shift
        self.min_view_cx = min_view_cx
        self.settings_btn = settings_btn
        self.spanel = spanel
        self.sp_row0 = sp_row0
        self.sp_rowh = sp_rowh

        self.selected = None      # the selected plane dict, or None
        self.view_cx = WIDTH // 2  # x-pixel that km-east 0 maps to (see radar.py's to_screen)
        self.settings_open = False

    # --- Tap to inspect (item 2a) -------------------------------------

    def _target_view_cx(self, p):
        """Where view_cx should sit for the current selection p (or None).
        Shifts left only as far as needed to bring p to panel_margin clear
        of panel_x -- zero shift if it's already clear, so a plane that
        didn't need moving is never pushed off the left edge by an unneeded
        shift -- then clamps to min_view_cx so an extreme-edge plane can't
        ask jpegdec for more shift than is known to work (see max_shift's
        definition in radar.py). Always returns an int: view_cx feeds
        jpegdec.decode()'s offset_x (and, via the vector-grid fallback,
        display.circle()/line()) uncast, and p["e"] * px_per_km is a float.
        """
        if p is None:
            return WIDTH // 2
        x0 = WIDTH // 2 + p["e"] * self.px_per_km    # p's unshifted screen x
        wanted = WIDTH // 2 - max(0, x0 - (self.panel_x - self.panel_margin))
        return int(max(wanted, self.min_view_cx))

    def set_selected(self, p):
        # Select p (or None to dismiss), shift the view just enough to keep
        # p clear of the panel, and kick a route lookup.
        self.selected = p
        cx = self._target_view_cx(p)
        if cx != self.view_cx:
            self.view_cx = cx
            # Both backdrops are pre-rendered through to_screen()/view_cx, so
            # both need a rebuild on a shift -- the vector cache re-projects
            # its segments; the raster re-decodes onto layer 0 at the new
            # offset (Backdrop.redraw).
            if self.backdrop.map_layers:
                self.backdrop.redraw(self.view_cx, self.selected)
            else:
                self.backdrop.build_vector_cache()
        if p is not None:
            routes.request(p["callsign"])

    def dismiss_if_hidden(self):
        """Called once per frame, before drawing (radar.py's _render_loop):
        clear the selection if it just became hidden -- it landed while
        HIDE_ON_GROUND was on, or the setting was flipped on while it was
        already on the ground. Dismissing here, rather than leaving it to
        Renderer, means the renderer never has to decide whether to draw a
        ring for a selection that's about to vanish."""
        if self.selected is not None and self.hidden(self.selected):
            self.set_selected(None)

    def on_feed_update(self, fresh):
        # feed.Feed.on_update: called with the fresh list after every
        # successful fetch. Re-point the selection at the same aircraft in
        # it, or clear it (and un-shift the view) if that aircraft has
        # dropped off -- or is now hidden by HIDE_ON_GROUND (dismiss_if_hidden()
        # would clear it on the next redraw anyway; doing it here skips that
        # one extra tick of a stale selection).
        if self.selected is not None:
            h = self.selected["hex"]
            self.set_selected(next((q for q in fresh
                                     if q["hex"] == h and not self.hidden(q)), None))

    # --- Settings overlay (PLAN 2b phase 1: in-memory toggles, no persistence) --

    def _in_rect(self, px, py, r):
        return r[0] <= px <= r[0] + r[2] and r[1] <= py <= r[1] + r[3]

    def toggle_setting(self, row):
        # Mutate settings in place rather than reassigning a name -- anything
        # holding a reference to it (not just this module) sees the new
        # value immediately (see radar.py's Settings class docstring).
        if row == 0:
            self.settings.DISPLAY_MODE = ("radar" if self.settings.DISPLAY_MODE == "map"
                                           else "map")
            # Aircraft icons already follow DISPLAY_MODE every frame
            # (Renderer.draw_planes()); the backdrop is a static layer-0 draw
            # and needs telling explicitly. Only takes effect if we booted
            # with 2 layers (a "map" boot) -- toggling *into* "map" from a
            # "radar" boot still can't get the raster, since there's no
            # layer 0 to draw it onto (PLAN item 8, "Runtime toggle").
            self.backdrop.redraw(self.view_cx, self.selected)
        elif row == 1:
            self.settings.COLOUR_MODE = "mono" if self.settings.COLOUR_MODE == "alt" else "alt"
        elif row == 2:
            # takes effect next redraw
            self.settings.HIDE_ON_GROUND = 0 if self.settings.HIDE_ON_GROUND else 1
        log("settings:", self.settings.DISPLAY_MODE, self.settings.COLOUR_MODE,
            "ground", "hide" if self.settings.HIDE_ON_GROUND else "show")

    def _settings_tap(self, tx, ty):
        if not self._in_rect(tx, ty, self.spanel):
            self.settings_open = False                        # tap outside closes
            return
        row = (ty - self.sp_row0) // self.sp_rowh
        if 0 <= row <= 2:
            self.toggle_setting(row)                          # cycle value, stay open
        else:
            self.settings_open = False                        # title / footer taps close

    def handle_tap(self, tx, ty):
        if self.settings_open:
            self._settings_tap(tx, ty)
            return
        if self._in_rect(tx, ty, self.settings_btn):
            self.settings_open = True
            self.set_selected(None)      # settings and the detail panel are exclusive
            return
        # A tap inside the open sidebar is for the panel, not a dismiss.
        if self.selected is not None and tx >= self.panel_x:
            return
        best, best_d = None, self.hit_radius * self.hit_radius
        for x, y, p in self.renderer.last_drawn:
            d = (x - tx) * (x - tx) + (y - ty) * (y - ty)
            if d < best_d:
                best, best_d = p, d
        self.set_selected(best)      # None => tapped empty space => dismiss
