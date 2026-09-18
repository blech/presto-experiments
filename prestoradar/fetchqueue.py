import asyncio


class Queue:
    """Generic priority-ordered, single-concurrency, paced work queue.
    Knows nothing about aircraft, hexes, or traces -- just drains
    (priority, key) pairs at a fixed interval, calling a caller-supplied
    async callback per key. One instance is meant to be shared across every
    feature that needs to stay under a remote service's request budget
    (traces.py's backfill now; a future list mode's route-fetching later --
    see docs/superpowers/specs/2026-09-18-trace-fetch-queue-design.md)."""

    def __init__(self, interval_ms):
        self._interval_ms = interval_ms
        self._pending = []          # [(priority, key), ...]

    def enqueue(self, key, priority):
        self._pending.append((priority, key))

    def _pop(self):
        """Remove and return the lowest-priority key, or None if empty.
        A plain list with a sort-then-pop is deliberate: expected depth is
        a few dozen entries at most, where an O(n log n) sort per drain
        tick is trivial -- no heap needed. Ties keep insertion order
        (list.sort() is stable)."""
        if not self._pending:
            return None
        self._pending.sort(key=lambda entry: entry[0])
        return self._pending.pop(0)[1]

    async def run(self, process_one):
        """Drain one entry per `interval_ms`, calling `process_one(key)`.
        Runs forever -- start as its own asyncio task."""
        while True:
            key = self._pop()
            if key is not None:
                await process_one(key)
            await asyncio.sleep(self._interval_ms / 1000.0)
