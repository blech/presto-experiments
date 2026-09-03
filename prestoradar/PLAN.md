# presto-radar — planned work

Running list of larger pieces not yet started. Smaller tweaks go straight in.

---

## 1. Shared UDP logging / telemetry module

**Why:** `radar.py` (`log()` + `radar_listen.py`) and the older `life.py`
(`send_start` / `send_generation` / `send_steady_state` + `life-listener.py` /
`life-listener-ncurses.py`) independently reinvent "spray visibility over UDP so
a laptop can watch a headless Presto". Two implementations of the same idea in
one repo — factor it out.

**Differences to reconcile:**

| | life family | radar family |
|---|---|---|
| addressing | multicast `239.255.255.250:32301` (listener joins group) | broadcast `255.255.255.255:47269` |
| payload | structured JSON events | plain text log lines |
| sender API | `send_<event>()` methods that know the schema | generic `log(*parts)` |
| consumer | raw dump + an ncurses dashboard that switches on `event` | tail / eyeball |

**Plan:**

- `lib/netlog.py` (device). `lib/` because MicroPython puts it on `sys.path`
  automatically — `import netlog`, no `sys.path.insert` dance.
  - `init(group="239.255.255.250", port=32301, ttl=1)` — multicast (the more
    correct transport; life already uses it).
  - `log(*parts)` — timestamped text line.
  - `emit(event, **fields)` — JSON record.
  - Both go over the same socket; a listener prints text raw and pretty-prints
    JSON.
  - Fix the `str` vs `bytes` bug from `life.py:70` (`sendto(json.dumps(...))`
    with no `.encode()`).
- `tools/udplisten.py` (desktop). Generic subscriber: join group, print text
  lines, pretty-print JSON. Replaces `radar_listen.py` and `life-listener.py`.
- `radar.py`: `log()` → `netlog.log()`; `deploy.sh` copies `lib/netlog.py` to
  `:lib/`.
- Optional / bigger: migrate `life.py` to `netlog.emit()` and move
  `life-listener-ncurses.py` into `tools/` as an app-specific renderer that
  imports the shared recv helper. Decide scope before starting.

---

## 2. Touch interactions

The Presto exposes `presto.touch` (`poll()`, `.x`, `.y`, `.state`) plus
`presto.touch_a` -> `(x, y, touched)`; the main loop already polls it and
discards the result.

**Does this force a move to asyncio? No.** `touch.poll()`
(`../presto/modules/py_frozen/touch.py`) is synchronous and non-blocking: it
checks the INT pin and only does the I2C read when a touch is present, with
built-in backoff so a wedged bus "would [not] stall the frame loop" — it is
designed to be called from exactly this kind of `while True` loop, and
`presto.update()` already calls it internally. State is a plain tuple you diff
frame-to-frame to spot a tap (rising edge). There is an interrupt mode
(`FT6236(enable_interrupt=True)`), but Presto's constructor doesn't use it and
an ISR is the opposite of needing asyncio anyway.

What *would* justify asyncio is unrelated to touch: the blocking
`requests.get()` (up to a 15 s timeout) freezes the animation during a fetch,
and a per-tap route lookup (adsbdb, see 2a) would add a second blocking call.
That is a networking concern; touch would just come along for free since it is
already non-blocking. The screenshot TCP server (item 4) adds only a little more
pressure -- a non-blocking `accept()` per frame plus one on-demand burst send --
not a recurring stall. Running tally: fetch = real, route lookup = would add to
it, touch = none, screenshot server = transient.

