#!/usr/bin/env python3
#
# runs on a computer to follow the state of the Life grid

import curses
import json
import socket
import struct

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
    s.settimeout(0.5)          # unblock recvfrom so the loop can poll the keyboard

    curses.use_default_colors()
    stdscr.nodelay(True)       # getch() returns -1 rather than blocking

    stdscr.clear()
    stdscr.addstr(0, 0, "Listening...  (any key or Ctrl-C to quit)")
    stdscr.refresh()

    while True:
        if stdscr.getch() != -1:      # any key -> quit
            break

        try:
            raw, addr = s.recvfrom(100)
        except socket.timeout:
            continue
        data = json.loads(raw)

        if data['event'] == 'start':
            stdscr.clear()
            stdscr.addstr(0, 0, "Listening...")

        if data['event'] == 'generation':
            stdscr.addstr(2, 0, f"Generation: {data['generation']}       ")
            if 'alive' in data:
                stdscr.addstr(3, 0, f"Cells alive: {data['alive']} / 6400       ")
            stdscr.addstr(4, 0, f"FPS: {data['fps']}")

        if data['event'] == 'steady_state':
            stdscr.clear()
            stdscr.addstr(0, 0, "Listening...")

            stdscr.addstr(6, 0, f"Previous final generation: {data['generation']}")
            if 'cycle_index' in data and 'matched' in data:
                stdscr.addstr(7, 0, f"Cycle index & matched: {data['cycle_index'], data['matched']}")

        stdscr.addstr(curses.LINES - 1, 0, "any key or Ctrl-C to quit")
        stdscr.refresh()

if __name__ == "__main__":
    try:
        curses.wrapper(curses_app)
    except KeyboardInterrupt:
        pass
