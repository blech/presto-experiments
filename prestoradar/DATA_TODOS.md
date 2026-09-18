# Data fetching: gap analysis and TODOs

Where Presto Radar's aircraft-fetch path stands against a set of ten other
free-feed trackers (desktop and server), and what it might best borrow from
them. Scope is the aircraft data path only -- `net.py`, `feed.py`, `routes.py`,
`geometry.py`, plus the `settings.py` knobs and the `dev/` harness. Basemap
fetching (`make_basemap.py`) is out of scope except as a pattern to reuse.

The ten compared: `flight-lookup`, `flight-tracking-application`, `skyboard`,
`sky-radar`, `termradar`, `flyover-alert`, `ads-b-playground`, `velocity`,
`Flight-Tracker`, `flyby33`.


## Where we already match the field

- **adsb.lol `/v2/point/{lat}/{lon}/{radius_nm}`** -- the same
  ADSBExchange-compatible endpoint shape four of the ten use (`termradar` default,
  `flyover-alert`, `ads-b-playground`, `velocity`). Radius as `round(km / 1.852)`
  nm is the universal idiom.
- **The `ac[]` field vocabulary** -- `hex, flight, lat, lon, alt_baro, gs,
  track, t, r` decoded in `feed.py._fetch()` is exactly what the other
  `/v2/point` consumers read.
- **Fixed-interval poll + keep-last-good-on-failure** -- `Feed.run()` at
  `FETCH_INTERVAL_MS` (30 s), old list kept animating on a failed fetch. Same as
  `sky-radar` (60 s), `flyover-alert` (60 s), `termradar` (5 s).
- **Keyless, with a descriptive contact User-Agent** -- adsb.lol rejects generic
  agents; `flyover-alert`, `termradar` and `ads-b-playground` all set the same
  kind of identifying string.
- **adsbdb for callsign -> route**, lazy (tap only), cached, non-fatal, with a
  retry/TTL notion. `flyover-alert` and `termradar` do the same call; neither
  cross-checks the answer.
- **Dead reckoning between polls** -- `feed.py` computes per-second `(ve, vn)`
  and the anim loop interpolates. `velocity` does the same thing at global
  scale (`SampledPositionProperty` + `LinearApproximation`).

In spirit the closest sibling is **`termradar`** (single radius source +
adsbdb, clean fetch/parse split, a CPython dev harness, a documented rate
posture). `routes.py` has already gone past it: the great-circle plausibility
check (cross-track <= 50 nm or 20% of the leg, along-track within the leg, plus
a heading check) and the escalation to adsb.lol's full-rotation
`/api/0/route` endpoint when adsbdb's single remembered leg doesn't fit are
more rigorous than anything in the ten except `ads-b-playground`'s route
validation -- and that one doesn't do the two-source escalation. See
`dev/route_check.py` for how this was derived, including the bug in adsb.lol's
own `plausible` flag (always truthy) that ruled out trusting it.


## TODOs, in priority order

### 1. A second position source + a small failure breaker

**Borrowed from:** `flyover-alert` (adsb.lol -> adsb.fi, each behind its own
circuit breaker).

**Why:** one flaky source is currently a blank scope. `routes.py._get_json()`
already notes adsb.lol answering `200` with `null` "when its backend is
unhappy", and the feed has no answer to that beyond skipping the cycle. A
prolonged adsb.lol wobble means the radar just shows nothing until it recovers.

**Do:**

