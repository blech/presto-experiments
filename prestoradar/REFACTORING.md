# presto-radar — refactoring proposal

`radar.py` has grown to ~1070 lines by accretion (PLAN.md items 1-8 landing
one at a time) and it shows: fetch, parse, project, cache, draw, touch, and
settings all live as module-level functions mutating ~15 module-level
globals (`_planes`, `_selected`, `_view_cx`, `_settings_open`, `_map_layers`,
`_showing_raster`, `_BASEMAP_SEGS`, `_BASEMAP_MARKS`, `_route_cache`,
`_last_drawn`, `_fetch_count`, `_fetch_ok`, `_basemap_ms`, plus the
runtime-toggled "constants" `DISPLAY_MODE`/`COLOUR_MODE`/`HIDE_ON_GROUND`
imported from `settings.py`). This works — MicroPython's cooperative
`asyncio` means none of it races — but every function can reach into every
other function's state, which makes the file hard to hold in your head and
impossible to unit-test off-device.

This is a proposal, landing incrementally per §8's order. PLAN.md keeps the
feature backlog; this is about the *shape* of the code carrying those
features. Where a PLAN item already tracks something below, it's
cross-referenced rather than duplicated.

Nothing here should change behaviour on its own — it's a reorganisation to
make the next PLAN items (5's layer model, 6's label culling, 2b's
persistence) easier to land, plus two concrete bugs it happens to fix
(colour-mode/theme duplication, live ground-toggle).

**Progress:** §8 steps 1 (`Settings` object), 2 (live ground-toggle) and 3
(theme table) are done and verified on-device. §1 (file split) is
**complete** -- `net.py`, `geometry.py`, `routes.py`, `feed.py`,
`backdrop.py`, `render.py` and `ui.py` are all out and verified. §4 (touch
latency) is still proposal only. §9 (persistent aircraft identity + trail
history) is a new proposal, not part of the original split.

---

## 1. Split fetch/parse from draw/display

Right now `fetch_planes()` (radar.py:471-556) does two jobs at once: HTTP and
JSON parsing (the on-ground presentation filter it also used to apply,
`HIDE_ON_GROUND`, is done -- see §5, now a draw-time `_hidden()` check
instead). Drawing (`draw_scene`, `draw_planes`, `draw_panel`,
`draw_legend_alt`, `draw_basemap`, the settings overlay) is a second,
separate concern that happens to currently sit in the same file, same
globals, same namespace.

Proposed split:

- **`net.py`** — **done.** `http_get()` (was `_http_get()`, radar.py:410-468),
  the minimal async HTTPS GET. Landed as sketched: `net.py` takes
  `user_agent` as a parameter rather than importing `settings` itself, so
  it stays a plain, dependency-free module (only `asyncio`/`ssl`); both
  `fetch_planes()` and `_fetch_route()` now call `net.http_get(...,
  USER_AGENT)`. `radar_deploy.sh` copies it alongside `radar.py`. First of
  the split's pieces landed since it had no shared mutable state to
  untangle -- everything below still has `_view_cx`/`_selected`/etc. to
  sort out first.
- **`feed.py`** — **done.** A `Feed` class holds what were the
  `_planes`/`_fetch_count`/`_fetch_ok` module globals as `.planes`/
  `.fetch_count`/`.fetch_ok` attributes, parses exactly like the old
  `fetch_planes()` did (§5 already covers `HIDE_ON_GROUND` -- every
  aircraft is real data, not filtered at parse time), and runs the fetch
  loop as an `async def run(self)` method radar.py's `_amain()` gathers
  instead of a bare `_fetch_loop()`. One thing the original sketch didn't
  anticipate: the fetch loop also used to re-point the UI's selection at
  the same aircraft in the fresh list, which `feed.py` has no business
  knowing about. Resolved with an `on_update(planes)` hook -- `Feed` calls
  it (if set) after every successful fetch, and radar.py assigns its own
  `_on_feed_update()` to it, keeping the selection-repointing logic exactly
  where it always lived without `feed.py` needing to import anything about
  selection or panels. `ui.py`, when it lands, just takes over that one
  assignment.
- **`routes.py`** — **done**, though landed as two calls rather than the
  single `resolve_route()` sketched originally: `request(callsign)`
  (fire off a lookup if one isn't cached or in flight yet -- what
  `_set_selected()` used to do inline) and `get(callsign)` (read the
  current cached state, called every panel redraw by `_fmt_route()`).
  Splitting them avoids a naive `resolve_route()` accidentally
  re-triggering a fetch every time the panel reads it for display, which a
  single combined call would risk. The route lookup itself is a
  known-naive placeholder (a single, unverified adsbdb hit — no
  cross-check, no fallback if adsbdb's callsign→route mapping is stale or
  just wrong for a shared/renumbered flight number), and a smarter
  replacement is planned — the point of the split holds regardless of
  which shape won: `is_hex_id()`, the cache, and all fetch/scoring logic
  are private to the module (`_fetch()`, `_cache`); `radar.py` only ever
  calls `routes.request()`/`routes.get()`/`routes.is_hex_id()` and formats
  whatever comes back (`_fmt_route`, still in `radar.py` for now --
  presentation, not resolution, moves to `render.py`/`ui.py` when those
  land). That means swapping the naive single-GET for a more sophisticated
  algorithm — multiple sources, scoring, whatever the design from your
  other conversation turns out to need — is a change entirely inside
  `routes.py`: same call
  signature, same cache shape, zero ripple into the panel drawing or touch
  handling that currently sit next to it in radar.py. Also the natural
  place to cap the cache (see §6).
