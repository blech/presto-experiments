"""
Save the Presto framebuffer to a BMP on flash -- real screenshots of the radar.

    import screenshot
    screenshot.save(display, presto.presto)      # write /shot.bmp right now
    screenshot.request()                         # write it after the next frame

then on the host:

    mpremote fs cp :shot.bmp .
    sips -s format png shot.bmp --out shot.png   # macOS; view shot.png

radar.py runs full_res, where `presto.presto` exposes the 480x480 RGB565 front
buffer through the buffer protocol (see the Presto firmware's
Presto_get_framebuffer). We expand RGB565 -> RGB888 and write a 24-bit,
bottom-up BMP, which `sips` reads directly. The expansion is a Python loop over
~230k pixels, so a full-res shot takes a few seconds and stalls the frame loop
while it writes -- fine for an occasional capture.
"""

import struct
import time

_pending = None  # path set by request(), consumed by service()


def request(path="/shot.bmp"):
    """Arm a capture for the next service() call (avoids a half-drawn frame)."""
    global _pending
    _pending = path
    print("screenshot: armed ->", path)


def service(display, framebuffer_owner):
    """Call once per frame, just after presto.update()."""
    global _pending
    if _pending is None:
        return
    path, _pending = _pending, None
    try:
        save(display, framebuffer_owner, path)
    except Exception as exc:  # noqa: BLE001
        print("screenshot: failed -", exc)


def save(display, framebuffer_owner, path="/shot.bmp"):
    w, h = display.get_bounds()
    fb = memoryview(framebuffer_owner)
    need = w * h * 2
    print("screenshot: framebuffer", len(fb), "bytes (need", need, ")")
    if len(fb) < need:
        print("screenshot: wrong display mode? aborting")
        return

    row_bytes = w * 3
    pad = (4 - row_bytes % 4) % 4
    stride = row_bytes + pad
    img_size = stride * h

    # Expand RGB565 -> RGB888 into a RAM buffer first, then write it in one go.
    # Keeping the two phases separate makes a slow expansion easy to tell apart
    # from a flash-write that hangs (some Presto firmware deadlocks on those --
    # see badgeware-presto's FLASH_WRITES_HANG).
    t = time.ticks_ms()
    body = bytearray(img_size)
    for y in range(h):
        src = (h - 1 - y) * w * 2               # BMP rows run bottom-up
        o = y * stride
        for x in range(w):
            i = src + x * 2
            px = fb[i] | (fb[i + 1] << 8)       # RGB565, little-endian
            r = (px >> 11) & 0x1F
            g = (px >> 5) & 0x3F
            b = px & 0x1F
            body[o] = (b << 3) | (b >> 2)       # BMP pixels are BGR
            body[o + 1] = (g << 2) | (g >> 4)
            body[o + 2] = (r << 3) | (r >> 2)
            o += 3
        if y % 80 == 0:
            print("screenshot: expand row", y, "/", h)
    print("screenshot: expanded in", time.ticks_diff(time.ticks_ms(), t),
          "ms; opening", path)

    t = time.ticks_ms()
    with open(path, "wb") as f:
        f.write(b"BM")
        f.write(struct.pack("<IHHI", 14 + 40 + img_size, 0, 0, 54))
        f.write(struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, img_size,
                            2835, 2835, 0, 0))
        print("screenshot: header written, writing", img_size, "bytes of pixels")
        f.write(body)
    print("screenshot: wrote {} ({}x{}) in {} ms.  host:  mpremote fs cp :{} .".format(
        path, w, h, time.ticks_diff(time.ticks_ms(), t), path.lstrip("/")))