- Add adsb.fi as a fallback: `opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{nm}`.
  Note the results key is `aircraft`, not `ac` -- `_fetch()` needs to know which
  key to read per source (a `(host, path_template, key)` tuple, the shape
  `flyover-alert`'s `FEEDS` uses).
- Try the fallback only when the primary fails, returns non-200, or parses to
  `null` / an empty `ac`. A good cycle never touches it, so there's no extra
  steady-state RAM or request cost.
- Add a consecutive-failure backoff: after N failed fetches (N ~= 3), stretch
  the interval to ~5 min until one succeeds, then snap back to
  `FETCH_INTERVAL_MS`. This stops the device hammering a dead endpoint every
  30 s and churning `gc` on each timeout. `flyover-alert`'s breaker
  (`threshold=4, cooldown=300`) is the reference; `velocity`'s daily
  0000-UTC breaker is overkill here.

**Cost:** ~30 lines in `feed.py`, one host constant, a `settings.py` toggle if
the fallback should be optional. No new memory in the common case.


### 2. A baked local type / registration table for the inspect panel

**Borrowed from:** `ads-b-playground` (multi-tier local enrichment:
registration -> country, ICAO24 -> country, type code -> manufacturer/model,
a ~249k-row year-built extract, all baked from public sources).

**Why:** when adsb.lol omits `t` / `r` (military, some GA, blocked airframes)
the tap-to-inspect panel shows blanks. PLAN.md already flags a local ICAO type
DB (mictronics `indexedDB`, or the OpenSky aircraft CSV) as a possible add.

**Do:**

- Reuse the `make_basemap.py` pattern exactly: fetch once on the desktop, filter
  to a compact dict, write it beside `basemap_data.py`, let `deploy.sh` carry
  it, decode at boot.
- Start with `type_code -> model/description` (a small, stable ICAO
  type-designator vocabulary -- only a few KB, covers most of the value). A full
  `icao24_hex -> (reg, type, desc)` table is far larger and needs a size-budget
  decision against the framebuffer; defer it.
- `feed.py` / `routes.py` consult the table only when the feed's own field is
  missing -- live value always wins, same precedence `ads-b-playground` uses
  (`live > adsbdb > local`).

**Cost:** a build-time script (like `make_basemap.py`'s OurAirports path) plus
the on-device decode. Size is the main constraint -- keep the first cut tiny.


### 3. Bound the route cache

**Borrowed from:** `termradar` (`CachedRouteProvider` with TTL + eviction).

**Why:** PLAN.md flags `routes._cache` / `_tries` as unbounded -- one entry per
callsign ever tapped. On a device sharing RAM with a full-res framebuffer this
is a real, if slow, leak.

**Do:** cap at ~50-100 callsigns, LRU eviction, over the existing
`(o,d)` / `None` / `""` / `"absent"` states. A resolved route can be dropped and
re-fetched later at no correctness cost.

**Cost:** small -- an `OrderedDict` or a manual move-to-front on hit.


### 4. Carry-forward for dropped contacts

**Borrowed from:** `velocity` (`_merge_with_previous(max_age_s)`, a 180 s
carry-forward window for contacts missing from a tick).

**Why:** `_fetch()` replaces `self.planes` wholesale every cycle. An aircraft
absent from a single response (feed hiccup, edge-of-range flicker) pops off the
scope and reappears 30 s later, instead of continuing on its last known vector.
The `(ve, vn)` needed to extrapolate it is already computed.

**Do:** merge fresh into old by `hex` instead of replacing. Keep an unmatched
plane for up to ~60-90 s, dead-reckoning from its last fix, with a `stale` /
`age` field the renderer dims or fades. Drop it past the window.

**Cost:** moderate -- an id-keyed merge in `feed.py`, a new field the renderer
honours, and a decision on how stale reads visually.


### 5. Snapshot sanity guard

**Borrowed from:** `velocity` (`_SNAPSHOT_MIN_RETAIN_FRACTION` -- reject a new
snapshot below 50% of the previous count unless the old one is already stale).

**Why:** a partial or `null`-ish adsb.lol response can blank or gut the scope
for a cycle. Cheap to reject.

**Do:** if a fetch returns fewer than ~50% of the previous plane count, keep the
old list for that cycle -- unless it's already older than a few intervals
(anti-lockout). Pairs naturally with 1 and 4.

**Cost:** a few lines in `Feed.run()`.


## Explicitly not doing

These fit the desktop/server tools in the comparison, not a microcontroller:

- **Multi-source ICAO24 dedup chains** (`ads-b-playground`'s 7-source priority
  merge). One source + one fallback is enough at ~35 aircraft; a real merge
  isn't worth the RAM or the code.
- **A global grid or firehose** (`velocity`: OpenSky `/states/all` +
  120-cell airplanes.live grid + FR24 mirror). `/states/all` alone is 5-6 MB.
- **OGN / APRS-IS** (`ads-b-playground`'s `ogn_source.py`) -- needs a persistent
  socket and a background thread holding an in-memory snapshot. No RAM budget.
- **Authenticated OpenSky.** Even ignoring payload size, adsb.lol already
  carries richer per-aircraft fields (`r`, `t`, `desc`, `category`, `baro_rate`)
  than OpenSky's state vector.
- **SQLite persistence, photo galleries, browser hand-off** (`ads-b-playground`,
  `flyby33`, `Flight-Tracker`).
- **HTTP keep-alive** across the 30 s interval -- not worth the RAM to save one
  ~280 ms TLS handshake per cycle.
