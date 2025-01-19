import asyncio
import json
import re
import socket
import sys
import time
from random import random

import machine
import network
from presto import Presto


FULL_RES    = False
WIDTH       = 80
HEIGHT      = 80
DEBUG       = False
MAX_CYCLES  = 6 # set 0 to disable cycle detection
FILENAME    = 'boss-synthesis'
LOG_COUNT   = True

MCAST_GRP   = '239.255.255.250'
MCAST_PORT  = 32301


class Life:
    def __init__(self):
        self.presto = Presto(full_res=FULL_RES)
        self.display = self.presto.display
        self.wipe()

        # canvas (it should be possible to calculate this)
        # (but I need to fix up draw_block first)
        self.width = WIDTH
        self.height = HEIGHT

        # rules
        self.born = [3]
        self.survive = [2, 3]

        self.socket = False
        self.socket_setup_task =  asyncio.create_task(self.setup_socket())

        self.start_tick = 0
        self.end_tick = 0
        self.generation = 0
        self.cycle_index = 0


    ### UDP setup
    async def setup_socket(self):

        self.presto.connect()
        wlan = network.WLAN(network.STA_IF)
        host = wlan.ifconfig()[0]
        addr = socket.getaddrinfo(host, MCAST_PORT)[0][-1]

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(addr)
        self.socket = s

    async def send_start(self):
        if not self.socket:
            return

        info = {
            'event': 'start',
        }
        self.socket.sendto(json.dumps(info), (MCAST_GRP, MCAST_PORT))

    async def send_generation(self):
        if not self.socket:
            return
        duration = self.end_tick - self.start_tick
        fps_raw = 1000/duration
        fps = f"{fps_raw:.2f}"

        info = {
            'event': 'generation',
            'fps': fps,
            'fps_raw': fps_raw,
            'generation': self.generation,
        }

        if LOG_COUNT:
            info['alive'] = sum([sum([cell for cell in row]) for row in self.grid])
        self.socket.sendto(json.dumps(info), (MCAST_GRP, MCAST_PORT))

    async def send_steady_state(self, matched: int=None):
        if not self.socket:
            return

        info = {
            'event': 'steady_state',
            'generation': self.generation,
        }
        if matched:
            info['cycle_index'] = self.cycle_index
            info['matched'] = matched
        self.socket.sendto(json.dumps(info), (MCAST_GRP, MCAST_PORT))


    ### New grid setup
    def setup(self, kind="rle", filename=None):
        if DEBUG:
            print(str(time.ticks_ms())+" - started")

        if kind == 'rle' and not filename:
            filename = FILENAME
        self.grid, self.neighbours = self.initialise_everything(kind, filename)

        self.draw_grid()
        if DEBUG:
            print(str(time.ticks_ms())+" - initialized grid, neighbours")

        self.presto.update()

        # capture up to MAX_CYCLES previous grids for comparison
        self.cycles = [self.empty_grid() for _ in range(MAX_CYCLES)]


    ### Presto display handling
    def wipe(self):
        BLACK = self.display.create_pen(0, 0, 0)
        self.display.clear()
        self.presto.update()

    def draw_block(self, x, y):
        self.display.pixel(x*3, y*3)
        self.display.pixel(x*3+1, y*3)
        self.display.pixel(x*3+1, y*3+1)
        self.display.pixel(x*3, y*3+1)

    def draw_grid(self):
        BLACK = self.display.create_pen(0, 0, 0)
        WHITE = self.display.create_pen(255, 255, 255)

        self.display.set_pen(BLACK)
        self.display.clear()
        self.display.set_pen(WHITE)

        for x in range(self.width):
            for y in range(self.height):
                if self.grid[x][y]:
                    self.draw_block(x, y)

    def change_cell(self, x, y, state):
        WHITE = self.display.create_pen(255, 255, 255)
        GREY = self.display.create_pen(51, 51, 51)

        if state:
            self.display.set_pen(WHITE)
        else:
            self.display.set_pen(GREY)
        self.draw_block(x, y)


    ### Noises
    async def make_sound(self, frequency, duration):
        buzzer = machine.PWM(machine.Pin(43))
        buzzer.freq(frequency)
        buzzer.duty_u16(32000)
        await asyncio.sleep(duration)
        buzzer.duty_u16(0)


    ### Life grid setup
    def initialise_everything(self, kind, filename='spaceship'):
        if kind == 'soup':
            grid = self.initialize_soup(chance=0.15, border=20)
        if kind == 'kaleidosoup':
            grid = self.initialize_kaleidosoup(chance=0.15, border=5)
        if kind == 'rle':
            try:
                with open(f'life-rles/{filename}.rle') as f:
                    lines = f.readlines()
                width, height, born, survive, line_data = self.parse_rle(lines)
                x_offset = int((self.width - width)/2)
                y_offset = int((self.height - height)/2)
                grid = self.build_grid(line_data, x_offset=x_offset, y_offset=y_offset)
            except Exception as e:
                print(f"Specified filename {filename}.rle which didn't work: {e}")
                raise

        if not grid:
            raise Exception(f"Didn't understand kind {kind}")

        neighbours = self.initialize_neighbours(grid)
        return (grid, neighbours)

    def empty_grid(self):
        return [[False for _ in range(self.width)] for _ in range(self.height)]

    def initialize_soup(self, chance=0.2, border=0):
        # random starting point ('soup') with an optional border to give it room to grow
        grid = self.empty_grid()
        for x in range(border, self.width-border):
            for y in range(border, self.height-border):
                grid[x][y] = bool(random() < chance)
        return grid

    def initialize_kaleidosoup(self, chance=0.2, border=0):
        # soup, but four-fold symmetry
        grid = self.empty_grid()
        for x in range(border, self.width/2):
            for y in range(border, self.height/2):
                state = bool(random() < chance)
                grid[x][y] = state
                grid[self.width-x-1][y] = state
                grid[x][self.height-y-1] = state
                grid[self.width-x-1][self.height-y-1] = state
        return grid

    def initialize_neighbours(self, grid):
        neighbours = [[0 for _ in range(self.width)] for _ in range(self.height)]
        for y in range(self.height):
            for x in range(self.width):
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

    def build_grid(self, line_data, x_offset=0, y_offset=0):
        grid = self.empty_grid()
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
            if 0 <= x+dx < self.width and 0 <= y+dy < self.height:
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
            if 0 <= nx < self.height and 0 <= ny < self.width:
                if grid[nx][ny]:
                    neighbours += 1
        return neighbours

    async def update_grid(self):
        new_grid = self.empty_grid()
        new_neighbours = [[self.neighbours[x][y] for y in range(self.height)] for x in range(self.width)]

        for y in range(self.height):
            for x in range(self.width):
                current_cell = self.grid[x][y]
                neighbour_count = self.neighbours[x][y]
                if not current_cell and not neighbour_count:
                    continue

                if not current_cell and neighbour_count in self.born:
                    new_grid[x][y] = True
                    self.change_cell(x, y, True)
                    new_neighbours = self.set_neighbours(new_neighbours, x, y, +1)

                elif current_cell and neighbour_count not in self.survive:
                    new_grid[x][y] = False
                    self.change_cell(x, y, False)
                    new_neighbours = self.set_neighbours(new_neighbours, x, y, -1)

                elif current_cell:
                    new_grid[x][y] = True

        self.generation += 1

        self.grid = new_grid
        self.neighbours = new_neighbours

    async def handle_cycles(self):
        self.cycles[self.cycle_index] = self.grid

        # detect cycles if not already in a steady state countdown
        if not self.countdown:
            cycle = False
            for i in range(0, MAX_CYCLES):
                if i == self.cycle_index:
                    continue
                if self.grid == self.cycles[i]:
                    cycle = True
                    break

            if cycle:
                self.countdown = 10
                self.matched_index = i
                await self.send_steady_state(matched=self.matched_index)
            else:
                self.cycle_index += 1
                if self.cycle_index >= MAX_CYCLES:
                    self.cycle_index = 0

        # count down to reset
        if self.countdown:
            self.countdown -= 1
            if not self.countdown:
                await self.send_steady_state(matched=self.matched_index)
                # await self.make_sound(440, 0.4)
                self.setup(kind="kaleidosoup")


    ### New grid setup
    def setup(self, kind="rle", filename=None):
        if DEBUG:
            print(str(time.ticks_ms())+" - started")

        if kind == 'rle' and not filename:
            filename = FILENAME
        self.grid, self.neighbours = self.initialise_everything(kind, filename)

        self.draw_grid()
        if DEBUG:
            print(str(time.ticks_ms())+" - initialized grid, neighbours")

        self.presto.update()

        # capture up to MAX_CYCLES previous grids for comparison
        self.cycles = [self.empty_grid() for _ in range(MAX_CYCLES)]
        self.cycle_index = 0
        self.generation = 0

    async def _app_loop(self):
        loop = asyncio.get_event_loop()
        self.countdown = 0

        while True:
            self.start_tick = time.ticks_ms()
            await self.update_grid()
            self.presto.update()

            if MAX_CYCLES:
                await self.handle_cycles()
            self.end_tick = time.ticks_ms()

            await self.send_generation()
            await asyncio.sleep(0)


### Go!
if __name__ == "__main__":
    life = Life()
    life.setup(kind='rle', filename='blinkers')

    asyncio.run(life._app_loop())