- **`render.py`** — **done**, and the largest single piece of the whole
  split (every `draw_*` function, `plane_pen`, the icon/rotor drawing
  helpers, and every pen -- `radar.py` dropped from 753 to 388 lines,
  `render.py` is 440). A `Renderer` class owns all of it, plus `theme()`
  (was `_theme()`). As with `Backdrop`, several things it needs still have
  no stable owner and are taken as method arguments instead of stored:
  `selected`, `settings_open` and `view_cx` (all UI-driven), and `to_screen`/
  `hidden` (`_hidden`, injected the same way `Backdrop` takes `to_screen` --
  `hidden` is also used by radar.py's own selection-repointing, so it isn't
  Renderer's alone to own). `Renderer` and `Backdrop` also need each other
  -- `Renderer.theme()` reads `backdrop.showing_raster`, but `Backdrop`'s
  constructor needs `Renderer`'s pens and its `draw_radar_grid` method.
  Resolved the same way `_feed.on_update` was: `Renderer` is constructed
  first with `backdrop` left unset, `Backdrop` is built from pieces off it,
  then `renderer.backdrop = backdrop` closes the loop.

  One real behavioural question came out of this move, not just relocation:
  `draw_planes()` used to both draw *and* decide "is the selection still
  valid" (dismissing it inline if the plane it pointed at had just become
  hidden). A drawing function silently mutating the selection doesn't
  belong in `Renderer` -- so that check moved to `_render_loop()` in
  radar.py, run *before* calling `Renderer.draw_scene()` each frame rather
  than partway through it. `Renderer.draw_planes()` now only ever draws
  whatever `selected` it's handed; it never changes it. Net effect on
  behaviour is a one-frame improvement, not a regression: previously, the
  exact frame a selection became hidden could show a stale panel/legend
  state for that one tick (the dismiss happened mid-draw, after the legend
  visibility was already decided); now the dismiss happens before any of
  that frame's drawing decisions are made, so they're consistent within the
  same frame.

  Also worth recording since it's the kind of bug only a cross-file check
  catches, not `py_compile`: `Backdrop.redraw()`'s vector-grid fallback
  calls the injected `draw_grid` callback -- which used to be a bare
  zero-argument `draw_radar_grid()`, but became `Renderer.draw_radar_grid(view_cx,
  selected)` in this same move. `Backdrop.redraw()`/`load()` gained a
  `selected` parameter to thread through, or every path that falls back to
  the vector grid (a missing/corrupt raster, or `DISPLAY_MODE == "radar"`)
  would have raised `TypeError` on the device. Caught and fixed before
  presenting this step for on-device testing.
- **`backdrop.py`** — **done**, and turned out considerably more entangled
  than "only talks to `basemap_data` and `jpegdec`" suggested: `draw_basemap()`
  used pens (`COAST_PEN`/`AIRPORT_PEN`) and its raster-missing fallback called
  `draw_radar_grid()` directly, `build_basemap_cache()` called `to_screen()`
  (which reads `_view_cx`), and `_draw_map_backdrop()` read `SETTINGS.DISPLAY_MODE`
  live. A `Backdrop` class now owns `map_layers`/`showing_raster`/the vector
  segment+mark cache and exposes `build_vector_cache()` / `draw_vector()`
  (was `build_basemap_cache`/`draw_basemap`) and `load(view_cx)` /
  `redraw(view_cx)` (was `load_raster_basemap`/`_draw_map_backdrop`).
  Two things it deliberately does *not* own, both injected at construction
  instead, following the same precedent §1's `to_screen`/`_target_view_cx`
  already set (leave state with no home yet where it is, don't force a move
  that just relocates the coupling):
  - **`view_cx`** stays a plain `radar.py` global -- it's UI-driven state
    (the panel opening/shifting), so every `Backdrop` method that needs it
    (`load`/`redraw`; `build_vector_cache()` doesn't, it goes through the
    injected `to_screen` instead) takes it as an argument rather than
    storing it.
  - **`draw_grid`** (the rings/crosshairs) is injected as a callback --
    it's a rendering concern (pens, no basemap data), so `Backdrop` calls
    it rather than owning it, staying put until `render.py` exists.
  `Backdrop` takes `settings` (for live `DISPLAY_MODE`), the boot-time
  `raster_ok`/`draw_basemap_flag` decisions, `basemap_data`, and three pens,
  all as constructor arguments -- no import of `radar.py`, so no
  circularity. `WIDTH`/`HEIGHT` (480, fixed for this hardware) and
  `RASTER_PATH` are plain module constants in `backdrop.py` itself, same as
  they were literals in `radar.py`.
