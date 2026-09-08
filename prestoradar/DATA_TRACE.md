# Flight trace (position history): source decision + live check

Handoff notes for adding a per-aircraft position-history trail to Presto Radar
(the "breadcrumb behind the selected plane" that `feed.py`'s wholesale list
replacement currently made impossible -- see `DATA_TODOS.md` item 4).

This document is the result of comparing three candidate trace sources and
running a live check against the chosen one on 2026-09-08. A separate agent
should be able to implement from here without re-doing the evaluation.

## Status -- landed 2026-09-08

Implemented as described below.

- `dev/trace_gzip_test.py` -- on-device probe. Ran on Presto firmware: `import
  deflate` PASS; bare `adsb.lol` returns 200 (no 302); body is gzip even with
  no `Accept-Encoding`; `deflate.DeflateIO(..., GZIP)` inflates a 3.6 KB body
  to 16 KB in 28 ms, `json.loads` 58 ms, ~16 KB transient against ~8 MB free.
- `traces.py` -- `request(p)` / `get(hex)` / `points_for(plane)`, mirroring
  `routes.py`. Direct host, gzip inflate (firmware `deflate`, `zlib` wbits=31
  on the CPython harness), project + downsample to ~45 points, 4-entry cache
  with a 90 s TTL and the size guards. `settings.TRACE_SEED` (default 1)
  toggles the network seed independently of the RAM trail.
- `feed.py` -- keeps a `hex -> Plane` registry and passes last fetch's Plane
  back to `Plane.from_feed(..., into=)` so an aircraft's object (and its
  `trail` of past `(e, n, alt)` fixes, one per fetch, capped at
  `plane._TRAIL_MAX`) survives across fetches. This is item 6's RAM fallback.
- `render.py` -- `_draw_trace()` draws the polyline (viewport-clipped) under
  the markers in `TRACE_PEN`; radar mode only. While a trace shows, the
  selected aircraft's track arrow and every other aircraft's callsign tag are
  suppressed.
- `dev/trace_lookup.py` -- CPython harness alongside `dev/route_lookup.py`.

Still open / not done: altitude colouring of the trail (single muted pen for
now -- step 7), a `trace_full` path (correctly avoided), and the route
disambiguation reuse below.

**Decision: seed the trail from adsb.lol's tar1090 `trace_recent`, with the
in-RAM accumulated trail as the always-available fallback.**


## Why adsb.lol `trace_recent` (not the other two, not `trace_full`)

Candidates considered:

