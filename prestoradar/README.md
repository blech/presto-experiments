# Presto Radar

## What

Use a [Pimoroni Presto](https://shop.pimoroni.com/products/presto) to display a
radar-style visual of aircraft in the airspace near a point.

## How

* Edit `prestoradar/settings.py` - particularly `CENTER_LAT` and `CENTER_LON`
* Generate a local base map with `python3 prestoradar/make_basemap.py --download`
  (the first run caches GSHHG coastline data and the OurAirports list; later
  runs can drop `--download`). Airport marks are filtered from OurAirports by
  size - `--airports large|medium|small|none`, default `medium`.
* Run `./deploy.sh` from the top level to push the code
* Reset the Presto, either with eg `ampy reset --hard` or the physical button
* Select "Presto Radar" from the main menu (or eg `ampy run presto_radar.py`)

## Example

![Radar centred on San Francisco](example_sanfrancisco.png)

