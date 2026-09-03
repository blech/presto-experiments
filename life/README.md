# Game of Life

## Getting started

* `pip install mpremote` - if it's not already available
* From the root of the repo, run `./life_deploy.sh`
* `mpremote run game_of_life.py`, or use the Presto launcher to run Game of Life

## Description

`game_of_life.py` and this directory provide a (currently non-interactive) display of Conway's [Game of Life](https://en.wikipedia.org/wiki/Conway%27s_Game_of_Life).

As currently configured, the Presto is run in low res mode (240x240 resolution), displaying an 80x80 grid of cells, with a repeating, mirrored 40x40 pattern that evolves from a random starting point.

However, there is code to show a non-kaleidoscope version, and to initialise based on [run-length encoded](https://conwaylife.com/wiki/Run_Length_Encoded) patterns, which can be used to start the display.

Meanwhile, there are two files in this directory for computer terminals, not the Presto. Those are `listener.py`, which shows currently updating info on the state of the current pattern iteration, and `logger.py`, a less sophisticated version which simply logs JSON lines.

## Further Development

These are things I'd like to get to, in theory.

* Allow configuration, either from `listener.py` sending commands back a Presto UI, passing to `mpremote game_of_life.py` or some combination
  - Enable/disable kaleidoscope (at startup, or even instantly?)
  - Colours / style options - currently black, white, grey, grid lines
  - Tweak startup probabilities (currently hardcoded and maybe broken?)
  - Starting pattern
  - General ruleset (ie allow variations from 23/3)
* Refactor UDP logging; add screenshot (see the `adsb-radar` branch)
* Add colours for cell state (born, survived?)
* ~Move Life class into life/life.py; keep launcher small~

## Notes

In general, I'm following the pattern seen here to co-exist with the Presto firmware, and in particular the default launcher:
* avoid use of `main.py`
* use a directory to house imports, data; both neat, and avoids showing files in launcher
* shell script to deploy directory and startup script
* (ideally) small launcher in main directory to keep code self-contianed

The only drawback to this approach is that, when the Presto is standalone, the script doesn't launch automatically; that's still reserved for the launcher. The fix is to `mpremote cp life.py :main.py` - but you'll need a copy of the [launcher script](https://github.com/pimoroni/presto/blob/main/examples/main.py) if you want to restore the default without a full reflash.