# presto-experiments

Experimental / sample / in development MicroPython code for the
[Pimoroni Presto](https://shop.pimoroni.com/products/presto)
microcontroller-powered 4" display.

## Life

An implementation of Conway's classic Game Of Life, with a series of RLE files
encoding possibly interesting patterns. It could do with a settings UI - or file -
but at the moment it's running in 'kaleidoscope' mode, where each quadrant is
reflected in the others, which hopefully achieves a pleasing background effect.

This really needs a deploy script; the `life-rles` directory needs to be placed
on device alongside `life.py``. Meanwhile, `life-listener.py` and
`life-listener-ncurses.py`  provide monitoring on a computer for the rendering process.

## Others

### 4096 Farben

Colour space explorations inspired by Gerhard Richter.

### Icosahedron

Cubes are great, but you know the best platonic solid? Right.

