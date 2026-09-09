import asyncio

import routes
import traces
from netlog import log

WIDTH, HEIGHT = 480, 480               # fixed: this hardware's full_res display size


def _advance_selection(selected, level, tapped, cycle_len=3):
    """Tap-cycle state machine. `tapped` is the plane under the tap, or None
    for empty space. `cycle_len` is how many stages the cycle has -- 2 in
    radar mode (data block -> + corner card), 1 in map mode (select ->
    corner card; UI-TRAILS.md "Map mode notes"). Returns (new_selected,
    new_level):

      - empty space        -> (None, 1)                 dismiss
      - a different plane   -> (tapped, 1)              select fresh at stage 1
      - the same plane      -> (selected, level % cycle_len + 1) cycle 1..N -> 1
    """
    if tapped is None:
        return None, 1
    if tapped is not selected:
        return tapped, 1
    return selected, level % cycle_len + 1


class UI:
    """Owns touch handling, the selected-aircraft state, and the settings
    overlay -- the last piece of
    REFACTORING.md #1's file split, and the first module state like
    `selected`/`view_cx`/`settings_open` gets an actual home in rather than
    being passed around as an argument: `Backdrop` and `Renderer` both took
    `view_cx`/`selected` as method arguments because neither of them was the
    right owner; this class is.

    `backdrop` and `renderer` are held by reference (constructed before this
    class, in radar.py) -- normal composition, not a global reach-around.
    `hidden` (the HIDE_ON_GROUND draw-time filter) stays injected rather than
    owned here even though this class uses it too: it's also `Renderer`'s,
    and neither owns the underlying settings check. `request_redraw` is
    injected for the same kind of reason -- it's radar.py's asyncio.Event
    across the render/touch task split (REFACTORING.md #4), and UI has no
    business owning that.
    """

    def __init__(self, settings, backdrop, renderer, hidden, request_redraw,
                 hit_radius,
                 settings_btn, spanel, sp_row0, sp_rowh):
        self.settings = settings
        self.backdrop = backdrop
        self.renderer = renderer
        self.hidden = hidden
        self.request_redraw = request_redraw
        self.hit_radius = hit_radius
        self.settings_btn = settings_btn
        self.spanel = spanel
        self.sp_row0 = sp_row0
        self.sp_rowh = sp_rowh

        self.selected = None      # the selected plane.Plane, or None
        self.detail_level = 1     # tap-cycle stage (radar 1..2, map 1), while selected
        self.view_cx = WIDTH // 2  # x-pixel that km-east 0 maps to (see radar.py's to_screen)
        self.settings_open = False
        self._backdrop_dirty = False  # set by toggle_setting() when DISPLAY_MODE
                                       # changes; see maybe_rebuild_backdrop()

    # --- Tap to inspect (item 2a) -------------------------------------

    def set_selected(self, p):
        # Select p, or None to dismiss. No view shift any more -- the detail
        # card sits in a corner (UI-TRAILS.md decision 9), so the scene stays
        # centred. Kicks the route and trace lookups, same as before.
        if p is None:
            self.detail_level = 1
        self.selected = p
        if p is not None:
            routes.request(p)
            traces.request(p)

    def maybe_rebuild_backdrop(self):
        """Called once per frame by radar.py's _render_loop, right after
        draw_scene(). The only thing that sets _backdrop_dirty now is the
        DISPLAY_MODE toggle in the settings overlay (toggle_setting): the
        backdrop is a static layer-0 / vector-cache draw, so a mode change
        needs it rebuilt explicitly (the aircraft icons already follow
        DISPLAY_MODE every frame). The view-shift-on-select machinery that
        used to drive this path is gone (UI-TRAILS.md decisions 1/9).
        Backgrounding the rebuild as a task from here, rather than inline in
        toggle_setting(), keeps the ~380 ms raster decode off the touch
        handler and sidesteps the hang the old inline build_vector_cache()
        call hit on-device."""
        if self._backdrop_dirty:
            self._backdrop_dirty = False
            asyncio.create_task(self._rebuild_backdrop())

    async def _rebuild_backdrop(self):
        # Runs only after a DISPLAY_MODE toggle (see maybe_rebuild_backdrop).
        # view_cx no longer moves -- the detail-panel view shift was removed
        # (UI-TRAILS.md decisions 1/9) -- so self.view_cx is always
        # WIDTH // 2 and the display_view_cx assignment below is effectively a
        # no-op, kept only so the two stay coupled if a shift is ever
        # reintroduced. The real work is rebuilding the backdrop for the new
        # mode: Backdrop.redraw() swaps layer 0 between the raster and the
        # vector grid on a map-capable boot; build_vector_cache() re-projects
        # the coastline on a radar boot.
        log("ui: updating reticle")
        self.backdrop.display_view_cx = self.view_cx
        log("ui: reticle updated")
        if self.backdrop.map_layers:
            log("ui: updating background")
            self.backdrop.redraw(self.view_cx, self.selected)
            log("ui: background updated")
        else:
            log("ui: updating coast vector")
            self.backdrop.build_vector_cache()
            log("ui: coast vector updated")
        self.request_redraw()   # show the corrected backdrop as soon as it's ready

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
        # it, or clear it if that aircraft has
        # dropped off -- or is now hidden by HIDE_ON_GROUND (dismiss_if_hidden()
        # would clear it on the next redraw anyway; doing it here skips that
        # one extra tick of a stale selection).
        if self.selected is not None:
            h = self.selected.hex
            self.set_selected(next((q for q in fresh
                                     if q.hex == h and not self.hidden(q)), None))

    # --- Settings overlay (PLAN 2b phase 1: in-memory toggles, no persistence) --

    def _in_rect(self, px, py, r, margin=0):
        return (r[0] - margin <= px <= r[0] + r[2] + margin
                and r[1] - margin <= py <= r[1] + r[3] + margin)

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
            #
            # Backgrounded via the same _backdrop_dirty/maybe_rebuild_backdrop()
            # path a selection shift uses (REFACTORING.md #4), not called
            # inline here as it used to be: an on-device lockup traced to
            # this exact call -- toggling to "radar" hung inside
            # build_vector_cache(), logged "updating coast vector" and never
            # returned -- while the identical build_vector_cache() call via
            # the backgrounded path has run cleanly throughout this session's
            # testing. The actual mechanism wasn't pinned down (nothing about
            # this call looked different enough on paper to explain a hang
            # rather than just being slow), but routing it through the one
            # path already proven to work rather than the one synchronous
            # backdrop rebuild left in the codebase is the safer fix either
            # way. view_cx itself doesn't change here -- _rebuild_backdrop()
            # reads self.view_cx/self.selected fresh when it actually runs,
            # so this just needs the flag.
            self._backdrop_dirty = True
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
            log("ui: settings dismissed (tap outside panel)")
            return
        row = (ty - self.sp_row0) // self.sp_rowh
        if 0 <= row <= 2:
            self.toggle_setting(row)                          # cycle value, stay open
        else:
            self.settings_open = False                        # title / footer taps close
            log("ui: settings dismissed (title/footer tap)")

    def handle_tap(self, tx, ty):
        # Every reachable path below is a real edge-triggered tap that's
        # meant to change something on screen (select/dismiss, open/close
        # settings, toggle a setting) -- request_redraw() unconditionally
        # here rather than threading a "did this actually change anything"
        # check through each branch (REFACTORING.md #4). Worst case, a
        # genuine no-op tap costs one redraw no earlier than it would have
        # happened anyway; that's negligible against the latency this is
        # fixing.
        self.request_redraw()
        if self.settings_open:
            self._settings_tap(tx, ty)
            return
        # +5px margin: SETTINGS_BTN (radar.py) doesn't quite reach the physical
        # corner (WIDTH-1, HEIGHT-1), so a tap aimed at the corner itself --
        # a natural target for a bottom-right icon -- can land just past its
        # tight hitbox and miss.
        if self._in_rect(tx, ty, self.settings_btn, margin=5):
            log("ui: settings opened")
            self.settings_open = True
            self.set_selected(None)      # settings and the detail panel are exclusive
            return
        # Map mode's tap cycle is a single stage (select -> card), radar's is
        # 2 (data block -> + card) (UI-TRAILS.md "Map mode notes").
        cycle_len = 1 if self.settings.DISPLAY_MODE == "map" else 2

        # Normal nearest-hit search FIRST, so a tap that's clearly on another
        # aircraft selects it even while something else is selected.
        best, best_d = None, self.hit_radius * self.hit_radius
        for x, y, p in self.renderer.last_drawn:
            d = (x - tx) * (x - tx) + (y - ty) * (y - ty)
            if d < best_d:
                best, best_d = p, d

        # Widened target is only a FALLBACK: no normal hit landed, but the tap
        # is within ~1.6x the hit radius of the current selection -- treat it
        # as a slightly-off re-tap that advances the cycle rather than a
        # background tap that dismisses. (It no longer runs ahead of the
        # nearest-hit search, so it can't swallow a nearer neighbour.)
        if best is None and self.selected is not None:
            for x, y, p in self.renderer.last_drawn:
                if p is self.selected:
                    r = self.hit_radius * 1.6
                    if (x - tx) ** 2 + (y - ty) ** 2 <= r * r:
                        _, self.detail_level = _advance_selection(
                            self.selected, self.detail_level, self.selected,
                            cycle_len=cycle_len)
                        self.set_selected(self.selected)  # keep route/trace warm
                        return
                    break

        if best is not None:
            log("ui: plane tapped", best.label)
        elif self.selected is not None:
            log("ui: panel dismissed (background tap)")
        else:
            log("ui: background tapped, nothing selected")
        # Tap-cycle: a second tap on the already-selected plane advances the
        # detail level; a tap elsewhere selects or dismisses (level resets to
        # 1 via set_selected / _advance_selection).
        sel, self.detail_level = _advance_selection(self.selected, self.detail_level,
                                                    best, cycle_len=cycle_len)
        self.set_selected(sel)
