#!/usr/bin/env python3
"""Desktop test: after the view-shift removal, UI has no _target_view_cx and
view_cx never moves off centre. Constructs UI with lightweight stubs."""

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


class _Stub:
    def __getattr__(self, _n):
        return _Stub()

    def __call__(self, *a, **k):
        return None


class _P:
    def __init__(self):
        # callsign == hex makes routes.request() early-return (no route to
        # look up for a hex id); with settings.TRACE_SEED = 0 below,
        # traces.request() is a no-op too, so set_selected() never touches
        # the network or the event loop -- this stays a pure unit test.
        self.hex = "abc123"
        self.callsign = "abc123"
        self.e = 12.0
        self.n = -5.0
        self.heading = 90.0


def main():
    import settings
    settings.TRACE_SEED = 0        # make traces.request() a no-op (no running loop)

    import ui

    assert not hasattr(ui.UI, "_target_view_cx"), "_target_view_cx should be gone"

    # Construct UI with stubs. Signature after this task: no panel_x /
    # panel_margin / max_shift / min_view_cx.
    u = ui.UI(_Stub(), _Stub(), _Stub(), lambda p: False, lambda: None,
              7.0, 26, _Stub(), _Stub(), 0, 0)

    _eq(u.view_cx, 240, "view_cx starts centred")
    u.set_selected(_P())
    _eq(u.view_cx, 240, "view_cx does not move on select")
    u.set_selected(None)
    _eq(u.view_cx, 240, "view_cx still centred after dismiss")

    print("ui: view-shift removed, view_cx pinned to centre")
    return 0


if __name__ == "__main__":
    sys.exit(main())