**`_thread` is not an option on the Presto** -- core 1 is permanently owned by
the display driver (`Presto.__init__` launches the ST7701 driver + backlight
loop there for the object's lifetime). So the only way to make blocking network
calls not stall the loop is cooperative: `asyncio`, or a non-blocking-socket
state machine polled from the main loop. TLS makes the latter hard, which is
why a first cut of the route lookup should just be synchronous with a
per-callsign cache (see 2a).

**Done: the fetch is now on asyncio.** A probe confirmed Presto firmware v2.0.0
does a non-blocking TLS handshake via `asyncio.open_connection(..., ssl=...)`
(worst animation hitch ~280 ms, and a 58 KB body downloaded over 4.4 s with the
loop still ticking). `radar.py`'s `main()` now runs two tasks -- `_render_loop`
(dead-reckon + draw every `ANIM_INTERVAL`) and `_fetch_loop` (`await`s a small
hand-rolled async HTTPS GET, `_http_get()`, then swaps the shared `_planes`).
`requests` is gone. The ~6 s per-fetch freeze is down to the ~280 ms handshake
plus a ~100-200 ms `json.loads` on the body. Still open: **keep-alive** (hold
the connection so the handshake is a one-time startup cost) and a streaming JSON
parse if that 100-200 ms grates. A per-tap route lookup (2a) can reuse
`_http_get`.

One caveat: `ANIM_INTERVAL = 0.5` means touch is only sampled ~2x/s, so a fast
tap can be missed. Either shorten the loop sleep and decouple poll rate from
redraw rate (poll ~50 ms, redraw ~500 ms), or accept that a deliberate tap is
held longer than 500 ms. Still no asyncio.

### 2a. Tap a plane → detail overlay

**Done (first cut).** A dedicated `_touch_loop` task polls `presto.touch` at
20 Hz (decoupled from the ~2 Hz redraw, so a quick tap isn't missed) and acts on
the rising edge. `handle_tap()` hit-tests the tap against `_last_drawn`
(`[(x, y, plane), ...]`, stashed by `draw_planes()`), nearest within
`HIT_RADIUS` = 26 px wins; a tap that hits nothing, or empty map with a panel
open, clears the selection. `fetch_planes()` now also keeps `reg`/`type`/`desc`/
`alt`/`vrate`/`squawk`/`emergency`/`dst`/`dir`/`hex` per plane. `_fetch_loop`
re-points `_selected` to the same `hex` in each fresh list (or clears it if the
aircraft dropped off).

`draw_panel()` is a right-hand sidebar (`PANEL_X = 330`) with callsign, reg +
type, desc, altitude, vertical rate, gs, track, squawk, distance + compass
bearing, and an `emergency` line in red when set. The selected aircraft gets a
`SELECT_PEN` ring (chosen over a leader line). Tapping empty map dismisses.

**Route (origin / destination):** `https://api.adsbdb.com/v0/callsign/<callsign>`
(no key, contact User-Agent, `hex`-only ids skipped). On select, `handle_tap`
fires `asyncio.create_task(_fetch_route(cs))` -- async now that item 2 landed, no
freeze -- which fills `_route_cache[cs]` with `(origin, dest)`, `None` (unknown),
or `""` while pending; the panel shows `route ...` / `LHR > JFK` / `route:
unknown` accordingly.

**Still open:** `_route_cache` is unbounded (one entry per callsign tapped, fine
in practice); the panel covers the eastern sector while open; no toggle-off by
re-tapping the same plane; airline name / `desc` wrapping could be nicer.

### 2b. Settings screen + on-device persistence

`settings.py` is currently edit-and-redeploy only. Add a touch-reachable
settings screen (long-press? corner button? tap the centre?) that edits the
tunables that make sense at runtime — `RADIUS_KM`, `HIDE_ON_GROUND`,
`DRAW_BASEMAP`, `ANIM_INTERVAL`, maybe `LEVEL_RATE_FPM`.

Persistence: `settings.py` is the per-location config (gitignored, copied from
`settings_example.py`); it holds the deploy-time values. Write runtime overrides
as JSON to `/prestoradar/settings_local.json` (also gitignored). On boot, load
`settings.py` then merge the JSON over it. Changing `RADIUS_KM` at runtime needs
the derived values
(`RADIUS_NM`, `RADAR_URL`, `PX_PER_KM`) recomputed and a basemap that matches.
`make_basemap.py` clips to `RADIUS_KM * 1.6` and records `RADIUS_KM` in the
`--if-stale` key, so a build-time radius change re-clips and rebuilds — but a
runtime change still leaves the deployed `basemap_data.py` clipped for the old
radius until it is regenerated and redeployed.

---

## 3. Aircraft-type icons (research)

**Category ladder done.** `fetch_planes` keeps `category`; map mode draws a
helicopter glyph (`_draw_rotor`) for `A7` and scales the fixed-wing icon by
`_CAT_SCALE` for `A1`..`A5` (light .. heavy). The engine-count / type-table part
below is still open.

Question: is there data to distinguish helicopter / private / twin light / twin
heavy / four-engine heavy?

**From the live feed, partially — two fields:**

- `category` — ADS-B emitter category. Gives a rough ladder for free:
  - `A7` = rotorcraft → **helicopter**
  - `A1` = light (<15.5k lb) → **private / light**
  - `A2` = small, `A3` = large, `A4` = high-vortex large (B757-ish),
    `A5` = heavy (>300k lb)
  - `A6` = high performance; `B1` glider, `B2` lighter-than-air, `B4`
    ultralight, `B6` UAV; `C*` = ground vehicles / obstacles
  - Not every aircraft sets it, and it's size/wake, not engine count.
- `t` — ICAO type designator (`B77W`, `A320`, `C172`, `EC35`, …). Exact type,
  but needs a lookup to get engine count / class.

**For engine count specifically** (twin vs quad) ship a small static table the
same way as the basemap: bake `t` → `(engine_count, engine_type, wtc)` from a
public ICAO type database (mictronics `indexedDB`, or the OpenSky aircraft
metadata CSV) into e.g. `prestoradar/aircraft_types.py`, filtered to types seen
locally to keep it small. `make_aircraft_types.py` alongside `make_basemap.py`.

**Mapping to the requested icons:**

| icon | rule |
|---|---|
| helicopter | `category == "A7"` (or `t` in rotorcraft set) |
| private / light | `category == "A1"`, or type table: 1 engine + light |
| twin light | type table: 2 engines + light/small wtc (King Air, Baron, …) |
| twin heavy | type table: 2 engines + medium/heavy (A320, B737, B777, B787, A350) |
| four-engine heavy | type table: 4 engines (B747, A340, A380, …) |

So: `category` alone gets helicopter + a size ladder immediately; the light/twin
distinctions need the baked `t` table. Icons themselves are small vector shapes
drawn in place of (or around) the current dot.

---

## 4. Screenshots off the device

**Done (TCP push):** flash writes deadlock this firmware, confirmed --
`screenshot.save()` gets through the RGB565->RGB888 expansion (~5 s) and then
hangs hard on `open()`. So `screenshot.py` runs a tiny TCP server instead
(`serve_init()` once, `serve_poll()` per frame, non-blocking `accept()`); on a
connection it sends `b"<w>x<h> <nbytes>\n"` then the raw RGB565 front buffer.
`screenshot_pull.py <presto-ip>` on the host reads that, expands to RGB888,
writes a BMP and shells out to `sips` for the PNG. `save()` is kept for a
firmware where flash writes work. Still open: fold `screenshot_pull.py` into a
`tools/` dir; optional multicast "live preview" (below).

Prior art in the sibling repos:

- **`../badgeware-presto`** (`lib/badgeware/_capture.py`) — local only, no
  network. `capture_next()` arms a flag that `update()` acts on right after the
  next render; it walks the framebuffer and writes a 24-bit BMP to flash
  (`/shot.bmp`), which you pull with `mpremote fs cp :shot.bmp .` and view via
  `sips -s format png`. Zero protocol, zero listener; not live / not remotely
  triggered. (Carries a `FLASH_WRITES_HANG` firmware caveat.)
- **`../compresto`** (`compresto/util.py:handle_screenshot_request` +
  `tools/take-screenshot.py`) — **TCP**, `asyncio.start_server` on port 11. On
  connect it writes `"{w}x{h}\n"` then the raw RGB565 `presto.buffer` and
  closes. The host tool reads the dims line, reads the rest as `array("H")`,
  `byteswap()`s, and hands it to ImageMagick/Wand as `rgb565` → PNG with an
  optional integer scale.

**UDP specifically:** possible but the wrong default. The radar runs
`full_res=True` → 480x480 RGB565 = **460,800 bytes**. A UDP datagram tops out
near 64 KB and has no reliability or ordering, so a framebuffer means a
home-grown chunk protocol (sequence numbers, missing-chunk detection, re-request
or FEC). compresto's TCP transfer gets all of that for free in ~15 lines.

**Recommendation:**

- Occasional / manual shots (docs, debugging): copy badgeware's flash-BMP
  approach — a `screenshot()` that writes `/shot.bmp` from the framebuffer,
  called from the touch handler or over the wire with `mpremote exec`. Least
  code, no listener, and the repo already uses `sips` for BMP→PNG (the basemap
  preview).
- Remote, on-demand: copy compresto's TCP puller. The radar loop is a plain
  `while True` + `time.sleep()`, not asyncio, so poll a non-blocking listening
  socket once per frame (`setblocking(False)`, accept-if-waiting) rather than
  restructuring to asyncio.
- UDP only earns its keep if the goal is a **multicast "live preview"** to
  several watchers at once — then send a *downscaled* frame (240x240 = 115 KB,
  or 120x120 = 28 KB) as N numbered chunks, best-effort, a dropped chunk just
  skips that frame. Could ride the same multicast group as the shared `netlog`
  (item 1).

---

## 5. More basemap layers + auto airports

Coastline alone is useless for an inland centre -- London 50 km shows a blank
field (see `example-london.png`). Add layers, and stop hardcoding airports.

### Layer model

Generalise `basemap_data.py` from fixed `COASTLINE` / `LAKES` / `AIRPORTS` to a
small set of line layers and mark (point + label) layers, e.g.

    LINES = {"coast": [...], "highways": [...]}       # closed/open rings, km
    MARKS = {"airports": [("LHR", e, n), ...], "cities": [...]}

`settings.py` gets a toggle, which feeds `--if-stale`'s param hash so flipping a
layer forces a rebuild:

    BASEMAP_LAYERS = ("coast", "highways", "airports")   # "cities" optional

`radar.py`'s `draw_basemap()` iterates whatever's present: a pen per line layer,
a dot + label per mark layer. `make_basemap.py` grows a `--layers` arg mirroring
the setting, and one clip/simplify/project pass per enabled source.

### Data sources

- **highways** — Natural Earth `ne_10m_roads` (public domain, one download).
  Sparse but has the UK motorway network (M25 ring + radials round Heathrow =
  instant orientation). `type` field distinguishes Major/Secondary. Same
  processing shape as GSHHG: clip box -> simplify -> project -> polylines.
  OSM via Overpass (`way["highway"~"motorway|trunk"](bbox)`) is the
  higher-detail alternative, ODbL, needs heavier simplification.
- **cities / city centres** — Natural Earth `ne_10m_populated_places` (public
  domain, points with `name` + `POP_MAX` + `SCALERANK` to filter by size), or
  just a hand-maintained `LANDMARKS` table for a few well-known points. Draw as
  a distinct marker (square/diamond) from airports.
- **airports (auto)** — *Done.* OurAirports `airports.csv`
  (`https://davidmegginson.github.io/ourairports-data/airports.csv`, public
  domain, ~13 MB, stdlib `csv`), cached in `~/.cache/ourairports/` alongside the
  GSHHG cache. The size tier is `settings.BASEMAP_AIRPORTS`
  (`large|medium|small|none`, default `medium` = large + medium airports); the
  `--airports <tier>` flag overrides it for a one-off build. Filters by `type`
  within the clip bbox, labels with `iata_code` else `ident`. The hardcoded
  `AIRPORTS` constant is gone; `--if-stale` records the tier string, not a list,
  and reads it from `settings.py` so `deploy.sh`'s flagless run keeps it. Output
  shape (`AIRPORTS = [(label, e, n), ...]`) is unchanged, so `radar.py` didn't
  move. Still open: an override for specific fields regardless of `type`.

### Dependency decision

GSHHG was hand-parsed with `struct` to stay stdlib-only. Natural Earth ships as
ESRI Shapefile (+ dBASE `.dbf` for attributes). **Decided: add `pyshp`** (pure
Python, small) rather than hand-roll a `.shp`/`.dbf` reader -- the DIY version
would be ~90 lines and only exists to avoid one lightweight dependency.
`pyproject.toml` gets `pyshp` when this work starts. OurAirports needs nothing
extra -- it's CSV.

### One pass?

Yes -- highways, cities and auto-airports all have the same shape (add source,
clip, project, emit, draw) and all benefit from the layer-model refactor, so
they're one coherent change rather than three. Airports was the smallest slice
(no new parser) and landed first, ahead of the layer-model refactor -- it reused
the existing `AIRPORTS` output shape. Highways and cities still want the refactor.

---

## 6. Callsign label overlap

**Why:** `draw_scene()` draws every callsign at a fixed `(x + 8, y - 8)` from
its dot, scale-2 (~8 px/char). Aircraft strung along a common corridor -- a
runway approach -- sit close together with labels at the same height, so the
text overwrites itself into an unreadable smear. Heathrow's final approach is
almost due east/west, so an inbound stream lands right on the horizontal
crosshair just east of centre; see `example_london.png` (also the departure
cluster to the upper right). London is the worst case because of the traffic
volume, but any busy single-runway field does it.

**Why it's hard:** general map-label placement is NP-hard, and this runs on a
~2 fps MicroPython loop redrawing from scratch each frame with positions that
drift between fetches (dead reckoning), so any solution has to be cheap and
stable frame-to-frame or labels will jitter and pop.

**Options, cheapest first:**

- **Greedy collision cull.** Keep a list of placed label rects for the frame;
  before drawing each label, test its box against the list and skip it on a
  hit. O(n^2) but n is ~30. Draw order decides who wins, so sort first by
  what matters -- distance from centre, or lowest altitude (closest to
  landing) -- so the most relevant labels get placed. Dropped aircraft still
  show as dots. Simplest real improvement.
- **Try alternate anchors.** On a collision, try the other three quadrants
  (left/below/above) before giving up. A few lines on top of the cull; helps
  sparse clashes, does nothing for a dense line where all four slots collide.
- **1 px background box behind each label.** Doesn't deconflict, but makes the
  topmost label in a pile readable instead of a smear. Cosmetic, pairs with
  the cull.
- **Label only what's interesting.** Only the tapped/selected plane (ties into
  2a), or the nearest N, or none until zoomed/selected. Sidesteps layout
  entirely; probably the right long-term answer.
- **Leader lines + vertical stack.** Detect a cluster, fan its labels out
  vertically with short lines back to the dots. The "proper" fix and by far
  the most code; hard to keep stable as the cluster moves.

Lean: greedy cull with a relevance sort, optionally the background box, and
fold in per-plane labelling if 2a lands.

---

## 7. Aircraft icons instead of dot + arrow + label

**Why:** the label overlap in item 6 is a symptom -- the ambient view tries to
show a callsign per aircraft in a space that can't hold them. FlightRadar24's
answer is a directional aircraft icon: orientation carries the track, shape can
later carry the type (item 3), and callsigns move to tap/selection only. Keeps
the vstate colour, kills the text pile-up.

**Done (first cut).** `settings.DISPLAY_MODE` selects `"radar"` (the scope look
-- blip + track arrow + callsign, unchanged) or `"map"` (a plane icon along the
track, no callsign). `radar.py`:

- `_ICON_TRIS` is a top-down airliner (fuselage quad + nose + swept wing per
  side + tailplane per side, 7 convex triangles, ~14 px long), local coords with
  the nose at +Y. `_icon_pass()` rotates every vertex in Python
  (`dx = sin(a)`, `dy = -cos(a)`, the `draw_track_arrow()` convention) and fills
  with `display.triangle()`. Every wing/tail root overlaps the fuselage quad so
  the shape stays connected at any rotation.
- Filled in `VSTATE_PENS[vstate]`. `heading is None` or `gs` ~ 0 falls back to
  the plain blip.
- Both modes now draw nearest-to-centre last (`sort` by e^2 + n^2) so the
  closest icon/tag sits on top.
- **No halo.** A per-icon `BG_COLOR` outline pass was tried; on a dense in-trail
  stream (the case this is for) it chops the icons into a centipede. Body-only
  with nearest-on-top reads as an overlapping line of aircraft, which is what
  FR24 does at low zoom. Individual identification is what item 2a is for.

**Still open:**

- **Callsign on selection.** With 2a, show the tag for the tapped plane only.
- **Type-driven icon.** Emitter-`category` size ladder + `A7` rotor glyph are
  done (item 3). Still want the twin/quad distinction from a baked `t` table.
- **Speed cue.** Optional thin line off the nose, length proportional to `gs`.
- **Tuning.** `_ICON_TRIS` coords and the on-ground fallback are easy to adjust;
  the shape is spiky at 45-degree headings and merges badly below ~1 px/plane
  spacing.

---

## 8. Raster basemap / a map mode

**Why:** the coastline vector basemap is thin for an inland or urban centre
(item 5 adds highways + cities to help), and the icon direction of item 7 pushes
the whole display toward a FlightRadar24-style map rather than a scope. A
pre-rendered raster map sidesteps both: arbitrary richness -- roads, water,
parks, place names -- baked in, drawn as a static background at essentially zero
per-frame cost. Worth having as **an alternative mode**, not a replacement: the
green-rings scope look still suits sparse airspace and the retro feel.

**Build side.** A `make_basemap_raster.py` (or a `--raster` mode on the existing
tool) renders a square image in the same kilometres-east/north frame radar.py
projects into, keyed on centre / `RADIUS_KM` / `--if-stale` like the vector
build. Source options: Natural Earth raster (public domain, coarse), a local
tile render, or a one-off static-map export (mind tile-usage terms +
attribution). Output as **`PEN_P8` palette** (1 byte/px, ~230 KB at 480x480, vs
460 KB for RGB565; 256 colours is plenty for a map) plus its palette, or plain
RGB565.

**Device side -- the blit is the crux.** PicoGraphics has no "draw this
bytearray as the background". Two routes:

- **240x240, non-`full_res` -> 2 layers.** Put the raster on layer 0 once, draw
  only planes (and optional rings) on layer 1 each frame. Cleanest; half the
  resolution. The right first prototype.
- **`full_res` `PEN_P8`, single layer + a resident copy.** Hold a ~230 KB
  `bytearray` of the rendered background; each frame
  `memoryview(fb)[:] = background` then draw planes on top. Needs
  `direct_to_fb=True` to expose the framebuffer as writable (see item 4's
  notes). ~230 KB on top of the framebuffer -- check `gc.mem_free()` against the
  TLS + fetch-body pressure first.

**RAM, not CPU, is the blocker.** TLS, the response body and the framebuffer
already stress the Pico; a second full-frame buffer may not fit at `full_res`,
which is why the 240x240 `PEN_P8` route is the one to try first.

**As a mode.** Fold into the existing `settings.DISPLAY_MODE` (item 7) rather
than a separate `BASEMAP_MODE`: `"map"` gains the raster background (blit the
image, skip the green grid), `"radar"` keeps the vector scope. `draw_scene()`
already branches on `DISPLAY_MODE` for aircraft; this extends that branch to the
backdrop. Items 6 and 7 already ship the no-labels + icon half of the mode; this
is the backdrop half.

**Lean:** prototype `raster` at 240x240 `PEN_P8` with two layers and Natural
Earth raster; decide from that whether the resolution and RAM headroom justify
the `full_res` `direct_to_fb` copy.
