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

## UI questions

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