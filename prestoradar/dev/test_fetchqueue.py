#!/usr/bin/env python3
"""Desktop test for fetchqueue.Queue -- priority ordering and draining.
No device, no asyncio event loop needed: only the pure enqueue/_pop pair is
exercised here. run() itself is thin async glue around _pop(), verified
on-device via radar_listen.py, the same way feed.py's run() is never
unit-tested directly -- only the pure logic behind it."""

import os
import sys

_PRESTORADAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_PRESTORADAR)
for _p in (os.path.join(_ROOT, "lib"), _PRESTORADAR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _eq(got, want, what):
    if got != want:
        raise AssertionError("%s: got %r, want %r" % (what, got, want))


def test_empty_pop_is_none():
    from fetchqueue import Queue
    q = Queue(interval_ms=1500)
    _eq(q._pop(), None, "an empty queue pops None")


def test_lowest_priority_first():
    from fetchqueue import Queue
    q = Queue(interval_ms=1500)
    q.enqueue("far", 40.0)
    q.enqueue("near", 5.0)
    q.enqueue("mid", 20.0)
    _eq(q._pop(), "near", "lowest priority pops first")
    _eq(q._pop(), "mid", "then the next-lowest")
    _eq(q._pop(), "far", "then the highest")
    _eq(q._pop(), None, "drained -> None")


def test_ties_preserve_insertion_order():
    from fetchqueue import Queue
    q = Queue(interval_ms=1500)
    q.enqueue("first", 10.0)
    q.enqueue("second", 10.0)
    _eq(q._pop(), "first", "equal priority: first enqueued pops first")
    _eq(q._pop(), "second", "then the second")


def test_enqueue_after_partial_drain():
    from fetchqueue import Queue
    q = Queue(interval_ms=1500)
    q.enqueue("a", 5.0)
    q.enqueue("b", 1.0)
    _eq(q._pop(), "b", "lowest first")
    q.enqueue("c", 0.5)
    _eq(q._pop(), "c", "a later, lower-priority enqueue still jumps ahead")
    _eq(q._pop(), "a", "then whatever was left")


def main():
    test_empty_pop_is_none()
    test_lowest_priority_first()
    test_ties_preserve_insertion_order()
    test_enqueue_after_partial_drain()
    print("fetchqueue.Queue: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
