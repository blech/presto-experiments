# Selected-aircraft UI: trails, the panel, labels, echoes

Design decisions for the batch of UI questions that surfaced once the
position-history trail landed (`DATA_TRACE.md`, commit 413e708). The trail
made a latent tension obvious: **the selected-aircraft UI is maximalist** --
full-height panel, a callsign tag on every aircraft, a speed arrow on every
aircraft -- and a trail needs clear space and contrast that this leaves no
room for.

Worked through in discussion on 2026-09-08; the decisions below are agreed in
principle and are what the implementation plan argues from. Scope-mode
(`DISPLAY_MODE = "radar"`) is the focus; map-mode notes are called out where
they differ.

Evidence referenced throughout is in `prestoradar/images/`:
`radar-20260908-1142.png` (scope, dense, arrows + label pile-up),
`example_london_30km.png` (the worst label case -- Heathrow's due-east
approach stacks ~8 tags on the horizontal crosshair),
`example_details_3.png` (scope + full panel, eastern half hidden),
`map-20260904-1126.png` (map mode: no labels, no arrows, reads cleanly),
`example_menu.png` / the settings overlay (`_SPANEL`, ~284x168, bottom-right
-- the compact-corner-panel precedent).


## The through-line

The nine questions are one question. The answer is a **focused selected
state**: tapping an aircraft strips ambient clutter (labels, arrows, legend)
and shows its trail prominently; detail is disclosed progressively over
repeated taps, ending in a small panel that never covers the trail. Every
decision below is a piece of that.


## Decisions at a glance

| # | Question | Decision |
|---|---|---|
| 1 | Panel size/position | Shrink to a ~210x186 corner card; drop the view-shift entirely |
| 2 | SQWK / ICAO / TRACK / DIST | Cut SQWK, ICAO, TRACK from the default; keep DIST; park the rest in a "more" rung |
| 3 | Trail colour | Dim yellow keyed to `SELECT_PEN`, with a 3-4 step oldest->newest fade |
| 4 | Speed arrow | Keep for now; retire when echoes (5) prove out |
| 5 | Echoes | Prototype 3 real-fix echoes, shrink+dim, radar-only, non-selected; behind a setting |
| 6 | Extended ATC label | Not ambient -- it's the middle rung of the tap cycle (8) |
| 7 | Label overlap | Now: greedy cull + relevance sort + 1px dark box. Direction: label almost nothing ambiently |
| 8 | Tap cycle | Radar: 2 stages, data-block+trail -> corner card; map: 1 stage, straight to the card; tap-again cycles |
| 9 | Slide vs shrink | Shrink (same as 1) -- deletes the buggy view-shift machinery |
| + | List / "board" mode | Worth it, but as a later separate track with its own aesthetic, not a third spatial mode |


## 1. The panel: full-height, ~47% wide (`PANEL_X = 256`), hides the trail

**Pros of today's design:** every field fits; fixed layout needs no placement
logic; the view-shift (`UI._target_view_cx`) nudges the *selected* plane out
from behind the panel so it stays visible.

**Cons:** hides the entire eastern half of the scope
(`example_details_3.png`: DLH455 / SWA35G / ASA cluster all gone); a trail
usually runs *behind* the plane, toward where it came from, straight into
that hidden strip; the view-shift is a documented bug source (radar.py's
`_MAX_SHIFT` comment, the float->int `TypeError` history, the map-mode
`jpegdec` negative-offset failure).

**Decision:** a **compact corner card**, ~210x186, in the corner diagonally
opposite the selected blip (fallback: fixed top-right -- the legend owns
bottom-left, the hamburger bottom-right and is hidden while a plane is
selected). This is the shape the settings overlay (`_SPANEL`) already proves
on-device. It lets the view-shift be **deleted outright**
(`_target_view_cx`, `_MAX_SHIFT`, `_MIN_VIEW_CX`, the backdrop-rebuild-on-
select), which also removes the map-mode decode bug. Cost: fewer fields fit,
forcing decision 2. Also add **re-tap-to-dismiss** (a PLAN 2a open item)
independent of everything else.


## 2. Are SQWK / ICAO / TRACK / DIST useful?

- **SQWK** -- only 7500/7600/7700 (emergency) and 1200/7000 (VFR) carry
  meaning for a viewer, and emergencies already get their own red line. A
  bare "1431" is noise. **Cut** from the default; optionally show it only
  when it's a special code.
