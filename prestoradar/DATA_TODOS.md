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

**Done.** `Feed.__init__` takes an optional `fallback=(host, path, key)` --
`radar.py` wires it to `opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{nm}`,
key `"aircraft"` (adsb.lol's is `"ac"`), gated by a new `settings.ADSBFI_FALLBACK`
toggle (default on). `_fetch()` only queries it when the primary comes back
empty -- a request/HTTP/JSON failure, or a technically-successful-but-empty
response (the `200` + `null` "unhappy backend" case this item was written
for) -- via a pure `_combine_sources()` decision that's careful not to
confuse "both sources genuinely have nothing in range right now" with "both
sources are down." A normal cycle with real primary data never touches the
fallback: no extra request or RAM cost, as specced.

A new `_consecutive_failures` counter (independent of #5's `_stale_cycles` --
a too-small-but-real fetch is a different problem with a different remedy)
drives `_backoff_interval()`: after 3 consecutive fully-failed cycles (both
sources down or no fallback configured), `run()`'s sleep between fetches
stretches from `FETCH_INTERVAL_MS` to 5 minutes, snapping back the moment
either source succeeds. Matches `flyover-alert`'s reference
(`threshold=4, cooldown=300`) closely enough; `velocity`'s daily 0000-UTC
breaker was correctly judged overkill here and not built.

See `dev/test_feed.py` for the pure-helper coverage (`_extract_aircraft`,
`_combine_sources`, `_backoff_interval`); the actual dual-host network path
is on-device-only verification, like every other network path in this
codebase. `radar.py`'s `ADSBFI_PATH` reads `CENTER_LAT`/`CENTER_LON` off
`_settings_module` directly rather than the star-imported bare names
`RADAR_PATH` uses, so it adds no new `ruff` `F405` hits to radar.py's
pre-existing count.

**Borrowed from:** `flyover-alert` (adsb.lol -> adsb.fi, each behind its own
circuit breaker).


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

**Done.** `routes._cache` / `_tries` are capped at `_MAX_ENTRIES = 75`,
evicted together as a pair so they never fall out of sync. Both CPython and
MicroPython dicts preserve insertion order, so eviction is a plain "drop the
oldest key" (`_evict_oldest()`) rather than a separate `OrderedDict`; `get()`
calls `_touch()` to bump a still-displayed callsign to the recently-used end,
so a route currently on screen isn't the one that gets dropped. `request()`
evicts, if needed, only when it's about to add a genuinely new callsign --
re-fetching an existing one never touches eviction order. See
`dev/test_routes.py`.

**Borrowed from:** `termradar` (`CachedRouteProvider` with TTL + eviction).


### 4. Carry-forward for dropped contacts

**Done.** `Feed._fetch()` merges the fresh response into the previous
`_by_hex` by hex instead of replacing wholesale: an aircraft absent from one
fetch keeps dead-reckoning from its last fix (`Plane.missing_since`, set the
first cycle it's absent, cleared by `Plane.from_feed()` the moment it
reappears) for up to `_CARRY_FORWARD_MS` (75 s) since it was last actually
seen, then drops. `render.py`'s `plane_pen()` dims a carried-forward
aircraft to a dedicated `stale` theme pen, overriding vstate/mono. See
`_carry_forward()` in `feed.py`, `dev/test_feed.py`, `dev/test_plane_pen.py`.

One correctness wrinkle worth recording: `Feed._by_hex` can't be overwritten
unconditionally inside `_fetch()` any more, because item 5's snapshot guard
can still reject the whole result and keep the old `self.planes` -- if
`_by_hex` had already been updated to the (rejected) small snapshot, the
*next* cycle's carry-forward and object-reuse lookups would use a `_by_hex`
out of step with `self.planes`. Fixed by having `_fetch()` stash the computed
map as `self._pending_by_hex` and having `run()` commit it to `self._by_hex`
only in the same branch that accepts `self.planes = fresh`.

**Borrowed from:** `velocity` (`_merge_with_previous(max_age_s)`, a 180 s
carry-forward window for contacts missing from a tick).


### 5. Snapshot sanity guard

**Done.** `Feed.run()` rejects a fetch whose aircraft count falls below
`_MIN_RETAIN_FRACTION` (0.5) of the previous count and keeps the old list for
that cycle, unless it's already been kept for `_MAX_STALE_CYCLES` (3) cycles
in a row (the anti-lockout escape valve), in which case whatever comes back
is accepted regardless of size. Pure predicate: `_reject_snapshot()` in
`feed.py`; see `dev/test_feed.py`.

In practice this now rarely fires on its own: item 4's carry-forward already
backfills most of a partial/empty response with dimmed, still-alive contacts,
so the merged count stays close to the previous one for a single bad cycle.
The guard becomes the meaningful backstop only once carry-forward's own
window starts expiring stale entries across several consecutive bad cycles --
which is exactly when the anti-lockout limit is designed to let it through
rather than getting stuck. Kept as a cheap, independent circuit breaker
regardless -- the two mechanisms compose correctly, not redundantly.

**Borrowed from:** `velocity` (`_SNAPSHOT_MIN_RETAIN_FRACTION` -- reject a new
snapshot below 50% of the previous count unless the old one is already stale).


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
