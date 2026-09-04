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
underway, module by module -- `net.py` and `geometry.py` are out, `net.py`
verified, `geometry.py` pending; the rest (`routes.py`, `feed.py`,
`backdrop.py`, `render.py`, `ui.py`) are still proposal. §4 (touch
latency) is still proposal only.

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
- **`feed.py`** — parsing (`fetch_planes()`'s per-aircraft dict-building,
  474-556) and the plane list's lifecycle (`_planes`/`_fetch_count`/
  `_fetch_ok`, 936-1014). Every field the feed produces is *data*: keep
  `on_ground` as a stored bool instead of filtering it away at parse time,
  and apply `HIDE_ON_GROUND` as a view filter at draw/hit-test time
  instead (§5).
- **`routes.py`** — `_fetch_route`/`_route_cache` (613-651), today's single
  adsbdb GET per callsign. This one is worth calling out on its own: the
  route lookup is a known-naive placeholder (a single, unverified adsbdb
  hit — no cross-check, no fallback if adsbdb's callsign→route mapping is
  stale or just wrong for a shared/renumbered flight number), and a
  smarter replacement is planned. So `routes.py` should expose exactly one
  entry point the rest of the app calls — e.g. `resolve_route(callsign) ->
  (origin, dest) | None | "pending"` — with the cache, the `_is_hex_id`
  skip, and whatever fetch/scoring logic sits behind it entirely private
  to the module. `ui.py`/`render.py` only ever call `resolve_route()` and
  format whatever tuple comes back (`_fmt_route`, 819-827 stays put in
  render/ui, since it's presentation, not resolution). That means swapping
  the naive single-GET for a more sophisticated algorithm — multiple
  sources, scoring, whatever the design from your other conversation turns
  out to need — is a change entirely inside `routes.py`: same call
  signature, same cache shape, zero ripple into the panel drawing or touch
  handling that currently sit next to it in radar.py. Also the natural
  place to cap the cache (see §6).
- **`render.py`** — every `draw_*` function, `plane_pen`, the icon/rotor
  drawing helpers, and the pens themselves.
- **`backdrop.py`** — the basemap subsystem: `build_basemap_cache`,
  `draw_basemap`, `load_raster_basemap`, `_draw_map_backdrop` (269-401).
  This is already fairly self-contained (only talks to `basemap_data` and
  `jpegdec`), it just currently reaches into radar.py's `_view_cx`/
  `_map_layers`/`_showing_raster` globals directly.
- **`ui.py`** — touch + selection + settings overlay: `handle_tap`,
  `_set_selected`, `_settings_tap`, `_toggle_setting`, `_target_view_cx`
  (585-714).
- **`radar.py`** stays, but shrinks to the entry point: build the pieces
  above, wire touch → UI → (feed filter / backdrop shift), and run
  `asyncio.gather(render_loop, fetch_loop, touch_loop)`. Keep calling the
  entry function unconditionally at module scope, no `if __name__`
  guard — MicroPython's launcher runs the file directly and a guard means
  it silently does nothing.

Net effect: `feed.py` can be imported and its parsing tested against a
canned adsb.lol JSON fixture on a laptop with plain CPython — currently the
only way to exercise `fetch_planes()`'s parsing logic at all is flash,
reset, and read the serial log.

---

## 2. Encapsulate in objects — and why settings has to move first

**Done so far: just the `Settings` slice below**, added in place in
`radar.py` (still one file). The rest of this section -- `PlaneFeed`,
`RouteCache`, `Backdrop`, `Renderer`, `UI` as actual classes in their own
modules -- is still proposal, landing with §1's file split.

The natural boundaries from §1 map onto a small number of classes rather
than a pile of same-named functions in different files:

```
Settings        # copied from settings.py at boot; the one mutable source
                # of truth for DISPLAY_MODE / COLOUR_MODE / HIDE_ON_GROUND
PlaneFeed       # owns _planes / _fetch_count / _fetch_ok; fetch_loop()
RouteCache      # owns _route_cache; fetch_route()
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
explicit field list), pass *that instance* to whatever needs it
(`PlaneFeed(settings)`, `Renderer(settings)`, `UI(settings)`), and have
`_toggle_setting()` mutate `settings.display_mode` etc. Every reader then
sees the live value because they're all holding the same object, not a
`from module import *` snapshot. `settings.py` itself doesn't need to
change shape — it's still the plain, gitignored, per-location constants
file `deploy.sh` copies down; only how `radar.py` consumes it changes.

This also directly enables §5 (live ground-toggle): once `PlaneFeed` holds
the `Settings` instance instead of a name copied at import time, its filter
can just read `self.settings.hide_on_ground` fresh on every draw.

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

- **`_route_cache` is unbounded** (already noted as open in PLAN item 2a) —
  one entry per distinct callsign ever tapped, for the life of the process.
  Harmless over a normal session, but worth a simple cap (e.g. drop the
  oldest entry past N) once it has its own module (§1's `routes.py`)
  rather than letting it grow forever on a display left running for days.
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
  feed.py        # PlaneFeed: fetch, parse, on_ground as data not a filter
  routes.py       # RouteCache: adsbdb lookup, capped
  backdrop.py    # Backdrop: vector cache + raster layer-0 loading
  render.py      # Renderer: pens, theme table, all draw_* 
  ui.py          # UI: selection, tap handling, settings overlay
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
4. §1's file split — the big one; do it after 1-3 so there's less state to
   carry across the split, and each new module can be dropped in with the
   old monolith still working as a fallback until the split module is
   confirmed on-device.
5. §4's redraw-on-tap and debounce tuning — do last, since it's the one
   change that needs on-device feel rather than a log line to judge.
