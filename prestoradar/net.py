import asyncio
import ssl


async def http_get(host, path, user_agent, port=443, timeout=15):
    """Minimal async HTTPS GET. Returns (status:int, body:bytes). The socket I/O
    is non-blocking, so the caller's animation loop keeps running during the
    transfer; only the TLS handshake (~0.3 s) and any json.loads afterwards
    still hitch. No cert check -- urequests didn't verify either, and there's
    no CA bundle on the device."""
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = ssl.CERT_NONE
    except Exception:  # noqa: BLE001 -- older ssl module: fall back to a plain flag
        ctx = True

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ctx), timeout)
    try:
        writer.write(("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\n"
                      "Connection: close\r\n\r\n" % (path, host, user_agent)).encode())
        await writer.drain()

        status = int((await asyncio.wait_for(reader.readline(), timeout)).split()[1])
        clen = None
        chunked = False
        while True:
            h = await asyncio.wait_for(reader.readline(), timeout)
            if h in (b"\r\n", b"\n", b""):
                break
            hl = h.lower()
            if hl.startswith(b"content-length:"):
                clen = int(h.split(b":", 1)[1])
            elif hl.startswith(b"transfer-encoding:") and b"chunked" in hl:
                chunked = True

        parts = []
        if chunked:
            while True:
                n = int((await reader.readline()).strip() or b"0", 16)
                if n == 0:
                    await reader.readline()
                    break
                got = 0
                while got < n:
                    b = await reader.read(min(2048, n - got))
                    if not b:
                        break
                    parts.append(b)
                    got += len(b)
                await reader.readline()  # chunk trailing CRLF
        else:
            want = clen if clen is not None else (1 << 30)
            got = 0
            while got < want:
                b = await reader.read(min(2048, want - got))
                if not b:
                    break
                parts.append(b)
                got += len(b)
        return status, b"".join(parts)
    finally:
        writer.close()
        await writer.wait_closed()
