#!/usr/bin/env python3
"""
Desktop (CPython) test for nearby.py's own presentation logic -- no WiFi, no
device. The classification core (board.bucket() and friends) moved to
board.py and its own dev/test_board.py; this file now only pins what's left
in nearby.py itself: the route column's text states.

    python3 prestoradar/dev/test_nearby.py
"""

import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import nearby  # noqa: E402


def _eq(got, want, what):
    if got != want:
        raise AssertionError("%s: got %r, want %r" % (what, got, want))


def main():
    # _fmt_route turns a routes.get() state (+ retrying flag) into the column.
    _eq(nearby._fmt_route(("SFO", "JFK")), "SFO->JFK", "a resolved route")
    _eq(nearby._fmt_route(("?", "LHR")), "?->LHR",
        "an endpoint the source only half-knew")
    _eq(nearby._fmt_route(""), "...", "pending is an ellipsis")
    _eq(nearby._fmt_route(None, retrying=True), "...",
        "unknown but still retrying is an ellipsis")
    _eq(nearby._fmt_route(None, retrying=False), "?",
        "unknown with the retries spent is a question mark")
    _eq(nearby._fmt_route("absent"), "", "never requested is blank")

    print("nearby.py: presentation assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
