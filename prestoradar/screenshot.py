"""
Screenshots of the radar, pushed over TCP.

Writing the framebuffer to flash deadlocks on the current Presto firmware (see
badgeware-presto's FLASH_WRITES_HANG), so instead the radar runs a tiny TCP
server: connect to it and it replies with a one-line header then the raw RGB565
front buffer. The host does the RGB565 -> PNG conversion.

    radar.py:  screenshot.serve_init(PORT)          once, after WiFi is up
               screenshot.serve_poll(display, presto.presto)   every frame

    host:      python3 prestoradar/screenshot_pull.py <presto-ip>

Wire format:  b"<w>x<h> <nbytes>\\n"  then  <nbytes> of RGB565 (little-endian),
rows top-to-bottom.

`save()` (framebuffer -> BMP on flash) is kept for use on a firmware where flash
writes work; it is a no-op hazard on this one.
"""

import socket
import struct
import time

_srv = None


def serve_init(port):
    """Open the non-blocking listening socket. Safe to call once WiFi is up.
    Best-effort: any failure (or a falsy port) just leaves the server off."""
    global _srv
    if not port:
        print("screenshot: server disabled (SCREENSHOT_PORT is 0)")
        return
    try:
        # Resolve the bind address the way the rest of this ecosystem does --
        # passing a plain ("0.0.0.0", port) tuple to bind() is unreliable on
        # MicroPython's lwIP sockets.
        addr = socket.getaddrinfo("0.0.0.0", port)[0][-1]
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(addr)
        s.listen(1)
        s.setblocking(False)
        _srv = s
        print("screenshot: server on tcp/%d" % port)
    except Exception as e:  # noqa: BLE001
        _srv = None
        print("screenshot: serve_init failed:", repr(e))


def serve_poll(display, framebuffer_owner):
    """Call once per frame. Serves one waiting client, if any; else returns fast."""
    if _srv is None:
        return
    try:
        conn, addr = _srv.accept()
    except OSError:
        return  # EAGAIN -- nobody waiting

    try:
        conn.settimeout(10)
        w, h = display.get_bounds()
        fb = memoryview(framebuffer_owner)
        n = w * h * 2
        conn.send(b"%dx%d %d\n" % (w, h, n))
        t = time.ticks_ms()
        mv = fb[:n]
        sent = 0
        while sent < n:
            sent += conn.send(mv[sent:sent + 4096])
        print("screenshot: sent %d bytes to %s in %d ms"
              % (n, addr[0], time.ticks_diff(time.ticks_ms(), t)))
    except Exception as e:  # noqa: BLE001
        print("screenshot: send failed:", repr(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save(display, framebuffer_owner, path="/shot.bmp"):
    """Framebuffer -> 24-bit BMP on flash. Deadlocks on firmware with the flash
    write bug -- use the TCP server instead there."""
    w, h = display.get_bounds()
    fb = memoryview(framebuffer_owner)
    if len(fb) < w * h * 2:
        print("screenshot: wrong display mode? aborting")
        return
    row_bytes = w * 3
    pad = (4 - row_bytes % 4) % 4
    stride = row_bytes + pad
    img_size = stride * h
    body = bytearray(img_size)
    for y in range(h):
        src = (h - 1 - y) * w * 2               # BMP rows run bottom-up
        o = y * stride
        for x in range(w):
            i = src + x * 2
            px = fb[i] | (fb[i + 1] << 8)
            r = (px >> 11) & 0x1F
            g = (px >> 5) & 0x3F
            b = px & 0x1F
            body[o] = (b << 3) | (b >> 2)
            body[o + 1] = (g << 2) | (g >> 4)
            body[o + 2] = (r << 3) | (r >> 2)
            o += 3
    with open(path, "wb") as f:
        f.write(b"BM")
        f.write(struct.pack("<IHHI", 14 + 40 + img_size, 0, 0, 54))
        f.write(struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, img_size,
                            2835, 2835, 0, 0))
        f.write(body)
    print("screenshot: wrote", path)
