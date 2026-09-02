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
`requests.get()` (up to a 15 s timeout) freezes the animation during a fetch.
That is a networking concern; touch would just come along for free since it is
already non-blocking. The screenshot TCP server (item 4) adds only a little more
pressure -- a non-blocking `accept()` per frame plus one on-demand burst send --
not a recurring stall. Running tally: fetch = real, touch = none, screenshot
server = transient. Not enough yet.

One caveat: `ANIM_INTERVAL = 0.5` means touch is only sampled ~2x/s, so a fast
tap can be missed. Either shorten the loop sleep and decouple poll rate from
redraw rate (poll ~50 ms, redraw ~500 ms), or accept that a deliberate tap is
held longer than 500 ms. Still no asyncio.

### 2a. Tap a plane → detail overlay

The callsign is the only thing shown, but the feed carries much more. On tap,
hit-test against the plane screen positions from the last `draw_scene()` (keep a
`[(x, y, plane), ...]` list as they're drawn; match within a few px), mark one
plane "selected", and draw a panel with:

- registration (`r`), type (`t`) and `desc` if present
- altitude (`alt_baro`) + climb/descent rate (`baro_rate`) — reuse the vstate
- ground speed (`gs`), track (`track`)
- squawk (`squawk`), `emergency` flag
- distance + bearing from centre (`dst`, `dir` — already in the feed)

Tap elsewhere / on the panel to dismiss. Selected plane could also get a ring or
brighter marker.

### 2b. Settings screen + on-device persistence

`settings.py` is currently edit-and-redeploy only. Add a touch-reachable
settings screen (long-press? corner button? tap the centre?) that edits the
tunables that make sense at runtime — `RADIUS_KM`, `HIDE_ON_GROUND`,
`DRAW_BASEMAP`, `ANIM_INTERVAL`, maybe `LEVEL_RATE_FPM`.

Persistence: `settings.py` stays the defaults; write overrides as JSON to
`/prestoradar/settings_local.json` (gitignored). On boot, load defaults then
merge the file. Changing `RADIUS_KM` at runtime needs the derived values
(`RADIUS_NM`, `RADAR_URL`, `PX_PER_KM`) recomputed and ideally a basemap that
matches — note that `make_basemap.py` is keyed to `CENTER_LAT/LON` + a 50 km
clip, so a much larger `RADIUS_KM` would need a regenerated `basemap_data.py`.

---

## 3. Aircraft-type icons (research)

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
