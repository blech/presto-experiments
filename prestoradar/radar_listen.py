"""
Watch radar.py's log output from another machine on the same LAN.

    python3 prestoradar/radar_listen.py

radar.py broadcasts every log() line as a UDP packet to 255.255.255.255:47269
once its WiFi is up. This binds that port and prints whatever arrives, prefixed
with the sender's IP. Ctrl-C to stop.

If nothing shows up: the two machines aren't on the same broadcast domain, or a
firewall / AP-isolation is dropping broadcast packets. Falling back to the
serial console (`mpremote run ...`) always works.
"""

import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import LOG_UDP_PORT

PORT = LOG_UDP_PORT


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    s.bind(("", PORT))
    print("listening on udp/%d (Ctrl-C to stop)" % PORT, file=sys.stderr)
    while True:
        data, addr = s.recvfrom(2048)
        print("%-15s %s" % (addr[0], data.decode("utf-8", "replace")), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
