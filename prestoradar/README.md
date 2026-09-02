# Presto Radar

## What

Use a [Pimoroni Presto](https://shop.pimoroni.com/products/presto) to display a
radar-style visual of aircraft in the airspace near a point.

## How

* `cp prestoradar/settings_example.py prestoradar/settings.py`, then edit it -
  particularly `CENTER_LAT` and `CENTER_LON`. `settings.py` is gitignored (it's
  per-location); `settings_example.py` is the tracked template. `./deploy.sh`
  creates `settings.py` from the template on first run if it's missing.
* Generate a local base map with `python3 prestoradar/make_basemap.py --download`
  (the first run caches GSHHG coastline data and the OurAirports list; later
  runs can drop `--download`). Airport marks are filtered from OurAirports by
  size via `settings.BASEMAP_AIRPORTS` (`large`/`medium`/`small`/`none`);
  `--airports <tier>` overrides it for a one-off build. `basemap_data.py` is
  generated and gitignored; `./deploy.sh` rebuilds it when `settings.py` changes.
* Run `./deploy.sh` from the top level to push the code
* Reset the Presto, either with eg `ampy reset --hard` or the physical button
* Select "Presto Radar" from the main menu (or eg `ampy run presto_radar.py`)

## Example

![Radar centred on San Francisco](example_sanfrancisco.png)

