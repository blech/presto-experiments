# Fonts at 480x480 (full_res) on the Presto — what actually works

Behind `font_probe.py` / `font_probe.sh` and the `diag_*.py` one-shots. The
question: prestoradar kept trying to put a nicer-than-bitmap font on the
full-res display and backing out (`render.py` ~line 221: *"PicoVector +
Roboto-Medium.af → NotImplementedError: opcode"*, built-in *"sans"* vector
font *"renders as a scribble of strokes"*), yet `presto/examples/co2.py` runs
PicoVector text at full_res. Why?

**Tested on device**, firmware **3.4.0** / MicroPython **1.29.0**
(`feature/presto-wireless-august-2026`, `_mpy=7942`).

## Bottom line

**PicoVector `.af` text works fine at 480×480 on this firmware.** The earlier
abandonment was based on (a) an `opcode` error that no longer reproduces and
(b) a soft-reset wedge that kept making the *next* run look like the font had
failed. Use `vector.text()` with `osansb.af` **or** `Roboto-Medium.af`; code
around one cosmetic artifact (below).

## Results

| Backend at full_res (480×480) | Result |
|---|---|
| **Bitmap** (`display.text`, bitmap6/8) | ✅ Perfect. What the radar already uses. |
| **PicoGraphics built-in vector** (`display.set_font("sans")` …) | ❌ Corrupt scribble, *and* left the board un-resettable. Unusable — matches the old note. |
| **PicoVector `vector.draw()` shapes** (Polygon circle/rect) | ✅ Clean. |
| **PicoVector `vector.text()` + `osansb.af`** | ✅ Text renders correctly and legibly. ⚠️ leaves a thin band of stray pixels (uninitialised — seen green and blue) along the **top edge**, ~y=0–30. See workaround. |
| **PicoVector `vector.text()` + `Roboto-Medium.af`** (incl. `set_font_letter_spacing` / `word_spacing`, e49dede's exact call order) | ✅ `set_font`→`True`, `measure_text`→sane `(2.0, 0.0, 236.0, 20.0)`, `text()` draws, legible. **`NotImplementedError: opcode` does NOT reproduce** — it was an earlier-firmware artifact. |

`measure_text()`, calling `text()` as the first PicoVector op, priming with a
throwaway `draw()`, `layers=1` vs `2`, font size — none of these change the
top-edge band. It's just what `vector.text()` does at full_res.

### Workaround for the top-edge band

The stray pixels are *in the framebuffer* and paint over. After the
`vector.text()` pass, draw an opaque rectangle over the top ~34 px
(`display.set_pen(bg); display.rectangle(0, 0, WIDTH, 34)`), or simply keep
vector text out of the top ~34 px. Confirmed to remove it completely.

## The real reason iteration kept failing: the soft-reset wedge

Once a process has brought up `Presto(full_res=True)`, this firmware can no
longer be **soft**-reset — mpremote / Thonny's Ctrl-D / the Stop button all
hang at "could not enter raw repl", and only a physical **RESET** (button;
plain RESET, *not* BOOT+RESET which is the UF2 bootloader) or a
`machine.reset()` clears it. So a font experiment that actually rendered fine
would still make the *next* run fail to start, and the failure got pinned on
the font.

Also seen: PicoGraphics-vector-at-full_res, and once a bad prior state, made
`machine.reset()` itself hang → button needed.

**How to iterate on full_res code without the treadmill:**
- `mpremote run --no-follow <file>` — detaches instead of soft-resetting on
  exit. The `diag_*.py` scripts hold their frame with `while True: sleep`.
- hard-reset (button or `machine.reset()`) between runs; never soft-reset.
- `Presto()` still only comes up once per process — one combo per run.

`font_probe.py` was built around this (each step records to
`/font_probe.results` then `machine.reset()`s) but the self-reset isn't
reliable enough after PicoGraphics-vector; the `diag_*.py` one-shots +
`--no-follow` + a human eyeballing the screen turned out to be the workable
loop. `font_probe.py` / `.sh` are kept for the bitmap/shape rows and as a
harness skeleton.

## Notes

- `presto/examples/co2.py` (full_res + PicoVector + `osansb.af`) is not
  stale — it works; its graph polygons and bottom-anchored text just never
  put vector text in the top band so the artifact goes unnoticed.
- The firmware image contains the string *"Presto: full_res is not supported
  by the PicoVector rasteriser."* but it did **not** print in any run here —
  plain `vector.text()` / `vector.draw()` at full_res don't trip it. Likely
  gated on `direct_to_fb=True` or `palette=True`; untested.
- `render.py`'s note at ~line 221 is now out of date. If the panels move to
  PicoVector text, that comment and the bitmap-only `_ptext()` can go.

## It's a firmware regression: clean on v1.0.0, broken on v2.0.0

Flashed the plain (no-filesystem) release uf2s and re-ran the probes
(`dev/fw_probe.py` isolated, and a module-context harness that imports
`render.py` and drives `draw_card`):

| firmware | MicroPython | vector.text() at full res | soft-reset wedge |
|---|---|---|---|
| **v1.0.0** | 1.26.0 (2025-08-18) | **clean** — circle, text, top edge all fine; card + settings panel render clean via `Renderer`, `draw_card+panel` 92 ms | **none** — mpremote reconnects fine after `Presto(full_res=True)` |
| **v2.0.0** | 1.29.0 (2026-08-26, "presto-wireless-august-2026") | the y21–24 stray-pixel band is back; cold bringups sometimes tile/corrupt the whole framebuffer | yes — next mpremote op hangs until a physical RESET |

So on v1.0.0 the radar could run `_PANEL_VECTOR_FONT = True` today. Every
symptom — the band, the cold-boot corruption, the wedge — is new in v2.0.0.

### Where the regression is

`git diff v1.0.0..v2.0.0` in the `presto` repo (checked out at `main` =
`b96a8cb` = v2.0.0) is a wholesale **PSRAM memory-map rework**:

- `boards/presto/memmap_mp_rp2350_psram.ld` (the bespoke PSRAM linker script)
  **deleted**; switched to the SDK's `MICROPY_HW_ENABLE_PSRAM` + `presto.ld`.
- PSRAM shrunk `8192k → 8128k`, top **64k reserved for a new RAMFS**.
- New `.psram_load` / `.psram_noload` sections; LWIP buffers moved from
  `.psram_data` to `.psram_uninitialised.lwip`.
- PSRAM GC heap re-based: `__PsramGcHeapStart = __psram_end__` (was
  `__psram_data_end`), `__PsramGcHeapEnd` 64k lower.
- MicroPython 1.26 → 1.29, and the (unpinned) `pimoroni-pico` / pretty-poly
  checkout moved too.

The full-res framebuffer and PicoVector's pretty-poly tile/AA buffers are all
PSRAM allocations. The artifact is content-dependent (`vector.text()`'s
per-glyph tile render dirties a *fixed* framebuffer band; `vector.draw()`
shapes and bitmap text are clean), which points at a pretty-poly working
buffer that is now mis-based or overlapping the framebuffer after the
reshuffle — not the ST7701 scanout (its diff is PIO-reset / rotation only,
and a scanout bug would be content-independent). Root-causing further needs a
local firmware build with the PSRAM allocations instrumented.

### Pixel-format probe (narrows the mechanism)

Re-ran the repro with `Presto(full_res=True, palette=True)` (PEN_P8, 1 byte/px):

| mode | framebuffer | result |
|---|---|---|
| RGB565 (`palette=False`) | `presto.py` passes `buffer=None` → **PicoGraphics self-allocates it from the PSRAM GC heap** | the y21–24 top band |
| P8 (`palette=True`) | `buffer = memoryview(self.presto)` → the **fixed `_presto` native buffer** | **no top band**, but the glyphs themselves are corrupt — letters partly overwritten by black/garbage (e.g. the "P" in "B789", scrambled letters in "the quick brown fox") |

The band did **not** move to y≈42 in P8 — it vanished. So it's not a raw
byte-offset overrun. It's tied to the **RGB565 path where PicoGraphics
mallocs the full-res framebuffer itself**: a PicoVector/pretty-poly scratch
or tile buffer (also a PSRAM allocation) collides with that malloc after
v2.0.0 re-based the PSRAM GC heap. P8 uses the fixed native buffer so it
dodges that collision, but then hits a separate stride/format bug in the
per-glyph blit. **Neither pixel format gives usable PicoVector text at full
res on v2.0.0** — palette mode is not a workaround.

### Worth reporting to Pimoroni

Minimal repro on a v2.0.0 Presto: `Presto(full_res=True)` → `PicoVector` →
`set_font("osansb.af", 20)` → `vector.text("...", x, y)` → `update()`. A
3–4 px band of stray pixels appears across the top of the screen (y ≈ 21–24)
regardless of where the text was drawn; no `vector.set_clip()` or
`display.set_clip()` contains it. `vector.draw()` of a `Polygon` is clean, as
is bitmap text. Clean on v1.0.0. With `palette=True` the band is gone but the
glyphs are corrupt instead. The firmware image also carries the string
*"Presto: full_res is not supported by the PicoVector rasteriser."* (does not
print in this repro).

## render.py integration — attempted, parked behind a flag

`render.py` now has a full PicoVector path for `_ptext()` (the detail card and
settings panel), plus `_repair_top_band()` for the top-edge artifact and
`osansb.af` added to the repo + `radar_deploy.sh`. It is gated on
`_PANEL_VECTOR_FONT` (top of `render.py`), **default `False`**.

Why it's off: exercising it the way the radar actually does — PicoVector
brought up inside `Renderer.__init__`, i.e. inside imported module code after
`render.py` and the big data modules are loaded — wedged or corrupted the
display on every attempt (a small standalone script never did). Part of that
is the pre-existing `Presto(full_res=True)` flakiness — in one run
`Presto(full_res=True)` hung *before* PicoVector was even touched — but it
couldn't be cleared for this path. The isolated `diag_*` runs stayed clean, so
the font itself is fine; the integration is the unknown.

To try it: set `_PANEL_VECTOR_FONT = True`, deploy, and cold-boot the radar a
few times. Leave it on only if the panels render and the display is stable
across boots. `dev/test_card_corner.py` still passes with the flag either way.

## Files

- `font_probe.py` — stepped matrix probe + `show()` one-shot. `count()`,
  `main(n)`, `summary()`, `show(full_res=, layers=, backend=, font=,
  spacing=, size=, mask_top=)`.
- `font_probe.sh` — driver for the matrix (`--fresh`, `--hold`, `FROM`/`TO`).
- `diag_*.py` lived in the scratchpad, not committed; recreate from the
  snippets in this session if needed.