- **ICAO hex** -- a cross-referencing aid, but it is already the label when
  an aircraft broadcasts no callsign, and it is the least glanceable row on
  the panel. **Cut** to a "more" rung.
- **TRACK** ("140") -- redundant with the arrow / icon / echoes for
  orientation. **Cut** from the default; certainly cut if the arrow or
  echoes stay.
- **DIST** ("7nm E") -- the scope shows this geometrically, but the number
  usefully quantifies the eye's estimate for one cheap line. **Keep**
  (borderline).

**Decision:** default card = **callsign - operator - route - altitude -
VS rate - speed - distance**, plus the red emergency line when set. ICAO /
SQWK / TRACK move to a later "more" expansion. This is also what makes the
corner card (1) fit.


## 3. Trail colour collides with the vector map

Concrete cause: `TRACE_PEN = (70, 110, 130)` sits almost exactly on
`COAST_PEN = (60, 90, 120)`. The trail is a *foreground* element -- the thing
you just selected -- not background history, and it was mis-pened as the
latter.

**Options considered:** a brighter distinct hue (amber is taken by
"descent"); **match the selection** -- a dim yellow keyed to
`SELECT_PEN = (255, 235, 90)`, which ties the line to the ring around the
same target and is unused by the vstate pens or the coast; vstate-coloured
per segment (information-dense, but a 3-colour line fights the aircraft-dot
colours and the "one continuous path" read, and needs a mono-mode fallback);
a brightness ramp along the line (the phosphor look; ~4 pre-made pens).

**Decision:** **dim yellow tied to `SELECT_PEN`**, plus a cheap **3-4 step
fade from oldest to newest** so the line also reads directionally. Skip
vstate-colouring -- the VS field and the trail's own shape already convey
climb/descent, and a tricolour line competes with the blips. In **map mode**,
theme it to a dark, saturated colour over the light raster through the
existing `Renderer.theme()` hook.


## 4. Retire the speed arrow (`draw_track_arrow`)?

**Keep:** in bare-blip scope mode it is the only speed-and-direction cue;
length proportional to ground speed is instantly readable.

**Retire:** a major clutter source (`radar-20260908-1142.png` -- white arrows
cross nearly every label); map mode already dropped it for icons;
REFACTORING #9 explicitly wants echoes in its place; one less thing drawn
over the labels.

**Decision:** **keep as the default for now, but tie its retirement to
decision 5.** It is already suppressed for the selected aircraft when its
trail shows. If echoes land for all aircraft, retire the arrow at that point
-- echoes carry speed through their spacing, so the two together are pure
clutter. Interim tidy: drop the arrowhead barbs, leaving a line plus one
short tick.

Resolved 2026-09-08 (`adsb-radar-echoes`): echoes (decision 5) were tried and
did not work, so the arrow **stays** -- but **unscaled**. `draw_track_arrow`
is now a fixed ~18 px line along the track with no barbs (`_TICK_LEN`);
speed is dropped from the ambient view (it is on the data block / card on
tap). This keeps the scope aesthetic while removing the scaled-arrow clutter
that motivated retiring it. Still suppressed for the selected aircraft once
its trace shows.


## 5. Echoes -- past radar returns

Nearly free now: `Plane.trail` already accumulates one real `(e, n, alt)`
fix per fetch for *every* aircraft, not just the selected one.

**Show them?** Yes -- worth prototyping. It is the single biggest lift to the
"scope" identity and it is what lets the arrow go. Cost is ~3 extra
`display.circle()` per aircraft per frame.

**Decay.** `trail` holds one point per ~30 s fetch, so 3 echoes span ~90 s --
roughly 12 nm between dots at jet speed. Start with **3 echoes at fetch
cadence, shrinking (r 2 -> 1) and dimming**. If they read as detached dots
rather than a tail, escalate to a per-render-tick ring buffer (5-8
close-spaced points) -- but that is new per-frame state, so only if the
cheap version fails.

**Real vs extrapolated points.** **Real fixes only.** Extrapolated
(dead-reckoned) echoes would just be evenly spaced dots along the current
velocity vector -- which is the arrow, drawn worse. Echoes earn their place
precisely because they show real turns and acceleration.

**Selected aircraft.** Suppress its echoes -- it gets the full trail instead.

**Decision:** prototype **3 real-fix echoes, shrink + dim, radar mode only,
non-selected aircraft**, behind an `ECHOES` setting; retire the arrow in the
same change if it reads well on-device. **No echoes in map mode** -- the icon
already carries direction and dots over the raster read as noise.

