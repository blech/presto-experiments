#!/usr/bin/env python3
#
# runs on a computer to follow the state of the Life grid

import curses
import json
import socket
import struct
import time

MCAST_GRP = '239.255.255.250'
MCAST_PORT = 32301

# UDP
def init_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((MCAST_GRP, MCAST_PORT))
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY
    )
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return s

# curses
def curses_app(stdscr):
    s = init_socket()

    curses.use_default_colors()

    stdscr.clear()
    stdscr.addstr(0, 0, "Listening...")
    stdscr.refresh()

    while True:
        raw, addr = s.recvfrom(100)
        data = json.loads(raw)

        if data['event'] == 'generation':
            stdscr.addstr(2, 0, f"Generation: {data['generation']}")
            if 'alive' in data:
                stdscr.addstr(3, 0, f"Cells alive: {data['alive']}")
            stdscr.addstr(4, 0, f"FPS: {data['fps']}")

        if data['event'] == 'steady_state':
            stdscr.clear()
            stdscr.addstr(0, 0, "Listening...")
            stdscr.addstr(6, 0, f"Previous final generation: {data['generation']}")
            stdscr.addstr(7, 0, f"Cycle index & matched: {data['cycle_index'], data['matched']}")

        if data['event'] == 'init':
            epoch_time = int(time.time())
            with open(f'grid_{epoch_time}.json') as f:
                json.dump(data['grid'], f)

        stdscr.refresh()
    stdscr.getkey()

if __name__ == "__main__":
    curses.wrapper(curses_app)
