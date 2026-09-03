# netlog -- best-effort UDP telemetry shared by the Presto apps.
#
# One multicast socket, two payload styles:
#
#   netlog.log("fetch done:", n, "planes")   -> "[   12.34] fetch done: 3 planes"
#   netlog.emit("generation", fps=fps, alive=count)  -> {"event": "generation", ...}
#
# `log()` is the radar family's timestamped text line; `emit()` is the life
# family's structured JSON event. A desktop watcher joins the group and prints
# text lines as-is / pretty-prints the JSON (see tools/udplisten.py, planned).
#
# Multicast (239.255.255.250:32301, the address life.py already used) rather
# than 255.255.255.255 broadcast: listeners opt in by joining the group, and
# APs / routers are less likely to drop it. TTL defaults to 1 -- same subnet
# only.
#
# Nothing in here raises. If init() was never called, or the socket setup
# failed, log()/emit() still print to the serial console and simply skip the
# network. Telemetry must never be able to take the app down.

import json
import socket

try:
    from time import ticks_ms as _ticks_ms          # MicroPython
except ImportError:                                  # CPython, for desktop tests
    from time import monotonic as _monotonic
    def _ticks_ms():
        return int(_monotonic() * 1000)

GROUP = "239.255.255.250"
PORT = 32301

_ECHO = True        # also print every record to stdout / the serial console
_sock = None
_addr = None

# Not every MicroPython port exposes these; fall back to no-ops if missing.
_IPPROTO_IP = getattr(socket, "IPPROTO_IP", 0)
_IP_MULTICAST_TTL = getattr(socket, "IP_MULTICAST_TTL", None)


def init(group=GROUP, port=PORT, ttl=1, echo=True):
    """Open the sending socket. Call once the network is up.

    Best-effort: on any failure the module stays in echo-only mode and this
    returns False rather than raising.
    """
    global _sock, _addr, _ECHO
    _ECHO = echo
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if _IP_MULTICAST_TTL is not None:
            try:
                s.setsockopt(_IPPROTO_IP, _IP_MULTICAST_TTL, ttl)
            except OSError:
                pass
        # Harmless for multicast, and lets the same call work if it is ever
        # pointed at a broadcast address instead.
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except (AttributeError, OSError):
            pass
        _addr = socket.getaddrinfo(group, port)[0][-1]
        _sock = s
    except Exception as e:  # noqa: BLE001 -- see module docstring
        print("netlog: init failed, echo only:", repr(e))
        _sock = None
        _addr = None
    return _sock is not None


def _send(text):
    if _sock is None or _addr is None:
        return
    try:
        _sock.sendto(text.encode(), _addr)      # .encode(): sendto wants bytes
    except Exception:  # noqa: BLE001
        pass


def log(*parts):
    """Timestamped free-text line (radar-style)."""
    line = "[{:8.2f}] {}".format(_ticks_ms() / 1000, " ".join(str(p) for p in parts))
    if _ECHO:
        print(line)
    _send(line)


def emit(event, **fields):
    """Structured record: {"event": event, **fields} as one JSON datagram."""
    record = {"event": event}
    record.update(fields)
    line = json.dumps(record)
    if _ECHO:
        print(line)
    _send(line)


def close():
    """Release the socket (e.g. before a soft reset). Safe to call anytime."""
    global _sock, _addr
    if _sock is not None:
        try:
            _sock.close()
        except Exception:  # noqa: BLE001
            pass
    _sock = None
    _addr = None
