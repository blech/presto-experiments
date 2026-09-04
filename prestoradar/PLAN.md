# presto-radar — planned work

Running list of larger pieces not yet started. Smaller tweaks go straight in.

---

## 1. Shared UDP logging / telemetry module

**Radar side done.** `lib/netlog.py` (multicast `239.255.255.250:32301`,
`init` / `log` / `emit` / `close`, echoes to serial, never raises) and
`lib/screenshot.py` were brought across from the Life refactor. `radar.py` now
does `import netlog` + `from netlog import log`; its own broadcast socket and
`LOG_UDP_ADDR` are gone. `radar_listen.py` joins the group (importing `GROUP`
from `netlog`) and pretty-passes JSON. `radar_deploy.sh` makes `:lib/` and
copies `netlog.py` + `screenshot.py` there (and removes the stale
`:prestoradar/screenshot.py` that would otherwise shadow it).
`dev/udp.py` was the throwaway spike -- superseded, can be deleted.

**Still open:** migrate `life.py` to `netlog.emit()` (sender side; the receiver
choice below is settled).

**Receivers stay per-app -- decided.** The earlier idea of one
`tools/udplisten.py` replacing every listener is dropped: they present the same
stream very differently and merging them would just add a mode switch.
`radar_listen.py` tails arbitrary timestamped text; life's `life-listener.py`
pretty-prints JSONL; `life-listener-ncurses.py` is a live dashboard keyed on
`event`. They already share the wire format and the multicast group via
`netlog`; that's the right amount of sharing. If a generic "join + dump"
helper is ever wanted it can be a tiny shared function, not a replacement.

**Why (original):** `radar.py` (`log()` + `radar_listen.py`) and the older
`life.py` (`send_start` / `send_generation` / `send_steady_state` +
`life-listener.py` / `life-listener-ncurses.py`) independently reinvent "spray
visibility over UDP so a laptop can watch a headless Presto". Two implementations
of the same idea in one repo — factor it out.

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
- ~~`tools/udplisten.py` (desktop). Generic subscriber replacing every
  listener.~~ Dropped -- see "Receivers stay per-app" above.
- `radar.py`: `log()` → `netlog.log()`; `deploy.sh` copies `lib/netlog.py` to
  `:lib/`. **Done.**
- Optional / bigger: migrate `life.py` to `netlog.emit()`. Its receivers keep
  their own presentation.

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

**Phase 1 done (in-memory).** A hamburger button bottom-right opens a small
overlay (`draw_settings_panel` / `_settings_tap`) with tap-to-cycle rows for
`DISPLAY_MODE`, `COLOUR_MODE` and `HIDE_ON_GROUND` — display-only tunables that
just change the next `draw_scene` (ground filter applies on the next fetch).
Reuses the item 2a touch plumbing; mutually exclusive with the detail panel.
Changes are lost on reboot.

