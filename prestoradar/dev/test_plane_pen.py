#!/usr/bin/env python3
"""Desktop test for Renderer.plane_pen's carried-forward (stale) dimming --
DATA_TODOS.md #4's rendering side. Stubs settings/backdrop/THEMES so no
PicoGraphics display is needed."""

import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _eq(got, want, what):
    if got != want:
        raise AssertionError("%s: got %r, want %r" % (what, got, want))


class _Settings:
    def __init__(self, colour_mode):
        self.COLOUR_MODE = colour_mode


class _Backdrop:
    def __init__(self, showing_raster):
        self.showing_raster = showing_raster


class _P:
    def __init__(self, vstate="level", missing_since=None):
        self.vstate = vstate
        self.missing_since = missing_since


def _make_renderer(colour_mode, showing_raster):
    import render
    r = object.__new__(render.Renderer)
    r.settings = _Settings(colour_mode)
    r.backdrop = _Backdrop(showing_raster)
    r.THEMES = {
        "radar": {"text": "RADAR_TEXT", "icon": "RADAR_ICON", "stale": "RADAR_STALE",
                  "vstate": {"level": "RADAR_LEVEL", "climb": "RADAR_CLIMB",
                             "descent": "RADAR_DESCENT"}},
        "map":   {"text": "MAP_TEXT", "icon": "MAP_ICON", "stale": "MAP_STALE",
                  "vstate": {"level": "MAP_LEVEL", "climb": "MAP_CLIMB",
                             "descent": "MAP_DESCENT"}},
    }
    return r


def main():
    r = _make_renderer("alt", showing_raster=False)
    _eq(r.plane_pen(_P(vstate="climb")), "RADAR_CLIMB",
        "a live aircraft still uses the normal vstate pen")
    _eq(r.plane_pen(_P(vstate="climb", missing_since=12345)), "RADAR_STALE",
        "a carried-forward aircraft is dimmed regardless of vstate")

    r_mono = _make_renderer("mono", showing_raster=False)
    _eq(r_mono.plane_pen(_P(missing_since=12345)), "RADAR_STALE",
        "carried-forward dimming overrides mono mode too")

    r_map = _make_renderer("alt", showing_raster=True)
    _eq(r_map.plane_pen(_P(vstate="descent", missing_since=999)), "MAP_STALE",
        "the map theme's own stale pen is used over the raster")

    print("render.Renderer.plane_pen: carried-forward dimming: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
