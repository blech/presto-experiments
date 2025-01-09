import json
import re
import sys
import time
from random import random

from presto import Presto

FULL_RES     = False
WIDTH        = 80
HEIGHT       = 80
DEBUG        = False


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
def initialise_everything(width, height, kind):
    if kind == 'soup':
        grid = initialize_soup(width, height, border=20)
    if kind == 'spaceship':
        with open('spaceship.rle') as f:
            lines = f.readlines()
        width, height, born, survive, line_data = parse_rle(lines)
        x_offset = int((WIDTH - width)/2)
        y_offset = int((HEIGHT - height)/2)
        grid = build_grid(WIDTH, HEIGHT, line_data, x_offset=x_offset, y_offset=y_offset)

    neighbours = initialize_neighbours(grid)
    return (grid, neighbours)

def initialize_soup(width, height, chance=0.2, border=0):
    if border:
        grid = [[False for _ in range(width)] for _ in range(height)]
        for x in range(border, width-border):
            for y in range(border, height-border):
                grid[x][y] = bool(random() < chance)
        return grid
    return [[bool(random() < chance) for _ in range(width)] for _ in range(height)]

def initialize_neighbours(grid):
    neighbours = [[0 for _ in range(WIDTH)] for _ in range(HEIGHT)]
    for y, row in enumerate(grid):
        for x, state in enumerate(row):
            neighbours[x][y] = count_neighbours(grid, x, y)
    return neighbours

### RLE file parsing (possibly broken?)
def parse_rle_line(line):
    pattern = re.compile(r'(\d*)([bo$!])')
    result = []
    pos = 0

    while pos < len(line):
        match = pattern.search(line[pos:])
        if not match:
            break

        count_str = match.group(1)
        char = match.group(2)
        count = int(count_str) if count_str else 1

        result.append((count, char))
        pos += len(match.group(0))

    return result

def parse_rle(lines):
    header = lines[0]
    lines = lines[1:]
    lines = ''.join(lines).strip('\n')
    header_pattern = re.compile(r'x\s?=\s?(\d+).*?y\s?=\s?(\d+).*?B(\d+).*?S(\d+.)')
    header_matches = header_pattern.search(header)
    try:
        born = header_matches.group(3)
        survive = header_matches.group(4)
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
    grid = [[False for _ in range(width)] for _ in range(height)]
    x = x_offset; y = y_offset

    for entry in line_data:
        count, kind = entry
        if kind == 'b':
            x += count
        if kind == 'o':
            for _ in range(count):
                grid[y][x] = True
                x += 1
        if kind == '$':
            x = x_offset
            y += 1

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

    cnt = 0
    for dx, dy in cells:
        nx, ny = x + dx, y + dy
        if 0 <= nx < HEIGHT and 0 <= ny < WIDTH:
            if grid[nx][ny]:
                cnt += 1
    return cnt

def update_grid(display, grid, neighbours):
    new_grid = [[False for _ in range(WIDTH)] for _ in range(HEIGHT)]
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

### Main loop
def main():
    presto = Presto(full_res=FULL_RES)

    display = presto.display
    wipe(presto, display)

    if DEBUG: print(str(time.ticks_ms())+" - display wiped")

    grid, neighbours = initialise_everything(WIDTH, HEIGHT, 'soup')
    draw_grid(display, grid)
    if DEBUG: print(str(time.ticks_ms())+" - initialized grid, neighbours")

    presto.update()

    # FIXME detect n cycles up to arbitrary max n
    two_ago = []
    one_ago = []

    while True:
        t = time.ticks_ms()

        two_ago = one_ago
        one_ago = grid

        grid, neighbours = update_grid(display, grid, neighbours)
        presto.update()
        g = time.ticks_ms() - t
        if DEBUG:  print(str(1000/g)+" fps")
        if one_ago == grid or two_ago == grid:
            print("Reached steady state; reset in 5s")
            time.sleep(5)
            one_ago = two_ago = []
            grid, neighbours = initialise_everything(WIDTH, HEIGHT, 'soup')
            draw_grid(display, grid)
            presto.update()

if __name__ == "__main__":
    main()
