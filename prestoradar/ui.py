import asyncio

import routes
import traces
from netlog import log

WIDTH, HEIGHT = 480, 480               # fixed: this hardware's full_res display size


def _advance_selection(selected, level, tapped):
    """Tap-cycle state machine. `tapped` is the plane under the tap, or None
    for empty space. Returns (new_selected, new_level):

      - empty space        -> (None, 1)                 dismiss
      - a different plane   -> (tapped, 1)              select fresh at stage 1
      - the same plane      -> (selected, level % 3 + 1) cycle 1 -> 2 -> 3 -> 1
    """
    if tapped is None:
        return None, 1
    if tapped is not selected:
        return tapped, 1
    return selected, level % 3 + 1


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
    and neither owns the underlying settings check. `request_redraw` is
    injected for the same kind of reason -- it's radar.py's asyncio.Event
    across the render/touch task split (REFACTORING.md #4), and UI has no
    business owning that.
    """

    def __init__(self, settings, backdrop, renderer, hidden, request_redraw,
                 px_per_km, panel_x, hit_radius, panel_margin, max_shift, min_view_cx,
                 settings_btn, spanel, sp_row0, sp_rowh):
        self.settings = settings
        self.backdrop = backdrop
        self.renderer = renderer
        self.hidden = hidden
        self.request_redraw = request_redraw
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

        self.selected = None      # the selected plane.Plane, or None
        self.detail_level = 1     # tap-cycle stage 1..3, meaningful while selected
        self.view_cx = WIDTH // 2  # x-pixel that km-east 0 maps to (see radar.py's to_screen)
        self.settings_open = False
        self._backdrop_dirty = False  # set by set_selected() (view_cx changed) or
                                       # toggle_setting() (DISPLAY_MODE changed); see
                                       # maybe_rebuild_backdrop()

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
        display.circle()/line()) uncast, and p.e * px_per_km is a float.
        """
        if p is None:
            return WIDTH // 2
        x0 = WIDTH // 2 + p.e * self.px_per_km    # p's unshifted screen x
        wanted = WIDTH // 2 - max(0, x0 - (self.panel_x - self.panel_margin))
        return int(max(wanted, self.min_view_cx))

    def set_selected(self, p):
        # Select p (or None to dismiss), shift the view just enough to keep
        # p clear of the panel, and kick a route lookup.
        #
        # self.view_cx is the *target*, updated immediately below when it
        # needs to change; the panel (fixed layout, doesn't care about
        # view_cx) and `selected` itself still take effect on the very next
        # frame, so a tap's response is still instant where it can be. But
        # radar.py's to_screen() -- and, in single-layer mode, the grid --
        # read backdrop.display_view_cx, not this, for actual pixel
        # positions: aircraft/ring/backdrop only move once
        # _rebuild_backdrop() advances display_view_cx to match, all
        # together in the same frame, rather than the aircraft jumping to
        # the new position while the backdrop (raster ~380ms decode, or the
        # vector cache rebuild) is still catching up. Flagged dirty here,
        # not kicked off directly -- see maybe_rebuild_backdrop()'s
        # docstring for why that ordering has to go through _render_loop
        # rather than being started right here (REFACTORING.md #4).
        had_selection = self.selected is not None
        self.selected = p
        if p is None:
            self.detail_level = 1
        cx = self._target_view_cx(p)
        if cx != self.view_cx:
            mode = self.settings.DISPLAY_MODE
            direction = "left" if cx < self.view_cx else "right"
            log("ui: shifting", mode, "by", abs(cx - self.view_cx), "pixels", direction,
                "(view_cx", self.view_cx, "->", cx, ")")
            self.view_cx = cx
            self._backdrop_dirty = True
        elif (p is not None) != had_selection:
            # view_cx isn't changing, but a redraw is still needed: on a
            # map-capable boot, the grid is only redrawn (Backdrop.redraw(),
            # via _rebuild_backdrop()) when _backdrop_dirty fires, and
            # draw_radar_grid()'s crosshair length depends on `selected is
            # not None` on its own, independent of view_cx -- e.g. dismissing
            # a plane that needed no shift in the first place (already clear
            # of the panel) leaves view_cx unchanged, so without this branch
            # the crosshair would stay frozen at its shortened, panel-open
            # length forever after, even once nothing's selected (confirmed
            # on-device: a screenshot with the legend and aircraft count both
            # showing -- nothing selected -- but the crosshair still cut
            # short at the old panel edge).
            log("ui: refreshing", self.settings.DISPLAY_MODE,
                "backdrop for selection change (view_cx unchanged at", cx, ")")
            self._backdrop_dirty = True
        if p is not None:
            routes.request(p)
            # Seed the position trail from adsb.lol's trace_recent. Lazy and
            # cached with a TTL like routes.request(), so being re-called for
            # the same aircraft every fetch (via on_feed_update) is cheap. A
            # no-op when settings.TRACE_SEED is 0 -- the in-RAM trail still
            # accumulates and renders.
            traces.request(p)

    def maybe_rebuild_backdrop(self):
        """Called once per frame by radar.py's _render_loop, right after
        draw_scene() -- i.e. only after the current frame has already been
        drawn at the current view_cx. Backgrounding the rebuild as a task
        from here, rather than from set_selected() itself, is what
        guarantees that ordering: this call is sequenced after draw_scene()
        in the same uninterrupted turn, so the task's body can't possibly
        run before this frame's draw does."""
        if self._backdrop_dirty:
            self._backdrop_dirty = False
            asyncio.create_task(self._rebuild_backdrop())

    async def _rebuild_backdrop(self):
        # Has to advance BEFORE either branch below runs, not after: both
        # Backdrop.redraw()'s vector-fallback branches and the direct
        # build_vector_cache() call here project through to_screen(), which
        # reads this value. Setting it afterward (the bug this replaced) left
        # the coastline cache always one shift stale -- built for whatever
        # display_view_cx was *before* this rebuild, one tap behind the
        # aircraft/grid, which already move to the new position via the same
        # to_screen(). On-device this looked exactly backwards: the coastline
        # sat at its normal, centred position while a plane was selected
        # (rebuilt for the *previous*, centred view before the shift), and
        # shifted left once dismissed (rebuilt for the *previous*, shifted
        # view before returning to centre). No async gap between this
        # assignment and the rebuild using it -- _rebuild_backdrop() has no
        # awaits before request_redraw(), so nothing else can run and observe
        # the two out of step.
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
        # it, or clear it (and un-shift the view) if that aircraft has
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
        # A tap inside the open sidebar is for the panel, not a dismiss.
        if self.selected is not None and tx >= self.panel_x:
            return
        # The already-selected plane gets a larger tap target, so a slightly
        # off second tap advances the cycle instead of dismissing.
        if self.selected is not None:
            for x, y, p in self.renderer.last_drawn:
                if p is self.selected:
                    r = self.hit_radius * 1.6
                    if (x - tx) ** 2 + (y - ty) ** 2 <= r * r:
                        self.set_selected(self.selected)  # keep route/trace warm
                        self.detail_level = self.detail_level % 3 + 1
                        return
                    break
        best, best_d = None, self.hit_radius * self.hit_radius
        for x, y, p in self.renderer.last_drawn:
            d = (x - tx) * (x - tx) + (y - ty) * (y - ty)
            if d < best_d:
                best, best_d = p, d
        if best is not None:
            log("ui: plane tapped", best.label)
        elif self.selected is not None:
            log("ui: panel dismissed (background tap)")
        else:
            log("ui: background tapped, nothing selected")
        # Tap-cycle: a second tap on the already-selected plane advances the
        # detail level; a tap elsewhere selects or dismisses (level resets to
        # 1 via set_selected / _advance_selection).
        sel, self.detail_level = _advance_selection(self.selected, self.detail_level, best)
        self.set_selected(sel)
