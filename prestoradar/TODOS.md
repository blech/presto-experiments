# TODOS

## Items to tackle

* Map improvements

Better basemap - no labels, better colours. Might require API key(s).

* Display improvements

Switch to left hand side panel (swapping if plane crosses under)

## Scratch notes

* Add '--run' option to radar_deploy.sh to call `mpremote run --no-follow ...`
* Add brightness control (slider?) to settings - particularly for map mode
* Add altitude colours to the selected-aircraft trail (the trail itself landed
  2026-09-08 -- see DATA_TRACE.md "Status"; drawn in one muted pen for now)
  - trail fade: undecided -- constant vs. slower fade / more segments; revisit alongside echoes
  - echoes: tried per-fetch (195f21b) and reverted -- didn't read as a tail, hurt touch;
    arrow kept but unscaled. See UI-TRAILS.md decision 5.
* Resolve incorrect routes
  - Include trail - see DATA_TRACE.md_
* Add 'speed' mode to alt/mono?
* Shadow for current altitude in map mode (requires new plane colour)
* Fix map not being available when starting in radar mode
* Disable LEDs option (settings.py only at first?)
* Retire `-raster` in favour of `-raster-fetch` (which can be renamed)
* List of flights instead of map?

### 2026-09-19

* ~~"on ground" incorrectly catches helicopter at FL0 _not_ at airport.
  Filter by location, too?~~ Done -- `geometry.ground_hidden()` (shared with
  `routes.py`'s existing, much looser 30 km route-plausibility check via the
  new `geometry.near_airport()` primitive) now also requires being within
  `radar.py`'s `GROUND_AIRPORT_KM` (3) of a displayed airport before trusting
  `Plane.on_ground`, falling back to today's behaviour when no basemap/
  airports are loaded. Applies to every aircraft, not just helicopters.
* Tap cycle: should the first tap on an aircraft show the panel (corner card)
  again? History: it began as tap 1 = select + trail + callsign only, tap 2 =
  more info, tap 3 = panel too, then loop; it is now 2 stages in radar mode
  (tap 1 = ring + trail + ATC data block, tap 2 = + corner card) and 1 in map
  mode (straight to the card). The question is whether radar mode should also
  go straight to the card on tap 1. Undecided -- see UI-TRAILS.md decision 8.
* Sporadic tap latency: taps are _sometimes_ slow to respond. Not yet
  measured. Suspects, all synchronous stretches that block the single event
  loop (touch is polled at 20 Hz, and each frame draw is ~100 ms):
  - `json.loads` + `gc.collect()` + `Plane.from_feed` over ~60 aircraft after
    each fetch. PLAN.md item 2 estimated 100-200 ms, but the timestamps in a
    2026-09-19 `radar_listen.py` session show **0.58-0.70 s** between
    `fetch: HTTP 200 N bytes` and `fetch: carried ...` on every one of ~9
    consecutive fetches (~every 33 s): one synchronous stretch during which
    nothing polls touch or redraws. A tap that lands in it is delayed by up
    to that long, and one shorter than it can be missed entirely.
  - TLS handshakes (~280 ms hitch, PLAN.md item 2) for the fetch, each trace
    backfill, and each route lookup -- a first tap on an aircraft calls
    `routes.request()`, which may start a handshake just as the redraw is
    wanted (compare REFACTORING.md #4's create_task-ordering finding)
  - trace inflate + parse (~90 ms each) and the `gc.collect()` calls around it
  Instrumentation added 2026-09-20 (watch with `radar_listen.py`):
  - `loop lag: N ms since the last touch poll` -- any gap over `LAG_LOG_MS`
    (200; normal is ~50 ms + a ~100 ms frame). Stamped when the stall ends,
    so it started N ms earlier; match it against the lines around it.
  - `touch: down at X Y poll gap G ms` -- the tap happened at most G ms
    before it was noticed.
  - `tap->drawn N ms` -- dispatch to the end of the frame showing it. Worst
    case from finger to screen is roughly G + N.
  - `fetch: parse N ms, gc M ms`, `fetch: decode ... N ms` -- splits the
    post-fetch stretch above into its parts.
  - `trace: ... sync N ms` -- the blocking part of one trace backfill.
  Still to try: A/B with `TRACE_SEED = 0`; tap the same aircraft repeatedly
  (first tap fires a route lookup, later ones don't); film finger + screen in
  slow-mo as ground truth. If the fetch stretch is the culprit, `json.loads`
  and `gc.collect()` can't be yielded from, but the per-aircraft decode loop
  can (`await asyncio.sleep_ms(0)` every N planes).
* ~~label on right overlaps direction indicator - add to label layout
  algorithm swapping side?~~ Done -- turned out to depend on heading, not
  screen side (`radar-20260919-1945/46.png`, ASA554): a direction tick
  drawn in the same up-and-right zone as the fixed `(x+8, y-8)` tag anchor
  is what collides, regardless of where the blip is on screen.
  `render._tag_anchor_dy()` flips the anchor below the blip when a tick is
  drawn heading 0-150 deg (initially tried 0-90 "NE only", but that missed
  LOT37 heading ~100 deg on-device -- `radar-20260919-2005.png`; the
  geometry (18px tick, 8px box offset, +-8px box height) actually collides
  to ~134 deg, so 150 adds a deliberate margin past that). Applies to both
  the ambient callsign
  tag and the tap-cycle's stage-1 ATC data block (which also reverses its
  3-line stacking direction so the top-to-bottom reading order stays the
  same either way). The ambient label cull (`_ambient_label_set`) uses the
  same flipped box so its overlap test matches what's actually drawn.
* refactor direct http fetch on Presto to requests-like signature for better code sharing between CPython and MicroPython

## UI questions

(Tackled 2026-09-10ish, mostly fixed)

I'd like to think through the UI more generally. There are a few things scattered through the TODO, PLAN, REFACTORING, and other Markdown files, and they're colliding with the trails. So:

- The panel. It's currently full height and almost half width. Showing it hides the plane's trail.
- The panel. Are SQWK, ICAO, and DIST useful? Or TRACK for that matter, given the arrow / plane icon?
- The trail. It's the same colour as the vector map, which makes it hard to spot.
- The speed arrow. Should it be retired?
- "Echoes" - radar points from the past. Should they be shown? If so. how fast should they decay? Should they only be shown for actual points, or for extrapolated ones?
- Additional plane info. Earlier there was a proposal to match ATC radar by extending the callsign to also show speed, altitude, and type - perhaps more? Is that still relevant?
- Fixing overlapping labels - either by suppressing (probably easier to check?) or by shifting them. Probably conflicts with the above.
- Should there be a tap cycle of label, detailed label, panel, label?
- Should the panel slide the background more? Or is it better to shrink the panel to under half the width & height and put it in the least obscuring corner?
I'd like pros and cons and a recommendation, please. Feel free to examine prestoradar/images/ - and concentrate on radar/scope mode, but if you can consider the map mode too, please do.