#!/usr/bin/env python3
"""Desktop test for feed.Feed.resolve() -- no network, no device."""

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


class _P:
    def __init__(self, hex):
        self.hex = hex


def test_resolve():
    from feed import Feed
    f = Feed("host", "/path", "agent/1.0", 256, 30_000)
    plane = _P("aabbcc")
    f._by_hex = {"aabbcc": plane}

    _eq(f.resolve("aabbcc"), plane, "resolves a hex present in the current registry")
    _eq(f.resolve("ffffff"), None, "a hex not in the registry resolves to None")

    f._by_hex = {}   # simulates the aircraft dropping off the next fetch cycle
    _eq(f.resolve("aabbcc"), None, "a departed aircraft's hex no longer resolves")


def main():
    test_resolve()
    print("feed.Feed.resolve(): all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
