import asyncio
import json
import re
import sys
import time
from random import random

from presto import Presto


FULL_RES     = False
WIDTH        = 80
HEIGHT       = 80
DEBUG        = True
MAX_CYCLES   = 3 # set 0 to disable cycle detection
FILENAME     = 'xs108_derived_test'


### Presto display handling
def wipe(presto, display):
    BLACK = display.create_pen(0, 0, 0)
    display.clear()
    presto.update()

def draw_block(display, x, y):
    display.pixel(x*3, y*3)
    display.pixel(x*3+1, y*3)
    display.pixel(x*3+1, y*3+1)
    display.pixel(x*3, y*3+1)

def draw_grid(display, grid):
    BLACK = display.create_pen(0, 0, 0)
    WHITE = display.create_pen(255, 255, 255)

    display.set_pen(BLACK)
    display.clear()
    display.set_pen(WHITE)

    for x in range(WIDTH):
        for y in range(HEIGHT):
            if grid[x][y]:
                draw_block(display, x, y)

def change_cell(display, x, y, state):
    WHITE = display.create_pen(255, 255, 255)
    GREY = display.create_pen(51, 51, 51)

    if state:
        display.set_pen(WHITE)
    else:
        display.set_pen(GREY)
    draw_block(display, x, y)


### Life grid setup
def initialise_everything(width, height, kind, filename='spaceship'):
    if kind == 'soup':
        grid = initialize_soup(width, height, chance=0.15, border=20)
    if kind == 'kaleidosoup':
        grid = initialize_kaleidosoup(width, height, chance=0.15, border=5)
    if kind == 'rle':
        try:
            with open(f'{filename}.rle') as f:
                lines = f.readlines()
            width, height, born, survive, line_data = parse_rle(lines)
            x_offset = int((WIDTH - width)/2)
            y_offset = int((HEIGHT - height)/2)
            grid = build_grid(WIDTH, HEIGHT, line_data, x_offset=x_offset, y_offset=y_offset)
        except Exception as e:
            print(f"Specified filename {filename}.rle which didn't work: {e}")
            raise

    if not grid:
        raise Exception(f"Didn't understand kind {kind}")

    neighbours = initialize_neighbours(grid)
    return (grid, neighbours)

def empty_grid(width, height):
    return [[False for _ in range(width)] for _ in range(height)]

def initialize_soup(width, height, chance=0.2, border=0):
    # random starting point ('soup') with an optional border to give it room to grow
    grid = empty_grid(width, height)
    for x in range(border, width-border):
        for y in range(border, height-border):
            grid[x][y] = bool(random() < chance)
    return grid

def initialize_kaleidosoup(width, height, chance=0.2, border=0):
    # soup, but four-fold symmetry
    grid = empty_grid(width, height)
    for x in range(border, width/2):
        for y in range(border, height/2):
            state = bool(random() < chance)
            grid[x][y] = state
            grid[width-x-1][y] = state
            grid[x][height-y-1] = state
            grid[width-x-1][height-y-1] = state
    return grid

def initialize_neighbours(grid):
    neighbours = [[0 for _ in range(WIDTH)] for _ in range(HEIGHT)]
    for y in range(HEIGHT):
        for x in range(WIDTH):
            neighbours[x][y] = count_neighbours(grid, x, y)
    return neighbours

### RLE file parsing
def parse_rle_line(line):
    pattern = re.compile(r'(\d*)([bo$!])')
    result = []
    pos = 0

    # FIXME: not handling \d$ case
    while pos < len(line):
        match = pattern.search(line[pos:])
        if not match:
            break

        count_str = match.group(1)
        char = match.group(2)
        num = int(count_str) if count_str else 1

        result.append((num, char))
        pos += len(match.group(0))

    return result

def parse_rle(lines):
    header = lines[0]
    lines = lines[1:]
    lines = ''.join(lines).replace('\n', '')
    header_pattern = re.compile(r'x\s?=\s?(\d+).*?y\s?=\s?(\d+).*?B(\d+).*?S(\d+.)')
    header_matches = header_pattern.search(header)
    try:
        born = header_matches.group(3)
        survive = header_matches.group(4)
    # FIXME MicroPython throws a different index matching error here
    except IndexError:
        print("No or improper rule in file; defaulting to B3/S23.")
        born = "3"
        survive = "23"
    width = int(header_matches.group(1))
    height = int(header_matches.group(2))
    line_data = parse_rle_line(lines)
    line_data = [(1, match[1]) if match[0] == '' else (int(match[0]), match[1]) for match in line_data]
    return width, height, born, survive, line_data

