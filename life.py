import asyncio
import json
import re
import sys
import time
from random import random

import machine
from presto import Presto


FULL_RES     = False
WIDTH        = 80
HEIGHT       = 80
DEBUG        = True
MAX_CYCLES   = 6 # set 0 to disable cycle detection
FILENAME     = 'tarantula'


class Life:
    def __init__(self):
        self.presto = Presto(full_res=FULL_RES)
        self.display = self.presto.display

        self.wipe()

        self.grid, self.neighbours, self.cycles = self.setup(kind="kaleidosoup")


    ### Presto display handling
    def wipe(self):
        BLACK = self.display.create_pen(0, 0, 0)
        self.display.clear()
        self.presto.update()

    def draw_block(self, display, x, y):
        self.display.pixel(x*3, y*3)
        self.display.pixel(x*3+1, y*3)
        self.display.pixel(x*3+1, y*3+1)
        self.display.pixel(x*3, y*3+1)

    def draw_grid(self, display, grid):
        BLACK = self.display.create_pen(0, 0, 0)
        WHITE = self.display.create_pen(255, 255, 255)

        self.display.set_pen(BLACK)
        self.display.clear()
        self.display.set_pen(WHITE)

        for x in range(WIDTH):
            for y in range(HEIGHT):
                if self.grid[x][y]:
                    self.draw_block(self.display, x, y)

    def change_cell(self, display, x, y, state):
        WHITE = self.display.create_pen(255, 255, 255)
        GREY = self.display.create_pen(51, 51, 51)

        if state:
            self.display.set_pen(WHITE)
        else:
            self.display.set_pen(GREY)
        self.draw_block(self.display, x, y)


    ### Noises
    async def make_sound(self, frequency, duration):
        buzzer = machine.PWM(machine.Pin(43))
        buzzer.freq(frequency)
        buzzer.duty_u16(32000)
        await asyncio.sleep(duration)
        buzzer.duty_u16(0)

    ### Life grid setup
    def initialise_everything(self, width, height, kind, filename='spaceship'):
        if kind == 'soup':
            self.grid = self.initialize_soup(width, height, chance=0.15, border=20)
        if kind == 'kaleidosoup':
            self.grid = self.initialize_kaleidosoup(width, height, chance=0.15, border=5)
        if kind == 'rle':
            try:
                with open(f'{filename}.rle') as f:
                    lines = f.readlines()
                width, height, born, survive, line_data = self.parse_rle(lines)
                x_offset = int((WIDTH - width)/2)
                y_offset = int((HEIGHT - height)/2)
                self.grid = self.build_grid(WIDTH, HEIGHT, line_data, x_offset=x_offset, y_offset=y_offset)
            except Exception as e:
                print(f"Specified filename {filename}.rle which didn't work: {e}")
                raise

        if not self.grid:
            raise Exception(f"Didn't understand kind {kind}")

        neighbours = self.initialize_neighbours(self.grid)
        return (self.grid, neighbours)

    def empty_grid(self, width, height):
        return [[False for _ in range(width)] for _ in range(height)]

    def initialize_soup(self, width, height, chance=0.2, border=0):
        # random starting point ('soup') with an optional border to give it room to grow
        grid = self.empty_grid(width, height)
        for x in range(border, width-border):
            for y in range(border, height-border):
                grid[x][y] = bool(random() < chance)
        return grid

    def initialize_kaleidosoup(self, width, height, chance=0.2, border=0):
        # soup, but four-fold symmetry
        grid = self.empty_grid(width, height)
        for x in range(border, width/2):
            for y in range(border, height/2):
                state = bool(random() < chance)
                grid[x][y] = state
                grid[width-x-1][y] = state
                grid[x][height-y-1] = state
                grid[width-x-1][height-y-1] = state
        return grid

    def initialize_neighbours(self, grid):
        neighbours = [[0 for _ in range(WIDTH)] for _ in range(HEIGHT)]
        for y in range(HEIGHT):
            for x in range(WIDTH):
                neighbours[x][y] = self.count_neighbours(grid, x, y)
        return neighbours

    ### RLE file parsing
    def parse_rle_line(self, line):
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

    def parse_rle(self, lines):
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
        line_data = self.parse_rle_line(lines)
        line_data = [(1, match[1]) if match[0] == '' else (int(match[0]), match[1]) for match in line_data]
        return width, height, born, survive, line_data

    def build_grid(self, width, height, line_data, x_offset=0, y_offset=0):
        grid = self.empty_grid(width, height)
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
    def set_neighbours(self, neighbours, x, y, change):
        cells = [
            (-1, -1), (-1, 0), (-1, 1),
            (0, -1),           (0, 1),
            (1, -1),   (1, 0), (1, 1)
        ]
        for dx, dy in cells:
            if 0 <= x+dx < WIDTH and 0 <= y+dy < HEIGHT:
                neighbours[x+dx][y+dy] += change
        return neighbours

    def count_neighbours(self, grid, x, y):
        cells = [
            (-1, -1), (-1, 0), (-1, 1),
            (0, -1),           (0, 1),
            (1, -1),   (1, 0), (1, 1)
        ]

        neighbours = 0
        for dx, dy in cells:
            nx, ny = x + dx, y + dy
            if 0 <= nx < HEIGHT and 0 <= ny < WIDTH:
                if self.grid[nx][ny]:
                    neighbours += 1
        return neighbours

    async def update_grid(self, display, grid, neighbours):
        new_grid = self.empty_grid(WIDTH, HEIGHT)
        new_neighbours = [[neighbours[x][y] for y in range(HEIGHT)] for x in range(WIDTH)]

        for y in range(HEIGHT):
            for x in range(WIDTH):
                current_cell = self.grid[x][y]
                neighbour_count = neighbours[x][y]
                if not current_cell and not neighbour_count:
                    continue

                if not current_cell and neighbour_count == 3:
                    new_grid[x][y] = True
                    self.change_cell(self.display, x, y, True)
                    new_neighbours = self.set_neighbours(new_neighbours, x, y, +1)

                elif current_cell and neighbour_count not in [2, 3]:
                    new_grid[x][y] = False
                    self.change_cell(self.display, x, y, False)
                    new_neighbours = self.set_neighbours(new_neighbours, x, y, -1)

                elif current_cell:
                    new_grid[x][y] = True

        return new_grid, new_neighbours

    ### New grid setup
    def setup(self, kind="rle", filename=None):
        if DEBUG: print(str(time.ticks_ms())+" - started")

        if kind == 'rle' and not filename:
            filename = FILENAME
        self.grid, self.neighbours = self.initialise_everything(WIDTH, HEIGHT, kind, filename)

        self.draw_grid(self.display, self.grid)
        if DEBUG: print(str(time.ticks_ms())+" - initialized grid, neighbours")

        self.presto.update()

        # capture up to MAX_CYCLES previous grids for comparison
        self.cycles = [self.empty_grid(WIDTH, HEIGHT) for _ in range(MAX_CYCLES)]

        return self.grid, self.neighbours, self.cycles

    async def _app_loop(self, generation=0, cycle_index=0):
        loop = asyncio.get_event_loop()

        while True:
            t = time.ticks_ms()
            self.grid, self.neighbours = await self.update_grid(self.display, self.grid, self.neighbours)
            self.presto.update()

            generation += 1

            if MAX_CYCLES:
                self.cycles[cycle_index] = self.grid

                cycle = False
                for i in range(0, MAX_CYCLES):
                    if i == cycle_index:
                        continue
                    if self.cycles[cycle_index] == self.cycles[i]:
                        print("Reached steady state: "+str(cycle_index)+" matched existing "+str(i)+"; reset in 5s")
                        print("Generation "+str(generation))
                        # await self.make_sound(440, 0.4)
                        self.grid, self.neighbours, self.cycles = self.setup(self.display, "kaleidosoup")
                        # this isn't very neat
                        cycle_index = -1
                        generation = -1
                        break

                cycle_index += 1
                if cycle_index >= MAX_CYCLES:
                    cycle_index = 0

            g = time.ticks_ms() - t
            if DEBUG: print(str(1000/g)+" fps, generation "+str(generation))
            await asyncio.sleep(0)

### Go!
if __name__ == "__main__":
    life = Life()

    asyncio.run(life._app_loop())
