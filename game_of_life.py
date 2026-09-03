# NAME Game of Life
# ICON joystick
# DESC Conway's classic for Presto

import asyncio

from life.life import Life

# Everything tunable lives at the top of life/life.py -- MODE picks the pattern.
life = Life()
asyncio.run(life._app_loop())