def build_grid(width, height, line_data, x_offset=0, y_offset=0):
    grid = empty_grid(width, height)
    x = x_offset
    y = y_offset

    for entry in line_data:
        num, kind = entry
        if kind == 'b':
            x += num
        if kind == 'o':
            for _ in range(num):
                grid[x][y] = True
                x += 1
        if kind == '$':
            x = x_offset
            y += num

    return grid


### Grid calculations and generation handling
def set_neighbours(neighbours, x, y, change):
    cells = [
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),   (1, 0), (1, 1)
    ]
    for dx, dy in cells:
        if 0 <= x+dx < WIDTH and 0 <= y+dy < HEIGHT:
            neighbours[x+dx][y+dy] += change
    return neighbours

def count_neighbours(grid, x, y):
    cells = [
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),   (1, 0), (1, 1)
    ]

    neighbours = 0
    for dx, dy in cells:
        nx, ny = x + dx, y + dy
        if 0 <= nx < HEIGHT and 0 <= ny < WIDTH:
            if grid[nx][ny]:
                neighbours += 1
    return neighbours

async def update_grid(display, grid, neighbours):
    new_grid = empty_grid(WIDTH, HEIGHT)
    new_neighbours = [[neighbours[x][y] for y in range(HEIGHT)] for x in range(WIDTH)]

    for y in range(HEIGHT):
        for x in range(WIDTH):
            current_cell = grid[x][y]
            neighbour_count = neighbours[x][y]
            if not current_cell and not neighbour_count:
                continue

            if not current_cell and neighbour_count == 3:
                new_grid[x][y] = True
                change_cell(display, x, y, True)
                new_neighbours = set_neighbours(new_neighbours, x, y, +1)

            elif current_cell and neighbour_count not in [2, 3]:
                new_grid[x][y] = False
                change_cell(display, x, y, False)
                new_neighbours = set_neighbours(new_neighbours, x, y, -1)

            elif current_cell:
                new_grid[x][y] = True

    return new_grid, new_neighbours

### New grid setup
def setup(presto, display, kind="rle", filename=None):
    if DEBUG: print(str(time.ticks_ms())+" - started")

    if kind == 'rle' and not filename:
        filename = FILENAME
    grid, neighbours = initialise_everything(WIDTH, HEIGHT, kind, filename)

    draw_grid(display, grid)
    if DEBUG: print(str(time.ticks_ms())+" - initialized grid, neighbours")

    presto.update()

    # capture up to MAX_CYCLES previous grids for comparison
    cycles = [empty_grid(WIDTH, HEIGHT) for _ in range(MAX_CYCLES)]

    return grid, neighbours, cycles

async def _app_loop(presto, display, grid, neighbours, cycles, generation=0, cycle_index=0):
    loop = asyncio.get_event_loop()

    while True:
        t = time.ticks_ms()
        grid, neighbours = await update_grid(display, grid, neighbours)
        presto.update()

        generation += 1

        if MAX_CYCLES:
            cycles[cycle_index] = grid

            cycle = False
            for i in range(0, MAX_CYCLES):
                if i == cycle_index:
                    continue
                if cycles[cycle_index] == cycles[i]:
                    print("Reached steady state: "+str(cycle_index)+" matched existing "+str(i)+"; reset in 5s")
                    print("Generation "+str(generation))
                    grid, neighbours, cycles = setup(presto, display, "kaleidosoup")
                    # this isn't very neat
                    cycle_index = -1
                    generation = -1

            cycle_index += 1
            if cycle_index >= MAX_CYCLES:
                cycle_index = 0

        g = time.ticks_ms() - t
        if DEBUG: print(str(1000/g)+" fps, generation "+str(generation))
        await asyncio.sleep(0)

### Go!
if __name__ == "__main__":
    presto = Presto(full_res=FULL_RES)
    display = presto.display

    wipe(presto, display)

    grid, neighbours, cycles = setup(presto, display, kind="kaleidosoup")

    asyncio.run(_app_loop(presto, display, grid, neighbours, cycles))
