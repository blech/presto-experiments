# Entry point for the flight radar, kept at the repo/device root so it can be
# started the usual ways -- `mpremote run presto_radar.py`, `ampy run
# presto_radar`, or picking it in the on-device launcher after a reset.
#
# The actual application lives in /prestoradar/ so the launcher doesn't list its
# modules (radar.py, basemap_data.py, ...). Only this one file sits at the root.
# radar.py starts itself with a bare main() call at module level, so importing it
# is enough -- don't add radar.main() here.
import sys

if "/prestoradar" not in sys.path:
    sys.path.insert(0, "/prestoradar")

import radar  # noqa: F401  (import runs radar.main())