- **`ui.py`** — **done**, and the last piece: a `UI` class holding
  `handle_tap`/`_settings_tap`/`toggle_setting`/`_target_view_cx` (was
  `_toggle_setting`/`_target_view_cx` etc., unprefixed where they're now
  public methods). This is the one class in the whole split where
  `selected`/`view_cx`/`settings_open` finally get an actual home as
  instance attributes, rather than being threaded through as method
  arguments the way `Backdrop`/`Renderer` had to -- they exist precisely
  *because* nothing else was the right owner, and now something is. `UI`
  holds `backdrop`/`renderer` by reference (plain composition -- both were
  already constructed) and takes `hidden` injected, same as `Renderer`
  does, since neither owns the underlying `HIDE_ON_GROUND` check.
- **`radar.py`** — **done shrinking**, though it stayed the entry point
  rather than disappearing: 753 lines at the start of this split, 288 now.
  It builds every object in dependency order (`Settings` -> `Feed` ->
  `Renderer` -> `Backdrop` -> `UI`, the last two needing pieces of the ones
  before them), wires the two two-phase assignments (`renderer.backdrop`,
  `feed.on_update`), and runs `asyncio.gather(_render_loop(), _feed.run(),
  _touch_loop())`. Still calls its entry function unconditionally at module
  scope, no `if __name__` guard — MicroPython's launcher runs the file
  directly and a guard means it silently does nothing. What's left in it:
  `to_screen()` (needs `_ui.view_cx`, still no cleaner home), `_hidden()`
  (shared by `Renderer` and `UI`, owned by neither), Presto/display setup,
  and the three async loops.

Net effect: `feed.py` can be imported and its parsing tested against a
canned adsb.lol JSON fixture on a laptop with plain CPython — currently the
only way to exercise `fetch_planes()`'s parsing logic at all is flash,
reset, and read the serial log.

---

## 2. Encapsulate in objects — and why settings has to move first

**Done so far: the `Settings` slice below, plus `feed.py`'s `Feed` and
`routes.py`.** Both landed a little differently than this section first
sketched -- see the note after each in the table -- but the underlying
shape held. `Backdrop`, `Renderer`, `UI` as actual classes in their own
modules are still proposal, landing with the rest of §1's file split.

The natural boundaries from §1 map onto a small number of classes rather
than a pile of same-named functions in different files:

```
Settings        # done: copied from settings.py at boot; the one mutable source
                # of truth for DISPLAY_MODE / COLOUR_MODE / HIDE_ON_GROUND
Feed            # done, as sketched but plainer: feed.py's Feed takes plain
                # constructor args (host/path/user_agent/level_rate_fpm/
                # fetch_interval_ms), not a Settings instance -- none of
                # those change at runtime, so there was nothing live to read.
                # request()/get() live at module scope in routes.py instead of
                # a RouteCache class -- there's no per-instance state to justify
                # one; a single shared cache is exactly what's wanted.
Backdrop        # owns _map_layers / _showing_raster / the vector cache
Renderer        # owns the Presto/display handle, the pens, draw_scene()
UI              # owns _selected / _settings_open / _view_cx; handle_tap()
RadarApp        # wires the above, runs the three asyncio tasks
```

**Why settings needs to move into object state, specifically, before the
rest of this split is safe:** today `radar.py` does `from settings import
*` (line 19) and `_toggle_setting()` (668-683) mutates the result with
`global DISPLAY_MODE`. That's importing a *reference* into radar.py's own
namespace — it happens to work today only because radar.py is the *only*
module that reads `DISPLAY_MODE`/`COLOUR_MODE`/`HIDE_ON_GROUND` at runtime.
The moment §1's split moves parsing into `feed.py` and `feed.py` also does
`from settings import *` to see `HIDE_ON_GROUND`, it gets its own frozen
copy from import time — `_toggle_setting()` flipping radar.py's copy would
silently leave `feed.py`'s copy (and therefore the actual filter) unchanged.
That failure mode is invisible until someone taps the settings toggle and
nothing happens.

Fix: build one `Settings` object at boot (attributes copied from the
`settings` module — a plain `for k in dir(settings_module)` walk, or an
explicit field list), pass *that instance* to whatever needs it, and have
`_toggle_setting()` mutate `SETTINGS.DISPLAY_MODE` etc. Every reader then
sees the live value because they're all holding the same object, not a
`from module import *` snapshot. `settings.py` itself doesn't need to
change shape — it's still the plain, gitignored, per-location constants
file `deploy.sh` copies down; only how `radar.py` consumes it changes.

**How §5 and `feed.py` actually turned out:** the live ground-toggle (§5)
landed before the file split, and the filter it added (`_hidden()`) stayed
in `radar.py` reading `SETTINGS.HIDE_ON_GROUND` directly -- it was never
`feed.py`'s concern, since "is this aircraft currently hidden" is a
draw-time question, not a fetch-time one. So when `feed.py`'s `Feed` landed
later, it turned out to need *no* live settings at all: `host`/`path`/
`user_agent`/`level_rate_fpm`/`fetch_interval_ms` are all fixed at boot,
so they're passed as plain constructor arguments rather than a `Settings`
reference. The predicted trap (a second `from settings import *` freezing
a stale copy) was avoided by design, just not the way this section
originally guessed -- worth remembering that "does this module need a
`Settings` reference" is a case-by-case question, not automatic for every
split-out module.