**Tried and abandoned 2026-09-08** (commit `195f21b` on `adsb-radar-echoes`,
reverted). Built at the per-fetch cadence: three dots from `Plane.trail`
(last 3 fixes minus the current), r1/r2, two dim greens. On-device
(`radar-20260908-1915.png`) it did not read as a tail -- dim green at r≤2 on
the dark scope is near-invisible, and at 30 s spacing a fast aircraft's
three dots are scattered across the scope with nothing connecting them, so
they don't say "direction" or "speed". It also measurably hurt touch
latency (the extra per-plane `circle()` loop in the synchronous
`draw_scene`). The per-render-tick ring buffer is not the fix either: its
between-fetch samples are dead-reckoned, i.e. an arrow drawn as dots (this
section's own "Real vs extrapolated" point), and it adds *more* per-frame
cost. **Outcome:** kept the arrow, unscaled -- see decision 4. Revisit
echoes only if a genuinely higher-cadence real position source appears.


## 6. Extended ATC-style label (callsign + FL + ground speed + type)

**Pros:** answers "what is that and where is it going" without a tap;
authentic to the scope aesthetic; reduces how often the panel is needed.

**Cons:** a 3-line block is triple the footprint, which **detonates decision
7** -- `example_london_30km.png` with data blocks instead of single tags is
unreadable -- and it is only ever legible for a handful of aircraft at once.

**Decision:** **not an ambient label.** It becomes the **middle rung of the
tap cycle** (8): tapping an aircraft gives its blip an on-scope data block
(`callsign` / `FL gs` / `type`) beside it, with the trail, and no panel.
Only ever one on screen, so the overlap problem does not apply. The panel
(1) then carries only what will not fit three lines -- route, registration,
distance.


## 7. Overlapping labels: suppress vs shift

`example_london_30km.png` (8+ tags on the horizontal approach line) is the
worst case; SF's south-east quadrant (`radar-20260908-1142.png`) is nearly
as bad.

**Suppress (greedy cull):** keep a list of placed label rectangles; before
drawing each, skip it if its box hits one already placed. Sort first by
relevance (nearest to centre, or lowest altitude = closest to landing =
most interesting). O(n^2) with n ~= 30 -- trivial, stable frame-to-frame,
easy to verify (does any text overlap? no -> done). Loses labels in a dense
stream, but they were unreadable there anyway.

**Shift (alternate anchors / leader-line stack):** keeps more labels, but
leader lines are the most code, jitter as a cluster drifts between
dead-reckon frames, and still fail where all four anchor quadrants collide.

**Decision, two moves:**
1. **Now:** greedy cull + relevance sort + a **1px dark box behind each
   drawn label**, so the surviving label reads against the coastline instead
   of smearing into it.
2. **Direction:** move scope mode toward map mode's discipline -- the ambient
   view labels little or nothing; the selected aircraft gets the data block
   (6). The cull is the safety net; "label less" is the actual design.
   Echoes plus the blip keep unlabelled aircraft informative.


## 8. Tap cycle: data block -> corner card -> data block

Today a tap selects (ring + view-shift + panel + route/trace fetch); a tap
on empty space dismisses; a re-tap of the same plane does nothing.

**Decision: a short cycle** on repeated taps of the same aircraft. Radar mode
is 2 stages:

| Tap | Shows | Scope visible? |
|-----|-------|----------------|
| 1 | ring + trail + ATC data block (`callsign` / `FL gs` / `type`) | fully |
| 2 | + compact corner card (route, reg, dist, ...) | mostly (corner) |
| 3 | → back to stage 1 | |

Map mode is a single stage:

| Tap | Shows | Scope visible? |
|-----|-------|----------------|
| 1 | ring + callsign tag + corner card | mostly (corner) |

A tap on empty space, or on a different aircraft, dismisses or switches; a
re-tap of the selected plane in map mode is a no-op (stays on the card).

Revised on-device 2026-09-08: the original stage 1 was callsign-only, which
read as sparse while the `trace_recent` seed was still loading — folded into
the data block.

**Pros:** progressive disclosure -- a glance versus a deep look, the viewer's
choice; stages 1-2 never obscure the scope, so the trail is always visible;
the panel becomes opt-in and rare; discoverable, since you tap the thing you
already tapped.

**Cons:** a `UI._detail_level` field and extra branches in `handle_tap` /
`draw_scene`; a slightly-off second tap could miss the blip and dismiss --
mitigate with a larger hit radius for "advance the current selection" than
for "dismiss".

Ship stages 1-2 first (no panel rework needed); add stage 3 with the corner
card. This cycle is the backbone that unifies 1 (panel smaller and rarer),
2 (fewer fields), and 6 (data block as a rung).


## 9. Slide the background further, or shrink the panel?

**Slide more:** keeps the full-height panel, but the shift is the documented
bug source, it only relocates the hidden half rather than removing it, it
breaks the map raster past a certain offset, and it muddies "centre =
centre".

**Shrink to a corner:** deletes the whole view-shift mechanism and its class
of bugs; keeps the scope and the trail visible; proven by the settings
overlay. Costs the field triage of decision 2 and a little placement logic.

**Decision:** **shrink, drop the shift** -- the same conclusion as 1. It is
the larger simplification and it directly serves "do not hide the trail".


## A third mode: list / "board"?

Already floated in `TODOS.md` ("List of flights instead of map?"). The
appeal: the spatial modes are poor at surfacing **destination** -- you tap
one aircraft, wait for the route lookup, read it, dismiss, tap the next. A
list could show route for everything in range at once.

**Pros:**
- Genuinely glanceable for "what is up there and where is it going".
- Sidesteps every label-overlap problem (7) -- text layout in a list is
  solved.
- No projection, clipping, or basemap -- cheap and robust to render.
- Location-agnostic: valuable where the vector basemap is a blank field (an
  inland or urban centre -- PLAN #5 notes London 30 km shows nothing).
- Sorting is itself a feature: by altitude (who is landing), by distance
  (what is overhead), by route.
- Orthogonal to the trail work -- it shares almost no code, so it neither
  helps nor blocks the decisions above.

**Cons:**
- Discards the point of a radar: spatial awareness, relative geometry, and
  the trail itself (which is inherently spatial and cannot be shown in a
  list).
- The "dull" worry is real -- a 30-row table refreshing every 30 s has none
  of the motion or shape the scope and map have. This is a desk display; on
  a hobby project the look matters.
- **The headline feature fights the architecture.** Routes are fetched
  lazily per callsign on tap (`routes.request`), the cache
  (`routes._cache`) is unbounded (DATA_TODOS #3), and adsb.lol's public
  endpoints allow ~1 request/second. A list that wants routes for ~30
  visible aircraft needs batched, bounded, rate-limited route fetching
  first. Without routes it is just `dev/list_aircraft.py` on a screen --
  altitude / speed / type, which the scope already conveys through colour,
  icon, and tap.
- 480 px / ~16 px rows is ~24 visible; the feed has held 76 aircraft over
  SF, so it needs scrolling or paging, hence more touch handling.
- Another mode to carry through every theme / settings / toggle change --
  the radar/map split already taxes the codebase everywhere (`THEMES`,
  `MAP_VSTATE_PENS`, `showing_raster`, the toggle bugs in radar.py).

**Middle grounds:**
- **A "board" mode styled as an airport flight-information display** -- large
  type, ~8-10 rows, nearest-first or arrivals-first,
  `CALLSIGN  TYPE  ALT^v  ROUTE`, paging through the rest on a timer. This
  leans into the aesthetic and becomes its own display *personality* (a FIDS
  board) rather than "the scope with the fun removed". It legitimately
  cannot show trails -- that is fine, it is a different job.
- **A list *overlay*, not a mode** -- a button opens a scrollable
  callsign -> destination list for everything in range; tapping a row
  selects that aircraft back on the scope (with its trail). Gets the
  glanceable-destinations value without leaving the spatial view, and
  reuses the selection / tap-cycle machinery. Opening the list is also the
  signal that the user wants routes, which is when the batch fetch should
  fire.

**Decision:** there is value, but **not as a co-equal third spatial-less
mode.** Pursue it later, on its own track, as either the **board mode** or
the **list overlay** (decide then; the overlay is cheaper and composes with
the tap cycle). Do not entangle it with the trail / panel / echo / label
work -- that work is entirely spatial-mode and should land first.

### Data layer readiness for a list app

Built as a **distinct app** (its own entry script alongside `radar.py`, the
way `life.py` is separate), a list can reuse almost the whole data layer
unchanged:

| Module | Status for a list app |
|---|---|
| `feed.Feed` | Reusable. Construct with a `/v2/point/...` path, run `run()` with an `on_update` callback, or call `_fetch()` directly. No display coupling. |
| `plane.Plane` (+ `aircraft_types`, `airlines`) | Reusable, and already a superset of a rich row: `callsign`, `operator`, `type_description`, `alt`, `vstate`, `vrate`, `gs`, `dst`, `dir`, `squawk`, `emergency`. No new fields. |
| `geometry.compass` | `p.dst` is already nm-from-centre and `p.dir` the bearing, straight from the feed -- a list needs no projection at all. |
| `net`, `settings`, `netlog` | Reusable. |

`dev/list_aircraft.py` already builds a full textual listing from
`Feed._fetch()` with zero data-layer changes -- it is effectively the proof.
Only duplication: `RADIUS_NM` / the `/v2/point` path are derived in
`radar.py`, not `settings.py`, so a list app re-does the
`round(RADIUS_KM / 1.852)` one-liner (as `list_aircraft.py` does). Lift it
to a shared helper, or live with it.

**`routes.py` is the exception** -- a list's whole point is routes for many
aircraft at once, and `routes.request()` is built for one selected aircraft:

1. **Unbounded caches.** `_cache` / `_tries` grow one entry per callsign
   ever seen (DATA_TODOS #3). Tap-driven that is a slow leak; a list
   touching ~30 callsigns per feed cycle makes it acute. Needs an LRU cap
   (~50-100). Prerequisite either way.
2. **No batching or throttle.** `request()` fires
   `asyncio.create_task(_fetch(...))` per call with no concurrency cap and
   no spacing. In a 30-plane loop that is up to ~60 concurrent HTTPS GETs
   (adsbdb + the adsb.lol escalation), each holding TLS buffers on the
   Presto -- a memory spike, and it blows adsb.lol's ~1 req/s courtesy
   limit. Needs a work queue with a concurrency cap of 1-2 and a ~1/sec
   politeness delay, so routes fill in progressively over ~30 s.
3. **No priority.** Fetch the visible rows first; re-seed the queue from the
   currently visible slice as the list scrolls or the feed churns.
4. **Optional: a TTL on resolved routes** so a long-running board does not
   retain every callsign forever (a resolved `(o, d)` is safe to drop and
   re-fetch).

Shape: `routes.request_many(planes)` (or an internal queue that `request()`
feeds) plus the LRU cap -- ~40-60 lines, no change to existing callers.
DATA_TODOS #4 (carry-forward for a one-cycle drop) and #5 (snapshot sanity
guard) also matter more in a list, where a row flickering in and out reads
worse than a blip does -- worth doing alongside, not blocking.


## Suggested sequence

1. **Trail colour fix** (3) -- `SELECT_PEN`-keyed yellow + fade.
   **Landed `b166724`.**
2. **Ambient de-clutter** (7.1) -- greedy label cull + relevance sort + dark
   box. **Landed `7210afb`.**
3. **Tap cycle** (8, 6) -- `UI.detail_level`, the ATC data block, the
   mode-aware cycle length. **Landed `b2f5d20`** (revised on-device to
   2-stage radar / 1-stage map -- see decision 8).
4. **Compact corner card + drop the view-shift** (1, 9, 2) -- the big
   simplification. **Landed `86bf0d3` + `8dee07f`**, tuned in `eda93a3` /
   `ad431e3` / `006657d`.
5. **Echoes + retire the arrow** (5, 4) -- **echoes tried and abandoned**
   (`195f21b`, reverted; see decision 5). Outcome: the arrow **stays,
   unscaled** -- fixed ~18 px direction tick, no barbs, no speed scaling
   (`draw_track_arrow` / `_TICK_LEN`). Branch `adsb-radar-echoes`.
6. **Settings**: `TRAIL_LENGTH` (0 = off). **Landed `ac5d33d`.** (`ECHOES`
   was added and then removed with the echo revert.)
7. **Later, separate track:** bounded/batched route fetching, then the
   board mode or list overlay.


## Map mode notes

- Decisions **1** and **4** (drop the view-shift; corner card) apply and
  help most here -- in map mode the shift triggers the ~380 ms `jpegdec`
  re-decode and the negative-offset bug, so removing it is a clear win.
- Decision **3** (trail colour) applies, themed dark/saturated for the
  raster.
- Decisions **2**, **5** (echoes), **6**, **7** are largely N/A -- map mode
  already has no ambient labels and no arrows, and the icon carries
  direction.
- The tap cycle (**8**) collapses to a single stage in map mode: selecting a
  plane goes straight to the corner card, with a plain callsign tag on the
  icon as an identifier. Re-tapping the selected plane is a no-op.