**Phase 2 (persistence) is blocked.** `settings.py` is still edit-and-redeploy
for anything that must survive a reboot. The plan was a JSON overrides file, but
flash writes deadlock this firmware (the reason item 4 uses a TCP screenshot
server, not a flash BMP) -- so persistence needs another route: host edits
`settings_local.json` and `deploy.sh` pushes it, or accept reboot = back to
`settings.py`. `RADIUS_KM` also can't move at runtime (needs `RADAR_URL` /
`PX_PER_KM` recompute *and* a matching basemap, which can't regenerate on
device).

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
- **`../compresto`** ([kmohrf/compresto](https://git.hack-hro.de/kmohrf/compresto);
  `compresto/util.py:handle_screenshot_request` + `tools/take-screenshot.py`) —
  **TCP**, `asyncio.start_server` on port 11. On
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

**Done (first cut).** `dev/raster_basemap_probe.py` measured three ways to get a
backdrop under the aircraft; `make_basemap.py --raster` and `radar.py` ship a
fourth assembled from what they showed (route B's layer split, kept at 480x480).

**The probe (`dev/raster_basemap_probe.py`, three routes, one Presto init each):**

| route | resolution | backdrop / frame | frame total | RAM over baseline | blocks the loop? |
|---|---|---|---|---|---|
| A `full_res` + per-frame `jpegdec` decode | 480x480 | 380 ms decode | 407 ms (~2.5 fps) | none (in place) | **yes, 380 ms/frame** |
| B 240x240, backdrop on layer 0 once, planes on layer 1 | 240x240 | ~0 | fast | ~115 KB layer | no |
| C `full_res` + `direct_to_fb`, resident RGB565 blit | 480x480 | 24 ms `buffer[:] = bg` | 25 ms | 460 KB copy | no |

**The "RAM, not CPU, is the blocker" premise was wrong for this firmware:**
`gc.mem_free()` is ~8 MB at boot (the MicroPython heap is in PSRAM), so a
full-res second buffer costs 460 KB out of 8 MB and the ~60 KB JSON body + TLS
buffers are noise. That killed the reason route B dropped to 240x240.

**Route C looked best on the probe but strobes in the real radar.** With
`direct_to_fb` there is one buffer and no atomic present: `draw_scene()`'s
whole-frame `presto.buffer[:] = _raster_bg` restore erases every icon and takes
~24 ms, during which the scanout DMA shows the icon-free backdrop. Each moving
icon is then absent for ~5% of every 500 ms frame -- a visible 2 Hz flicker.
Route A's per-frame decode has the same "absent" window, 15x worse.

**Shipped: route B's structure at full res.** `jpegdec` decodes `basemap.jpg`
onto PicoGraphics **layer 0** once at boot; `draw_scene()` clears **layer 1**
and draws the aircraft there each frame; `presto.update()` does its beam-raced
composite (`*dst = layer1 ? layer1 : layer0`, `st7701.cpp`) -- tear-free, no
per-frame backdrop cost, and 480x480 is kept because nothing forces `layers=1`
at `full_res`, only the `Presto()` default does (overridden with `layers=2`).
Cost is ~1.4 MB more PicoGraphics buffer (two full-res layers) -- fine against
8 MB. **Confirmed on-device:** `full_res` + `layers=2` is accepted and
composites cleanly -- the overlay (legend, selected-plane icon, settings
button) all sit correctly over the backdrop with no corruption, tearing, or
flicker. The 240x240 fallback was not needed.

**What shipped:**

- **Build, fetch (no key):** `make_basemap.py --raster-fetch` GETs
  `basemap.jpg` from an ArcGIS World MapServer's public `export` endpoint
  (`--raster-style topo/street/imagery`; `topo` default), standard library only.
  First cut requested the radar frame's bbox with `bboxSR=imageSR=4326` (plain
  lat/lon), expecting ArcGIS to render it as a linear equirectangular raster
  matching radar.py's own projection. **On device it came out visibly stretched
  vertically** -- these services are Web-Mercator-tiled, and reprojecting the
  cache to 4326 on the fly does its own aspect handling in the native SR,
  which didn't line up with a bbox only made square via the degrees-times-
  cos(lat) trick. Fixed by fetching in the service's **native SR (3857)**
  instead (`frame_bbox_3857()`): Web Mercator is locally conformal, so a bbox
  built by applying one local scale factor (`sec(CENTER_LAT)`) to the target
  ground distance in both directions is square in Mercator metres too, and
  over a radar-sized extent (tens of km) that factor barely varies across the
  bbox -- no server-side reprojection, no distortion, confirmed by eye against
  the pre-fix fetch. Natural Earth raster was considered and rejected: its
  finest tier is ~2 km/px, far too coarse for a 60-190 km radar frame
  (world/continent scale, not this zoom).
- **Build, bring-your-own:** `make_basemap.py --raster <image>` (needs Pillow,
  the `raster` optional-dependency extra) instead conforms an image you already
  have -- resize so the short side is 480, centre-crop to 480x480, save
  baseline JPEG. Unlike `--raster-fetch` it does **not** know the image's
  geographic bounds, so alignment is on you: the source must already cover the
  radar frame in the same flat projection.
  Both write `prestoradar/basemap.jpg` + a `basemap.jpg.json` sidecar of their
  params for `--if-stale`, and short-circuit the whole GSHHG/airport path.
- **Deploy:** `radar_deploy.sh` copies `basemap.jpg` to `:prestoradar/` if
  present. `basemap.jpg` + sidecar are gitignored like `basemap_data.py`.
- **Device:** `radar.py` boots `Presto(..., layers=2 if _RASTER_OK else 1)`
  where `_RASTER_OK = DISPLAY_MODE == "map" and DRAW_BASEMAP`.
  `load_raster_basemap()` decodes onto layer 0 via `_draw_map_backdrop()` (or
  draws the vector grid there if `basemap.jpg` is missing); `draw_scene()`
  branches on `_map_layers` to clear+draw on layer 1.
- **Panel shift, both modes.** `_set_selected()` shifts `_view_cx` the same way
  in map mode as in scope mode -- `_draw_map_backdrop()` re-decodes the JPEG
  onto layer 0 at the new x offset (`_view_cx - WIDTH/2`) so the raster stays
  registered with the aircraft instead of drifting under them. One ~380 ms
  decode, paid only on selection change like the vector cache rebuild it
  mirrors, not per frame. **Confirmed on-device:** `jpegdec.decode()` clips a
  negative x cleanly on this firmware.
- **Panel shift, adaptive.** The first cut shifted by a flat `_PANEL_SHIFT`
  whenever anything was selected -- correct only for a plane that started near
  centre. A plane already clear of the panel got shifted anyway (risking the
  left edge); a plane already under where the panel lands often stayed there
  after only a fixed 112 px move. `_target_view_cx(p)` replaces it: shift left
  only as far as needed to bring `p` to `_PANEL_MARGIN` (20 px) clear of
  `PANEL_X` -- zero shift if it's already clear.

  Clamping this to the raster's theoretical limit (`PANEL_X - WIDTH`, so the
  map-mode backdrop -- one 480 px `jpegdec` decode starting at the shift --
  still reaches `PANEL_X`) instead made the backdrop **disappear** for a plane
  far enough right to need close to that much shift, rather than just clip.
  The magnitude involved (up to -224) was larger than the old flat shift ever
  asked for (-112, confirmed working); something in `jpegdec.decode()`'s
  negative-x handling likely breaks down somewhere in between, not yet pinned
  down. `_MAX_SHIFT` now clamps to that smaller, previously-working magnitude
  instead -- **mitigates, not confirmed fixed**: a plane needing more shift
  than that still lands partly under the panel (the lesser failure), but the
  backdrop itself should never vanish. Watch the device log
  (`_draw_map_backdrop`'s `offset_x` print) if it does -- narrowing where it
  actually breaks would let `_MAX_SHIFT` come back up.

  A second, distinct bug turned up alongside it: `_target_view_cx()` did
  float arithmetic (`p["e"] * PX_PER_KM`) and could return a `float` whenever
  an actual shift was needed. `_view_cx` then fed uncast into
  `jpegdec.decode()`'s `offset_x` and, via the vector-grid fallback,
  `display.circle()`/`line()` -- both want ints, and MicroPython's C
  extensions raise rather than coerce, surfacing as
  `TypeError("can't convert float to int")` from the touch handler on
  selection. The old flat-shift formula was pure integer arithmetic so this
  only appeared with the adaptive rewrite; fixed with a single `int(...)` on
  `_target_view_cx()`'s return.
- **Legend/status contrast in map mode.** `TEXT_COLOR` (pale green, tuned for
  the dark scope background) and `VSTATE_PENS["level"]` (near-white) both
  washed out over light map colours. `MAP_TEXT_PEN` (near-black -- deliberately
  not pure black, which is `TRANSPARENT_PEN`'s value and would show layer 0
  through the mark instead of drawing over it) is used for the legend labels,
  the status line, and (via `MAP_VSTATE_PENS`, which reuses `VSTATE_PENS` for
  climb/descent) the "level" dot -- in the legend *and* on the aircraft itself,
  via `plane_pen()`, so the swatch keeps matching what's drawn. A dark halo
  behind each legend dot besides. climb/descent (cyan/amber) weren't touched --
  saturated enough to read on the basemap styles tried so far. The
  detail/settings panels already have their own opaque background so weren't
  affected. Not yet re-examined: the callsign tag in `_draw_planes_radar`
  (scope-only, not reachable in map mode)
  and any other bare text drawn straight onto the raster.

**Still open:**

- **`--raster` (bring-your-own) alignment.** Still trusts you to supply an
  image of the right box in the right projection -- `--raster-fetch` sidesteps
  this entirely, so it's only a gap for a custom source. Printing
  `frame_bbox_deg()`'s box for the user to export/crop to, or accepting the
  source's own bounds + projection and reprojecting, would close it. A visual
  overlay check (raster + vector coastline on top) would catch a mis-scaled or
  off-centre source either way.
- **Runtime toggle restores the whole look, not just the icons.** Toggling
  `DISPLAY_MODE` from the on-device settings overlay always changed the
  aircraft icon shape (`draw_planes()` already read it every frame), but nothing
  else followed along, in two separate ways found back to back:

  1. The **backdrop** stayed on whatever it booted with -- `_draw_map_backdrop()`
     only ever ran from boot and from the panel-shift path, never from the
     toggle itself, and even when it did run for `"radar"` it only drew
     `draw_radar_grid()` (rings), not `draw_basemap()` (coastline/airports) --
     so toggling to `"radar"` showed rings on a blank field instead of the full
     scope look. Fixed: `_toggle_setting()` now calls `_draw_map_backdrop()`
     too, and both its branches draw the *complete* look for their mode
     (raster, or `draw_radar_grid()` + `draw_basemap()` together) -- the same
     pairing `draw_scene()`'s non-2-layer path already draws every frame.  This
     only works from a **`"map"` boot** (2 layers): a **`"radar"` boot** (1
     layer) has no layer 0 to redraw into, so toggling into `"map"` from there
     still can't get the raster -- that half needs a second `Presto` bring-up,
     or always booting `layers=2` (and paying the extra buffer in scope mode
     too).

  2. Separately, `plane_pen()`, `draw_legend_alt()` and the status line all
     picked their pens off `_map_layers` -- true forever once booted with 2
     layers, regardless of which mode was *currently* showing -- so toggling to
     `"radar"` from a `"map"` boot kept the near-black `MAP_TEXT_PEN`/
     `MAP_VSTATE_PENS` meant for a light raster, now sitting on a dark scope
     background (illegible). `_showing_raster`, a new flag `_draw_map_backdrop()`
     sets to reflect what it actually just drew (true only after a successful
     raster decode, false for the vector-grid fallback, `"radar"`, or no `"map"`
     boot at all), replaces `_map_layers` at all three call sites -- contrast
     now tracks what's actually behind the text/dots/icons rather than what the
     hardware is capable of.
- **Regen on centre/radius change.** `deploy.sh` copies `basemap.jpg` but cannot
  rebuild it. `--raster-fetch` makes this a one-line re-run, no state needed;
  `--raster`'s sidecar records the source path so a `--raster-refresh` that
  re-runs from it is possible there too.
- **Attribution.** `--raster-fetch` prints the required Esri/OSM credit line but
  nothing shows it on-device -- fine for a personal desk display, would need a
  small always-on label if this were ever shared or shipped.