| Source | Verdict |
|---|---|
| **adsb.lol `trace_recent`** (tar1090 `trace_recent_{hex}.json`) | **Chosen.** Same aggregator we already use for live positions (`api.adsb.lol/v2/point`) and route legs (`routes.py`). Keyless, no account. Keyed by ICAO hex, which `feed.py` already keeps per plane. Entry shape drops straight into `geometry.project()`; `alt` feeds the existing `COLOUR_MODE = "alt"` colouring. Best coverage of the three (feeder-network data -- covers GA/rotorcraft that OpenSky's track endpoint often lacks). |
| OpenSky `/api/tracks/all?icao24=...&time=0` | Rejected as primary. Smaller payload, but `time=0` only returns *currently airborne* aircraft, the endpoint is officially "experimental", coverage is worse (ads-b-playground and velocity both keep a fallback for exactly this), and it's a host we touch nowhere else. Reasonable *second* fallback if ever wanted. |
| FlightRadar24 via `FlightRadarAPI` SDK | Rejected outright. CPython SDK with `requests` etc. -- will not run on MicroPython. Reimplementing FR24's private endpoints by hand is fragile and ToS-grey. |

`trace_recent` vs `trace_full`: **use `trace_recent`.** `trace_full` is ~24 h of
history (previous flight + ground time included) and decompresses to hundreds of
KB -- it will OOM the Presto (framebuffer + body + parsed dict share RAM;
`settings_example.py` already warns an oversized body "shows up as a JSON /
memory error rather than an HTTP one"). `trace_recent` is the last ~5 minutes,
which is all a 30 km scope shows a plane for anyway.


## Live check -- 2026-09-08

Request (no `Accept-Encoding` sent, mimicking `net.py`):

```
curl -A 'presto-radar/1.0 (+https://github.com/blech/presto-experiments)' \
     https://adsb.lol/data/traces/a6/trace_recent_aa79a6.json
```

Hex `AA79A6` resolved to **N774UA**, a United **Boeing 777-200**, airborne out
of SFO (callsign UAL599). Both trace files returned HTTP 200 with valid data.

| | `trace_recent` | `trace_full` |
|---|---|---|
| Wire size (gzip) | **3.7 KB** | 52 KB |
| Decompressed | **16 KB** | 371 KB |
| Points | 92 | 2 223 |
| Time span | 4.4 min | ~25 h |

16 KB decompressed for `trace_recent` is ~the size of one full live
`/v2/point` fetch (~15 KB). Acceptable for a tap-triggered, one-aircraft-at-a-
time lookup. A long-haul plane mid-cruise may run somewhat larger but is still
bounded by the ~5 min window.

### Gotcha 1 -- `globe.adsb.lol` 302-redirects to `adsb.lol`

```
https://globe.adsb.lol/data/traces/a6/trace_recent_aa79a6.json
  -> 302 Location: https://adsb.lol/data/traces/a6/trace_recent_aa79a6.json
```

`net.py`'s `http_get()` does not read or follow `Location`. **Request
`adsb.lol` directly** -- do not use the `globe.` host velocity's code uses:

```
host = "adsb.lol"
path = "/data/traces/%s/trace_recent_%s.json" % (hex_lc[-2:], hex_lc)   # hex lowercase
```

### Gotcha 2 -- responses are ALWAYS gzipped, even with no `Accept-Encoding`

The check sent no `Accept-Encoding` header and still got:

```
content-type: application/json
content-encoding: gzip
content-length: 3752          <- this is the COMPRESSED length
```

(There is a `globe-gzip-sticky3` cookie -- the gzip is deliberate server
behaviour, not content negotiation.) `net.py` does no decompression, so
`json.loads` receives raw gzip bytes and fails (reproduced during the check:
`'utf-8' codec can't decode byte 0x8b in position 1`).

**This is the blocker to resolve first.** The body must be gunzipped before
parse:

```python
import deflate, io
raw = deflate.DeflateIO(io.BytesIO(body), deflate.GZIP).read()
data = json.loads(raw)
```

**Before building anything, confirm the `deflate` module exists in the Presto's
MicroPython firmware** (`import deflate` at the REPL, or check with
`radar_debug.py`). If it is absent, this whole approach is blocked and the
in-RAM accumulated trail (below) becomes the only option.

`content-length` is the compressed length, so `net.py`'s existing
content-length read path is correct as-is; only an inflate step is added after
the body is fully read. The trace responses seen were **not** chunked
(`content-length` present).


## Trace entry shape

Each element of the top-level `trace` array is a 14-element list. Base epoch is
the file's `timestamp` field; entry `[0]` is seconds offset from that base.

```
[ dt_s, lat, lon, alt, gs, track, flags, vrate, <details|null>, source, alt_geom, geom_rate, ... ]
   0     1    2    3    4    5      6     7       8               9      10        11
```

- Presto only needs `[0:6]` -> `(t, lat, lon, alt, gs, track)`.
- `alt` is feet, or the string `"ground"` -- same convention as the live feed;
  reuse `feed.py`'s handling / `geometry.alt_key`.
- Element `[8]` is a fat per-point detail dict (present on most points) and is
  the bulk of the JSON weight. It cannot be skipped during `json.loads`, so the
  ~16 KB parse cost stands regardless of which fields are kept.
- `trace_recent` for the test aircraft was 92 points over 4.4 min (~3 s
  cadence). Downsample to roughly every 3rd point for a scope trail, or cap at
  N most-recent.


## Implementation shape

Mirror `routes.py`, not `feed.py` -- this is a lazy, on-selection lookup, never
part of the poll loop.

1. **Trigger:** on tap-to-inspect (`ui.py` selection), with the selected
   plane's `hex`. One trace in flight at a time.
2. **Fetch:** `net.http_get("adsb.lol", trace_path, settings.USER_AGENT)` ->
   gunzip -> `json.loads`. `gc.collect()` before and after, as
   `feed._fetch()` does.
3. **Guard the size:** pass a max-bytes ceiling into the read (`net.py`
   currently reads up to `content-length` or `1<<30`); if the compressed body
   exceeds, say, 48 KB, or the inflated body exceeds ~64 KB, abort and fall
   back to the RAM trail rather than risk the allocator.
4. **Project + downsample:** map `[0:6]` through `geometry.project()`, keep
   ~30-60 points, oldest->newest.
5. **Cache:** by hex, ~60-120 s TTL (a trace "barely changes over a few
   minutes"). Re-tap within TTL reuses it. Bound the cache (a few entries) --
   same unbounded-dict caution as `routes._cache` (`DATA_TODOS.md` item 3).
6. **Fallback -- the RAM trail:** independently of the fetch, keep the last N
   live fixes per selected hex from the poll loop (`feed.py` already computes
   `(ve, vn)`; appending `(e, n, alt)` each successful fetch is cheap). Render
   this whenever the `trace_recent` fetch is unavailable (host down, gzip
   unsupported, size guard tripped, aircraft not in adsb.lol's trace set). This
   is the same graceful-degradation mix ads-b-playground and velocity use:
   real history when it's there, own-accumulated trail when it isn't.
7. **Render:** a polyline under the aircraft marker, altitude-coloured when
   `COLOUR_MODE == "alt"`, plain in `"mono"`. Respect `DISPLAY_MODE` (radar vs
   map) the same way the marker layer does.

Add a `settings.py` toggle (default on) so the network trace seed can be
disabled independently of the RAM trail.


## Verification steps for the implementer

- `import deflate` succeeds on the device firmware. (Hard gate.)
- `dev/` desktop harness: add a `dev/trace_lookup.py` alongside
  `dev/route_lookup.py` that resolves `--hex` against the live feed then fetches
  and prints the trace, so parsing can be checked on CPython without a deploy.
- Confirm the redirect is avoided (request `adsb.lol`, expect a direct 200, not
  a 302).
- Confirm a hex with no trace (e.g. an aircraft adsb.lol isn't currently
  tracing) degrades to the RAM trail, not an error.
- Watch `gc.mem_free()` in the log around a trace fetch on the busiest scope
  you can find; the inflate doubles peak transient memory (compressed +
  inflated both live briefly).


## Reusing the trace for route disambiguation

`dev/route_check.py` (and, in simpler form, `routes.py`) decides which leg of a
candidate route an aircraft is actually flying by scoring a single
instantaneous `(lat, lon, track)` sample against each leg's great circle:
cross-track distance to the A->B line, along-track within the leg, and current
heading within 90 deg of the bearing to B. That single sample is the weak
point -- it is null on the ground, noisy at low speed, and momentarily wrong in
a vectored turn, a hold, or a procedure turn, which is exactly when routes are
most ambiguous (near an airport, "departing A" and "arriving at A" put the
plane in the same place).

`trace_recent` -- the same ~90-point, ~5-minute `(t, lat, lon, alt, gs, track)`
array this document is about -- turns that one sample into a short path, which
disambiguates better in four ways, roughly in order of value:

1. **Altitude trend -- a signal `score_leg` currently has none of.** Trace
   entries carry `alt` (feet, or `"ground"`). Linear-fit it over the window:
   a clear climb means recently departed, so the airport near the *start* of
   the trace is the origin; a clear descent means approaching, so the airport
   near the *end* is the destination. This alone resolves the module
   docstring's UAL2274 case -- climbing near SFO means SFO is the origin, which
   kills the `KIAD -> KSFO` candidate (that leg needs SFO as the destination,
   i.e. a descent) without consulting heading at all. Use
   `settings.LEVEL_RATE_FPM` for the climb/level/descent cutoff, same as
   `feed.py`.
2. **Multi-point cross-track fit instead of one sample.** Compute cross-track
   for the last K trace points against each candidate leg and use the mean
   (and max). A single fix can sit within 50 nm of two legs' great circles
   near a hub where airways converge; a 5-minute segment that hugs one leg's
   line and not another's is far more discriminating, and the averaged error
   is clean enough to tighten the threshold (~25 nm mean vs the current 50 nm
   single).
3. **Direction of travel from the path, not the transponder `track` field.**
   Net course = bearing(oldest kept point -> newest), which is robust where the
   instantaneous `track` is not. Add a monotonic-progress check: does the
   along-track projection onto A->B actually increase across the window? A
   plane genuinely flying A->B shows increasing along-track; one arriving at A
   (which the wrong leg models as "departing A") does not.
4. **Endpoint proximity / ground state.** ~5 minutes is ~40-60 nm at jet
   speed. For short legs and the just-after-departure / just-before-arrival
   windows, the oldest trace point may land within a few nm of the true origin
   (or read `alt == "ground"` there) -- a near-certain origin confirmation,
   available exactly where the geometry is most ambiguous. For mid-cruise
   long-haul the trace reaches neither airport and this contributes nothing,
   but cross-track alone already handles cruise.

**Where it does not help:** an aircraft that just appeared (2-3 trace points)
-- fall back to the existing single-sample score; and mid-cruise, as above.

**Cost / synergy.** It is one more fetch, with the gzip + direct-host handling
from this document. If the trace-trail feature above is built, the trace is
*already fetched and cached on selection*, so `routes.py` can reuse the cached
trace for disambiguation at no extra network cost -- one request pays for both
the trail and a better route verdict. For `dev/route_check.py` (desktop, CPython,
no RAM or request-budget constraint) there is no cost concern -- just add it.

**Concrete change to `dev/route_check.py`:**

- Add `async def fetch_trace(hex_id)` returning
  `[(t, lat, lon, alt, gs, track), ...]` from
  `adsb.lol/data/traces/{hex[-2:]}/trace_recent_{hex}.json` (gzip + direct
  host per this doc). `route_check.py` already resolves the aircraft's `hex`
  in `find_aircraft()`, so it is available.
- Add `score_leg_with_trace(trace, a_lat, a_lon, b_lat, b_lon)` returning the
  mean/max cross-track, the along-track delta (oldest -> newest), the alt slope
  in ft/min, and `origin_confirmed` / `dest_confirmed` booleans.
- Keep the existing `score_leg` as a printed fallback column so a disagreement
  between the single-sample and trace-based verdicts is visible; add columns
  for `mean XT`, `alt trend`, and `orig/dest confirmed`.
- When `fetch_trace` returns fewer than ~5 points, skip the trace scorer and
  use `score_leg` alone.

The same `score_leg_with_trace` logic can later move into `routes.py` behind
the shared trace cache; keep the geometry in a form that ports (plain lat/lon
math, as `route_check.py` already uses).


## Not doing

- `trace_full` (OOM risk -- see above).
- OpenSky or FR24 as the primary source (see the candidates table).
- Any trace fetch inside the poll loop -- selection only.
- Following the `globe.adsb.lol` redirect in `net.py` -- just use `adsb.lol`.