A note on cost: MicroPython attribute lookups (`self.x`) are marginally
slower than a bare global, but the render loop runs at ~2 fps and touch at
20 Hz — nowhere near where that difference would be measurable. The actual
per-frame cost here is `jpegdec` decodes and coastline segment counts
(PLAN item 8's numbers), both unaffected by this.

---

## 3. Colour configuration vs map/scope mode

**Done.** Landed close to as sketched below, with two differences worth
recording: `_theme()` is a plain module function (`THEMES["map"] if
_showing_raster else THEMES["radar"]`), not a `Renderer.theme` property --
there's no `Renderer` object yet, that's still §1/§2's file split; and the
grid-pen question resolved as "keep the same name" -- `draw_radar_grid()`
references `RADAR_ICON_COLOR` directly rather than a separate
`RADAR_GRID_PEN`, since nothing yet needs them to be distinct values.

Before, "what colour is this thing" was decided by re-deriving the same
ternary at every call site:

- `plane_pen()` (742-752): `MAP_VSTATE_PENS if _showing_raster else
  VSTATE_PENS` (per-vstate case), then separately `MAP_TEXT_PEN if
  _showing_raster else RADAR_GREEN` (mono case).
- `draw_legend_alt()` (717-737): the same `_showing_raster` branch,
  independently, to pick `pens`/`text_pen`.
- `draw_scene()`'s status line (919): `MAP_TEXT_PEN if _showing_raster else
  TEXT_COLOR`, a third copy of the same test.

Three call sites independently re-deriving the same fact means a fourth
place that needs it (say, a new label per PLAN item 6) is one more copy to
keep in sync, and it already *did* drift once — that's exactly what PLAN
item 8's "runtime toggle" bug was (`_map_layers` vs `_showing_raster`
picked inconsistently across call sites until it was tracked down).

It's also a naming mess in its own right: `TEXT_COLOR` and `RADAR_GREEN`
(the scope pens) are bare, unprefixed names, while their raster
counterparts are `MAP_TEXT_PEN`/`MAP_VSTATE_PENS` — so the two halves of
every pair don't read as a pair. Standardise on a symmetric `RADAR_*` /
`MAP_*` naming for every themed pen, one name per role:

| role | scope (dark) | raster (light) |
|---|---|---|
| status/legend text | `RADAR_TEXT_PEN` *(was `TEXT_COLOR`)* | `MAP_TEXT_PEN` *(unchanged)* |
| mono-mode aircraft | `RADAR_ICON_COLOR` *(was `RADAR_GREEN`)* | `MAP_ICON_COLOR` *(new name)* |
| per-vstate aircraft | `RADAR_VSTATE_PENS` *(was `VSTATE_PENS`)* | `MAP_VSTATE_PENS` *(unchanged)* |

Two things to settle deliberately while renaming, not let fall out of a
find-and-replace:

- **`RADAR_ICON_COLOR` vs the grid pen.** `RADAR_GREEN` currently does
  double duty — it's both the mono-mode aircraft pen (`plane_pen()`, 752)
  *and* the ring/crosshair pen (`draw_radar_grid()`, 218-224). That's
  presumably deliberate ("everything reads as the same scope green"), so
  the rename shouldn't quietly split them into two pens that could drift —
  either keep `draw_radar_grid()` referencing `RADAR_ICON_COLOR` by the
  same name, or give the grid its own `RADAR_GRID_PEN` defined as `=
  RADAR_ICON_COLOR` so the shared-value intent is written down rather than
  implied.
- **`MAP_ICON_COLOR` doesn't exist yet as its own value.** Mono mode over
  the raster currently just reuses `MAP_TEXT_PEN` (line 752) — there's no
  separate raster mono-aircraft pen today. Giving it its own name now
  (even as `MAP_ICON_COLOR = MAP_TEXT_PEN` initially) means a future "make
  mono-on-raster its own colour" doesn't require re-threading a new
  constant through `plane_pen()` again — the name is already there,
  pointing at whatever value it should hold.

With the names settled, the theme table itself (keyed by what's actually
behind the drawing — today's `_showing_raster`, **not** `DISPLAY_MODE`,
since the vector-grid fallback when a raster fails to decode is still the
dark "radar" look even while `DISPLAY_MODE == "map"`):

```python
THEMES = {
    "radar": {"text": RADAR_TEXT_PEN, "icon": RADAR_ICON_COLOR, "vstate": RADAR_VSTATE_PENS},
    "map":   {"text": MAP_TEXT_PEN,   "icon": MAP_ICON_COLOR,   "vstate": MAP_VSTATE_PENS},
}
```

`Renderer.theme` becomes one property (`THEMES["map" if
self.backdrop.showing_raster else "radar"]`), and `plane_pen()`/
`draw_legend_alt()`/the status line all read `theme["vstate"][p["vstate"]]`
/ `theme["text"]` / `theme["icon"]` instead of re-testing `_showing_raster`
each time. `COLOUR_MODE` (`mono`/`alt`) and the radar/map theme stay the
two independent axes they already are (a 2x2: mono-radar, alt-radar,
mono-map, alt-map) — the table just makes that explicit instead of
implicit in scattered ternaries. Worth noting `mono` mode on the raster
currently collapses every aircraft to the same near-black dot (line 752)
with no differentiation at all — that's presumably intentional (the
raster's equivalent of "everything scope-green"), but the theme table is
the place to confirm that's still what's wanted rather than an artifact of
the ternary.

The panel (`draw_panel`, 829-866) and settings overlay
(`draw_settings_panel`, 884-906) are correctly *not* part of this — they
paint their own opaque background first, so `RADAR_TEXT_PEN`/`PANEL_LABEL`
always have the contrast they were tuned for regardless of what's behind
the rest of the screen. Left alone, beyond the mechanical `TEXT_COLOR` →
`RADAR_TEXT_PEN` rename (they're always the dark-scope value, no ternary
needed).

---

## 4. Touch responsiveness

The touch loop is already decoupled from the render loop (20 Hz poll vs.
~2 fps redraw, PLAN item 2), so the input side is in good shape. The
remaining latency is downstream of a successful tap:

- **A tap's visible effect waits for the next scheduled render tick.**
  `handle_tap()` (697-714) only updates state (`_selected`, `_view_cx`,
  `_settings_open`); the screen doesn't actually change until
  `_render_loop`'s next `draw_scene()` call, up to `ANIM_INTERVAL` (500 ms)
  later. That's the dominant perceived-latency cost, well above the touch
  poll's own 50 ms. Fix: have the touch handler request an out-of-cycle
  redraw instead of waiting — e.g. an `asyncio.Event` the render loop waits
  on with a timeout (`asyncio.wait_for(redraw.wait(), ANIM_INTERVAL)`
  instead of a plain `asyncio.sleep`), set from `handle_tap()` whenever it
  actually changes something. (MicroPython's `asyncio` should have `Event`
  on this firmware — worth a quick on-device check before committing to
  this, same as PLAN item 2's own asyncio probe.)
- **The fixed 250 ms debounce (981-983) is on top of, not instead of,
  rising-edge detection.** `was`/`touched` already stops a held finger from
  re-firing, so the extra time-based debounce only costs latency on a
  legitimate second tap — e.g. tap a plane, then quickly tap the settings
  button — without preventing anything the edge check doesn't already
  prevent. Worth trying a shorter value (or removing it) and see if
  spurious double-fires actually reappear; if they don't, the edge check
  was always sufficient and the 250 ms is pure lag.
- **The panel-shift redraw is slow, but doesn't need to block the parts of
  the response that aren't slow.** Selecting a plane does two things:
  (a) draw the selection ring + detail panel, cheap; (b) shift `_view_cx`
  and rebuild the backdrop (~380 ms raster decode, per PLAN item 8's own
  numbers), only needed if the plane is near the edge. Right now both
  happen inside `_set_selected()` before anything is drawn. Splitting them
  — draw the ring/panel immediately at the tap, let the shifted backdrop
  catch up on the following frame if one was needed — would make the
  common case (a plane that doesn't need shifting) feel instant and only
  pay the 380 ms where a shift is actually happening.
- Hit-testing itself (`handle_tap`'s linear scan over `_last_drawn`,
  ~30-50 entries, at 20 Hz) is not a bottleneck and doesn't need touching.

**Done**, verified on-device: `asyncio.Event` works fine on this firmware,
menu-tap timing feels right, and the debounce cut to 80 ms hasn't brought
back spurious double-fires. Landed as sketched above, post-file-split so
this refers to the current module names:

- `radar.py` owns an `asyncio.Event` (`_redraw`), passed into `ui.UI` as
  `request_redraw`. `_render_loop` now does
  `asyncio.wait_for(_redraw.wait(), ANIM_INTERVAL)` instead of a plain
  `asyncio.sleep`; `UI.handle_tap()` sets it unconditionally rather than
  threading a per-branch "did this actually change anything" check through
  select/dismiss/settings-toggle -- every reachable path through
  `handle_tap()` is a real tap meant to change something, so the cost of an
  occasional no-op redraw is negligible against the latency this fixes.
- The fixed debounce is cut from 250 ms to 80 ms, not removed outright --
  same reasoning as before (the rising-edge check already does the real
  work), but shortened rather than deleted since there was no way to
  confirm from off-device that spurious double-fires stay gone.
- `UI.set_selected()` flags the backdrop dirty (`self._backdrop_dirty =
  True`) instead of rebuilding it inline or scheduling the task itself.
  `_render_loop` calls the new `UI.maybe_rebuild_backdrop()` once per frame,
  right after `draw_scene()` -- only then does it background the rebuild
  (`asyncio.create_task(self._rebuild_backdrop())`).

  **First cut got this wrong.** The original version had `set_selected()`
  call `asyncio.create_task()` directly, on the reasoning that since
  `request_redraw()` (which wakes `_render_loop`) always runs earlier in
  the same `handle_tap()` call than the `create_task()` for the backdrop,
  a FIFO ready-queue would run the render loop's resumption first. On
  device this didn't hold: aircraft taps that needed a view shift felt
  like the old blocking behaviour again (menu taps, which normally don't
  need a shift, were fine) -- the two tasks' actual start order clearly
  wasn't the simple queue-order story above. Moving the `create_task()`
  call from `set_selected()` to a point strictly *after* `draw_scene()` in
  `_render_loop`'s own body sidesteps the question entirely: within one
  uninterrupted turn of that loop, the draw is guaranteed to happen before
  the task is even created, regardless of how the scheduler orders ready
  tasks afterwards.

---

## 5. Live-toggle hide-on-ground

**Done.** Root cause was `HIDE_ON_GROUND` applied inside `fetch_planes()` as
a parse-time filter — a hidden aircraft was simply never added to the list
`_planes` became, so toggling the setting changed nothing about the list
already in memory until the next `_fetch_loop` iteration (up to
`FETCH_INTERVAL_MS` = 30 s) produced a fresh list built under the new
setting.

Shipped simpler than originally sketched here: rather than a stored
`on_ground` field, a `_hidden(p)` predicate re-derives the same check
(`p["alt"] in (0, "ground") or p["gs"] == 0`) on demand from the `alt`/`gs`
fields `fetch_planes()` already stores — no new field needed, it's the exact
expression that used to live in `fetch_planes()`, just moved. `_hidden()` is
applied at the two places that used to consume the *filtered* list:

- `draw_planes()` — skips a hidden aircraft when building `order` (which
  also feeds `_last_drawn`, so `handle_tap()`'s hit-test can't select a
  hidden aircraft either, with no separate change needed there).
- `_fetch_loop()`'s selection re-pointing — a selection landing on `_hidden`
  is treated the same as one that dropped off the feed entirely.

This makes the toggle take effect on the next redraw (≤`ANIM_INTERVAL` =
0.5 s) instead of the next fetch (≤30 s). It needed §2's `Settings` object
first, exactly as predicted: `_hidden()` reads `SETTINGS.HIDE_ON_GROUND`
live, which is the whole point of that fix.

**Behavioural call made:** if the *selected* aircraft becomes hidden (lands
while `HIDE_ON_GROUND` is on, or the setting is flipped on while it's
already down), the panel now auto-dismisses -- `draw_planes()` calls
`_set_selected(None)` when it notices `_selected` is now `_hidden()`, rather
than leaving a panel open with no matching ring/dot on-screen.

**Caught in the same pass:** `_status_text()`'s "Aircraft: N" count had the
same bug one level up -- it counted `len(planes)` on the same now-unfiltered
list, which would have silently started counting hidden aircraft. Fixed
alongside this (counts `sum(1 for p in planes if not _hidden(p))` instead),
while leaving the stale/no-data branch condition on the raw list's
truthiness, since that's asking a different question ("do we have *any*
carried-over data") than the display count is.

---

## 6. Other cleanups noticed along the way

Not asked for, but adjacent enough to flag:

- **`routes.py`'s cache is still unbounded** (already noted as open in PLAN
  item 2a, before the module existed) — one entry per distinct callsign
  ever tapped, for the life of the process. Harmless over a normal
  session, but worth a simple cap (e.g. drop the oldest entry past N) now
  that it's contained to `routes.py`'s own `_cache` rather than letting it
  grow forever on a display left running for days. Still open.
- **`SKIP_NETWORK` runs a separate, hand-duplicated loop** (`main()`,
  1030-1039): a plain `while True: draw_scene([]); time.sleep(1)` with no
  touch handling at all, instead of the real `asyncio.gather` path with an
  always-empty/no-op fetch loop. That means the settings overlay and tap
  handling — the things §4 is about improving — can't be exercised offline
  at all today; unifying the two paths (real asyncio loop, `_fetch_loop`
  just skips its network call under `SKIP_NETWORK`) would make touch/UI
  changes testable on a laptop-attached Presto with no wifi.
- **The three task loops duplicate the same error guard.**
  `_render_loop`/`_touch_loop`/`_fetch_loop` each wrap their body in
  `try/except Exception: log(...); sys.print_exception(e)`. A single
  `_safe_loop(name, body_coro)` wrapper (or a small decorator) would remove
  three copies of the same boilerplate and make it impossible for a fourth
  loop to forget it.
- **Pure math/formatting functions are already dependency-free** — **partly
  done.** `project`, `compass` (was `_compass`) and `alt_key` (was
  `_alt_key`) touch no `display`/`presto` state and are out in `geometry.py`
  now, meaning they can be unit-tested with plain `pytest` on a laptop --
  otherwise impossible for anything in this codebase, since the only test
  loop today is flash, reset, eyeball the screen. `to_screen` and
  `_target_view_cx` stay in `radar.py` for now -- both need `_view_cx`,
  which doesn't have a home yet (it becomes constructor state once §2's
  `Backdrop`/`UI` classes exist); moving them now would just relocate the
  coupling, not resolve it. `_fmt_alt`/`_fmt_route` stay too -- they're
  panel presentation, not geometry, and belong with `render.py`/`ui.py`
  when those land.

---

## 7. Suggested layout

```
prestoradar/
  radar.py       # entry point: build Settings + the objects below, run asyncio.gather
  settings.py    # unchanged shape; gitignored, per-location (settings_example.py template)
  net.py         # done: http_get()
  geometry.py    # done: project, compass, alt_key (to_screen stays in radar.py for now)
  routes.py      # done: request()/get()/is_hex_id(), adsbdb lookup + cache
  feed.py        # done: Feed (fetch, parse, .run() loop, on_update hook)
  backdrop.py    # done: Backdrop (vector cache + raster layer-0 loading)
  render.py      # done: Renderer (pens, theme table, all draw_*)
  ui.py          # done: UI (selection, tap handling, settings overlay)
  basemap_data.py  # unchanged: generated, gitignored
```

No package/`__init__.py` needed — flat modules under `/prestoradar/` the
same way `basemap_data.py`/`settings.py` already work, so `deploy.sh`
copying the directory doesn't change.

## 8. Suggested order

Each step should be independently deployable and verified on-device before
the next, the same way PLAN.md's items landed incrementally:

1. **Done.** §2's `Settings` object first — it's the prerequisite the others
   quietly depend on, and is a small, low-risk change on its own (radar.py
   keeps every other global as-is, just stops treating settings names as
   mutable module globals).
2. **Done.** §5's live ground-toggle, now that settings are shared state —
   small, testable by eye immediately (toggle, watch the next redraw).
3. **Done.** §3's theme table, verified on-device including mono-mode over
   the raster (booted with `DISPLAY_MODE = "map"`, not the on-device toggle
   -- the toggle can't reach the raster from a `"radar"` boot at all, a
   pre-existing hardware constraint unrelated to this step; see PLAN.md
   item 8 and `_draw_map_backdrop()`'s docstring).
4. **Done.** §1's file split — the big one; did it after 1-3 so there's
   less state to carry across the split, and each new module was dropped
   in with the old monolith still working as a fallback until confirmed
   on-device. Landed module by module in this order: `net.py`,
   `geometry.py`, `routes.py`, `feed.py`, `backdrop.py`, `render.py`,
   `ui.py` -- `radar.py` went from 753 lines to 288 across the whole
   sequence, all verified on-device.
5. **Done.** §4's redraw-on-tap and debounce tuning — the one item left
   from the original split. Verified on-device, including a real bug the
   initial off-device version got wrong (see §4's write-up). §9 (below) is
   a separate, later proposal -- not part of this ordering.

---

## 9. Persistent aircraft identity (trail history + route caching)

**Not started — proposal only. Revised once already** (see "Scope
correction" below) after feedback narrowed the trail to a single
aircraft, which changes the design a lot.

Prompted by a design question rather than a code smell: `feed.py`'s
`Feed._fetch()` throws away the entire plane list and rebuilds it from
scratch every fetch (every `FETCH_INTERVAL_MS`, 30 s) -- "the same
aircraft" across two fetches is only ever a coincidence of matching `hex`
strings between two otherwise-unrelated dicts, never an actual persistent
identity. That's fine for what the radar draws today (position, heading, a
detail panel), but it rules out a standard flight-tracking UI behaviour:
a trail (the aircraft's recent track, drawn as a fading line behind it) --
which needs *some* memory of where the aircraft was a minute ago, and a
rebuilt-every-fetch dict fundamentally can't hold that.

### Scope correction: one trail, not one per aircraft

The first draft of this section assumed every visible aircraft needed a
persistent trail, and sized the design around that (a `Feed`-wide
`hex -> Plane` registry, replacing `self.planes` entirely). That's not
what real flight-tracking UIs do, and it's not what's wanted here either:
**a trail only ever shows for the one selected aircraft.** That shrinks
the whole feature enormously -- it needs memory of *one* aircraft's recent
positions, not all thirty-odd currently on screen, and that memory only
needs to exist while something is selected.

Which means the trail can live entirely in `ui.py`, as `UI` state, with
**no `Feed`/`Plane`/registry rework at all**:

- `UI` gains `self.trail` -- a bounded `collections.deque(maxlen=...)` of
  past `(e, n)` positions for whatever's currently selected. (MicroPython's
  `deque` requires `maxlen` up front and silently drops the oldest point
  once full -- no manual trimming needed.)
- `set_selected(p)` resets `self.trail` (clears it, or seeds it with just
  `p`'s current position) whenever the selection changes to a different
  aircraft, or is dismissed.
- `on_feed_update(fresh)` -- which already re-points the continuing
  selection to its matching entry in the fresh list every ~30 s fetch --
  appends the *old* `self.selected`'s position to `self.trail` right
  before replacing it with the match. One point per real fetch, not per
  0.5 s dead-reckon tick: recording the interpolated positions would blow
  the trail length budget for no visual benefit over the real, ~30 s-apart
  fetched ones.
- `Renderer.draw_scene()`/`draw_planes()` take `trail` as one more
  argument alongside `selected`/`settings_open`/`view_cx` (same pattern as
  those three: `UI` owns it, `Renderer` just draws whatever it's handed),
  and draw a polyline through `to_screen()`-projected trail points plus the
  current position, before drawing the aircraft mark so the mark sits on
  top.

The original design's `Plane` class / `Feed` registry -- and the
`on_feed_update()` re-pointing-search simplification it would have
bought -- is **no longer motivated by the trail** and is a much weaker
proposal on its own remaining merits (today's `next(... for q in fresh
...)` search is already O(n) over ~30 planes at one fetch per 30 s --
cheap, not worth a cross-module dict-to-attribute rename to remove). Worth
revisiting only if something else independently wants persistent
per-aircraft identity later; not proposed as its own step here.

Open items to settle while implementing (not before):

- **Which pen/theme.** A dim, desaturated line reads as "history" against
  either backdrop; themed like everything else in §3, or one fixed muted
  colour -- worth a quick on-device look rather than guessing.
- **Both display modes, or just `"map"`?** Worth checking on-device rather
  than assuming either way.
- **A `TRAIL_LENGTH` setting** (0 disables it) fits the existing
  `settings.py` convention (`DRAW_BASEMAP`, `HIDE_ON_GROUND`, ...).

### The cold-start problem: backfilling on selection

Raised as a second point: an aircraft that's been in the air for a while
before you select it (including one already on screen when the radar
boots) shouldn't have to wait several empty minutes for its trail to
build up live -- ideally it shows its recent track immediately. Framed
generally rather than as a boot special-case, this is really "whenever a
*new* aircraft gets selected, try to backfill `self.trail` with recent
history before falling back to live accumulation" -- boot is just the
first moment that can happen, not a distinct code path. That maps cleanly
onto the same one-shot-fetch shape `routes.py` already uses: a
`request(hex)` that kicks a background fetch if not already
cached/pending, `get(hex)` that returns whatever's cached (or a pending
sentinel), called from `UI.set_selected()` exactly where `routes.request()`
already is.

**What that needs, and what I could and couldn't confirm:** adsb.lol
states API compatibility with the ADSBExchange v2 API ("a drop-in
replacement", per [adsblol/api's own README](https://github.com/adsblol/api)),
and ADSBExchange's v2 docs describe a documented "trace" format --
per-point time-offset, lat, lon, altitude, ground speed, track, vertical
rate — attached to *some* response shape. I could not confirm, from
adsb.lol's own docs, the actual endpoint that returns it for one aircraft
on demand:

- adsb.lol separately publishes a **bulk daily archive** (one gzip JSON
  file per aircraft per day, via GitHub releases -- see
  [their historical-data docs](https://www.adsb.lol/docs/open-data/historical/)).
  This is clearly the wrong mechanism for "backfill one aircraft's trail
  right now" -- it's a research/offline-analysis dataset, not a live query.
- Their interactive API reference lives at `api.adsb.lol/docs`, which
  renders as a JS Swagger UI I can't read via a plain fetch, so I couldn't
  read the actual route list from it.
- adsb.lol's own live "Globe" map (built on tar1090, the same frontend
  stack behind their infrastructure) visibly supports showing a clicked
  aircraft's recent track in the browser, which means the *data* and a
  *live* mechanism for it both exist somewhere in their stack -- I just
  couldn't pin down the request shape their own frontend uses for it
  without a real browser to inspect network requests.

**Next step, since you already offered to look into this:** open
`api.adsb.lol/docs` in a real browser (the Swagger UI should list every
route with example responses), or open `https://adsb.lol/?icao=<some hex
currently in the air>` and check the network tab for whatever request
fires when you click that aircraft on their globe map -- that's the same
request a backfill could reuse. Worth checking on the existing
`/v2/point/...` response too, on the off chance a `trace` field is already
present per-aircraft and simply unused by `feed.py` today (cheap to check:
log a full aircraft object once and look). If nothing pans out without
running a feeder, the feature degrades gracefully to "start the trail
empty and build it live from the moment of selection" -- exactly like
today, just with the backfill attempt as a bonus when it's available.

### An orthogonal, more speculative idea: phosphor echoes instead of the arrow

Raised alongside the above, explicitly as a "maybe" rather than a
request: real radar displays don't compute a synthetic heading/speed
arrow (`Renderer.draw_track_arrow()`, `"radar"` display mode only, not
drawn in `"map"` mode at all) -- they show the *actual* fading history of
a target's returns, and the **spacing between those echoes** is what
communicates speed, the way phosphor persistence works on a real
sweep-based radar tube. Replacing the arrow with a short trail of
fading/shrinking dots (every aircraft, not just the selected one --
this is a different, `"radar"`-mode-only rendering style question, not
the trail feature above) would need a *short* per-plane position memory
of its own -- maybe 3-5 points, nowhere near the full selected-aircraft
trail's length or lifetime. This is a distinct, later idea: a
`"radar"`-mode visual style change, not a prerequisite for or a
consequence of the selected-aircraft trail above. Not sized or ordered
here -- flagged so it doesn't get lost, not proposed for the current pass.

### Suggested order

1. Trail for the selected aircraft only: `UI.trail`, reset on selection
   change, appended once per fetch in `on_feed_update()`, threaded through
   to `Renderer` the same way `selected`/`settings_open`/`view_cx` already
   are. No `Feed`/`Plane` changes needed.
2. Trail rendering in `Renderer`, behind a `TRAIL_LENGTH` setting.
3. Backfill-on-selection, once the adsb.lol endpoint (or lack of one) is
   confirmed -- a small `trace.py` mirroring `routes.py`'s
   `request()`/`get()` shape, called from `UI.set_selected()`.
4. The phosphor-echo idea, and anything to do with a `Feed`-wide `Plane`
   registry, stay unscheduled -- revisit only if a concrete need for
   either comes up on its own.
