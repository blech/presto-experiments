#!/usr/bin/env python3

"""
Watch radar.py's log output from another machine on the same LAN, ncurses style.

    python3 prestoradar/radar_listen_ncurses.py

Same multicast feed as radar_listen.py, but redraws a fixed-height scrolling
pane in place instead of printing one line per datagram -- leave this running
for hours without it flooding the terminal's scrollback. JSON records (from
netlog.emit) are pretty-printed, same as the plain listener.

    q / Ctrl-C   quit
    c            clear the log pane

If nothing shows up: the two machines aren't on the same subnet, or a switch /
AP is dropping multicast. Falling back to the serial console (`mpremote run
...`) always works.
"""

import curses
import json
import os
import socket
import struct
import sys
from collections import deque

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, os.pardir, "lib"))

from settings import LOG_UDP_PORT
from netlog import GROUP            # 239.255.255.250 -- shared with the device

PORT = LOG_UDP_PORT or 32301
MAX_LINES = 2000                    # kept in memory as scrollback; only the
                                     # bottom of the window is ever drawn


def init_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    s.bind(("", PORT))
    mreq = struct.pack("4sl", socket.inet_aton(GROUP), socket.INADDR_ANY)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return s


# write `text` on row `y`, truncated and space-padded to the window width
def _line(stdscr, y, text, attr=0):
    try:
        stdscr.addstr(y, 0, text[:curses.COLS - 1].ljust(curses.COLS - 1), attr)
    except curses.error:      # window too small / mid-resize
        pass


def _format(raw):
    text = raw.decode("utf-8", "replace")
    if text[:1] == "{":
        try:
            return json.dumps(json.loads(text), separators=(", ", ": "))
        except ValueError:
            pass
    return text


def curses_app(stdscr):
    s = init_socket()
    s.settimeout(0.2)          # unblock recvfrom so the loop can poll the keyboard

    curses.curs_set(0)
    curses.use_default_colors()
    has_color = curses.has_colors()
    if has_color:
        curses.init_pair(1, curses.COLOR_RED, -1)      # ERROR lines
    stdscr.nodelay(True)       # getch() returns -1 rather than blocking

    device_ip = None
    count = 0
    lines = deque(maxlen=MAX_LINES)

    while True:
        key = stdscr.getch()
        if key in (ord('q'), ord('Q')):
            break
        if key in (ord('c'), ord('C')):
            lines.clear()

        try:
            raw, addr = s.recvfrom(4096)
        except socket.timeout:
            raw = None
        if raw is not None:
            device_ip = addr[0]
            count += 1
            lines.append(_format(raw))

        stdscr.erase()

        header = "radar_listen -- group %s udp/%d" % (GROUP, PORT)
        if device_ip:
            header += "  from %s  (%d lines)" % (device_ip, count)
        else:
            header += "  waiting for a sender..."
        _line(stdscr, 0, header, curses.A_BOLD)

        body_rows = max(curses.LINES - 3, 0)
        for i, text in enumerate(list(lines)[-body_rows:]):
            attr = curses.color_pair(1) if has_color and "ERROR" in text else 0
            _line(stdscr, 2 + i, text, attr)

        _line(stdscr, curses.LINES - 1, "q quit   c clear")
        stdscr.refresh()


if __name__ == "__main__":
    try:
        curses.wrapper(curses_app)
    except KeyboardInterrupt:
        pass
