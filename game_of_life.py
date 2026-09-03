# NAME Game of Life
# ICON joystick
# DESC Conway's classic for Presto

import asyncio
from life.life import Life

life = Life()

# In the absence of better config, this (and the settings at the top of life/life.py)
# will be what you're looking for

# life.setup(kind="rle", filename="blinkers")
life.setup(kind="kaleidosoup")

asyncio.run(life._app_loop())
