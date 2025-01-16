#!/usr/bin/env python3
#
# runs on a computer to follow the state of the Life grid

import socket
import struct

MCAST_GRP = '239.255.255.250'
MCAST_PORT = 32301

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((MCAST_GRP, MCAST_PORT))
mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY
)
s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

try:
    while True:
        data, addr = s.recvfrom(100)
        print(data.decode('utf-8'))
except KeyboardInterrupt:
    pass

