#!/usr/bin/env python3
#
# runs on a computer to follow the state of the Life grid.
#   s  save a screenshot to life/life-<timestamp>.png (pulled from the device
#      over TCP; the device must be running with SCREENSHOT_PORT enabled)
#   q / Ctrl-C        quit

import curses
import json
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screenshot_pull import pull

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

# write `text` on row `y`, truncated and space-padded to the window width
def _line(stdscr, y, text):
    try:
        stdscr.addstr(y, 0, text[:curses.COLS - 1].ljust(curses.COLS - 1))
    except curses.error:      # window too small / mid-resize
        pass


def _screenshot(stdscr, device_ip):
    name = time.strftime("life-%Y%m%d-%H%M%S.png")
    if not device_ip:
        return "screenshot: no device seen yet (waiting for telemetry)"
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    _line(stdscr, curses.LINES - 2, f"screenshot: pulling from {device_ip} ...")
    stdscr.refresh()
    try:
        path, w, h = pull(device_ip, out)
    except Exception as e:  # noqa: BLE001 -- keep the UI alive on any failure
        return f"screenshot: FAILED ({e})"
    return f"screenshot: saved life/{os.path.basename(path)} ({w}x{h})"


# curses
def curses_app(stdscr):
    s = init_socket()
    s.settimeout(0.5)          # unblock recvfrom so the loop can poll the keyboard

    curses.use_default_colors()
    stdscr.nodelay(True)       # getch() returns -1 rather than blocking

    device_ip = None
    shot_status = ""

    stdscr.clear()
    stdscr.addstr(0, 0, "Listening...")
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key in (ord('q'), ord('Q')):
            break
        if key in (ord('s'), ord('S')):
            shot_status = _screenshot(stdscr, device_ip)

        try:
            raw, addr = s.recvfrom(100)
        except socket.timeout:
            raw = None
        if raw is not None:
            device_ip = addr[0]
            data = json.loads(raw)

            if data['event'] == 'start':
                stdscr.clear()
                stdscr.addstr(0, 0, "Listening...")

            # reminding myself - these extra spaces ensure that new lines don't leave junk
            if data['event'] == 'generation':
                stdscr.addstr(2, 0, f"Generation: {data['generation']}       ")
                if 'alive' in data:
                    stdscr.addstr(3, 0, f"Cells alive: {data['alive']} / 6400 ({data['alive']/64.0:.1f}%)       ")
                stdscr.addstr(4, 0, f"FPS: {data['fps']}")

            if data['event'] == 'steady_state':
                stdscr.clear()
                stdscr.addstr(0, 0, "Listening...")

                stdscr.addstr(6, 0, f"Previous final generation: {data['generation']}")
                if 'cycle_index' in data and 'matched' in data:
                    stdscr.addstr(7, 0, f"Cycle index & matched: {data['cycle_index'], data['matched']}")

        _line(stdscr, curses.LINES - 2, shot_status)
        _line(stdscr, curses.LINES - 1, "s screenshot   q quit")
        stdscr.refresh()

if __name__ == "__main__":
    try:
        curses.wrapper(curses_app)
    except KeyboardInterrupt:
        pass
