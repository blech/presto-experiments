# Draw a grid of 4096 seven-pixel squares based on a shuffled RGB colour cube
# Named in reference to Gerhard Richter's painting
# https://www.gerhard-richter.com/en/art/paintings/abstracts/colour-charts-12/4096-colours-6089
# https://www.sothebys.com/en/articles/4096-farben
#
# In order to make this a more interesting script, it doesn't just draw a single
# rendition of 4096 uniquely-coloured pixels and then stop. Instead, it will swap
# pairs of neighbouring pixels. Initially it will only do this for one pair; then
# two; until it's swapping 256 pairs, at which point it resets down to one again.
#
# NAME 4096 Farben
# DESC Draw a grid of 4096 seven-pixel squares based on a shuffled RGB colour cube
# ICON deployed_code

import time
from random import randrange, randint

from presto import Presto

PIXEL_SIZE = 7    # Size of each rectangle: 64*7 = 448
COLOUR_STEP = 12  # How much to increment the RGB step; not 16 for a faded quality
OFFSET = 16       # Since 448 < 480 and 64*8 > 480, centre the drawn pixels

TICK = 5          # Seconds between redraws

from random import randrange

# https://stackoverflow.com/questions/73143243/are-there-any-alternatives-for-the-python-module-randoms-shuffle-function-in
def shuffle(array):
    "Fisher–Yates shuffle"
    for i in range(len(array)-1, 0, -1):
        j = randrange(i+1)
        array[i], array[j] = array[j], array[i]

def get_pens(display):
    pens = []
    for b in range(0, 16*COLOUR_STEP, COLOUR_STEP):
        for g in range(0, 16*COLOUR_STEP, COLOUR_STEP):
            for r in range(0, 16*COLOUR_STEP, COLOUR_STEP):
               pens.append(display.create_pen(r, g, b))
    return pens

def main():
    # Setup for the Presto display
    presto = Presto(full_res=True)
    display = presto.display
    display.clear()
    presto.set_backlight(0.5)
    pens = get_pens(display)
    shuffle(pens)

    SWAP_COUNT = 1

    while True:
        # twiddle neighbouring cells, SWAP_COUNT times
        print(SWAP_COUNT)
        for i in range(0, SWAP_COUNT):
            idx = randint(0, 4096)
            swap = idx - 1

            if swap < 0:
                swap = 4096

            print(i, swap, idx)
            pens[idx], pens[swap] = pens[swap], pens[idx]
        SWAP_COUNT += 1
        if SWAP_COUNT > 256:
            SWAP_COUNT = 1

        # draw pixels
        # (optimisation: only draw the changed ones?)
        for y in range(0, 64):
            for x in range(0, 64):
                display.set_pen(pens[x+64*y])
                display.rectangle(
                    x*PIXEL_SIZE+OFFSET,
                    y*PIXEL_SIZE+OFFSET,
                    PIXEL_SIZE,
                    PIXEL_SIZE,
                )
                # presto.update()
        presto.update()
        time.sleep(TICK)


main()
