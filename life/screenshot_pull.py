"""
Pull a screenshot from the running Game of Life over TCP and save a PNG.

    python3 life/screenshot_pull.py <presto-ip> [-o shot.png] [--bmp]

life.py prints its IP at startup ("screenshot: pull with ... <ip>"). It must be
running with the TCP screenshot server (screenshot.serve_init); the port is
life.py's SCREENSHOT_PORT, mirrored by SCREENSHOT_PORT below.

The device sends  b"<w>x<h> <nbytes>\\n"  then <nbytes> of RGB565, rows
top-to-bottom. The Presto framebuffer is big-endian RGB565 (high byte first --
same as compresto's byteswap and the presto repo's convert-image-rgb565.py). We
expand to RGB888, write a 24-bit BMP, and (unless --bmp) convert to PNG with
`sips` (macOS).
"""

import argparse
import os
import socket
import struct
import subprocess
import sys

SCREENSHOT_PORT = 8011  # keep in step with life/life.py


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("-o", "--output", default="shot.png")
    ap.add_argument("--port", type=int, default=SCREENSHOT_PORT)
    ap.add_argument("--bmp", action="store_true", help="keep the BMP, skip the PNG step")
    args = ap.parse_args()

    with socket.create_connection((args.host, args.port), timeout=10) as s:
        f = s.makefile("rb")
        w, h, n = _parse_header(f.readline())
        data = f.read(n)
    if len(data) != n:
        sys.exit("short read: got %d of %d bytes" % (len(data), n))

    bmp_path = os.path.splitext(args.output)[0] + ".bmp"
    _write_bmp(bmp_path, data, w, h)
    if args.bmp:
        print("wrote %s (%dx%d)" % (bmp_path, w, h))
        return
    subprocess.run(["sips", "-s", "format", "png", bmp_path, "--out", args.output],
                   check=True, stdout=subprocess.DEVNULL)
    os.remove(bmp_path)
    print("wrote %s (%dx%d)" % (args.output, w, h))


def _parse_header(line):
    dims, nbytes = line.decode("ascii").split()
    w, h = (int(v) for v in dims.split("x"))
    return w, h, int(nbytes)


def _write_bmp(path, rgb565, w, h):
    row = w * 3
    pad = (4 - row % 4) % 4
    stride = row + pad
    img = stride * h
    out = bytearray(54 + img)
    out[0:2] = b"BM"
    struct.pack_into("<IHHI", out, 2, 54 + img, 0, 0, 54)
    struct.pack_into("<IiiHHIIiiII", out, 14, 40, w, h, 1, 24, 0, img, 2835, 2835, 0, 0)
    p = 54
    for y in range(h - 1, -1, -1):             # BMP bottom-up; framebuffer top-down
        base = y * w * 2
        for x in range(w):
            px = (rgb565[base + x * 2] << 8) | rgb565[base + x * 2 + 1]  # big-endian
            r = (px >> 11) & 0x1F
            g = (px >> 5) & 0x3F
            b = px & 0x1F
            out[p] = (b << 3) | (b >> 2)        # BGR
            out[p + 1] = (g << 2) | (g >> 4)
            out[p + 2] = (r << 3) | (r >> 2)
            p += 3
        p += pad
    with open(path, "wb") as fh:
        fh.write(out)


if __name__ == "__main__":
    main()
