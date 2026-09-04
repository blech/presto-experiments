#!/usr/bin/env python3

"""
Watch radar.py's log output from another machine on the same LAN.

    python3 prestoradar/radar_listen.py

radar.py sends every log() line as a UDP multicast datagram (via lib/netlog.py)
once its WiFi is up. This joins that group and prints whatever arrives, prefixed
with the sender's IP; JSON records (from netlog.emit) are pretty-printed. Ctrl-C
to stop.

If nothing shows up: the two machines aren't on the same subnet, or a switch /
AP is dropping multicast. Falling back to the serial console (`mpremote run
...`) always works.
"""

import json
import os
import socket
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, os.pardir, "lib"))

from settings import LOG_UDP_PORT
from netlog import GROUP            # 239.255.255.250 -- shared with the device

PORT = LOG_UDP_PORT or 32301


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    s.bind(("", PORT))
    mreq = struct.pack("4sl", socket.inet_aton(GROUP), socket.INADDR_ANY)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    print("joined %s udp/%d (Ctrl-C to stop)" % (GROUP, PORT), file=sys.stderr)

    while True:
        data, addr = s.recvfrom(4096)
        text = data.decode("utf-8", "replace")
        if text[:1] == "{":
            try:
                text = json.dumps(json.loads(text), separators=(", ", ": "))
            except ValueError:
                pass
        print("%-15s %s" % (addr[0], text), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
