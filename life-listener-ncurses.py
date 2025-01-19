#!/usr/bin/env python3
#
# runs on a computer to follow the state of the Life grid

import curses
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

    curses.use_default_colors()

    stdscr.clear()
    stdscr.addstr(0, 0, "listening")
    stdscr.refresh()

    while True:
        data, addr = s.recvfrom(100)
        stdscr.addstr(0, 0, "listening")

        stdscr.addstr(2, 0, data.decode('utf-8'))

        stdscr.refresh()
    stdscr.getkey()

if __name__ == "__main__":
    curses.wrapper(curses_app)
