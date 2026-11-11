"""Per-client rate limiting that delays requests instead of rejecting them.

Each client gets a reserved time slot at most once per ``interval`` seconds.
A request that arrives early sleeps until its slot comes round, so a burst of
calls is served in order, one per interval. Only when a client has queued up
more than ``max_wait`` seconds' worth of calls is the request refused, which
stops one caller from tying up every server thread.
"""

import threading
import time


class RateLimitExceeded(Exception):
    """Raised when a request would have to wait longer than ``max_wait``."""

    def __init__(self, retry_after):
        super(RateLimitExceeded, self).__init__(
            "Too many requests; retry in %.0f seconds." % retry_after
        )
        self.retry_after = retry_after


class SlotRateLimiter(object):
    def __init__(self, interval=1.0, max_wait=30.0, clock=time.monotonic,
                 sleep=time.sleep):
        self.interval = interval
        self.max_wait = max_wait
        self._clock = clock
        self._sleep = sleep
        self._next_slot = {}
        self._lock = threading.Lock()

    def wait(self, client):
        """Block until ``client`` may proceed; return how long it waited."""
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot.get(client, now))
            delay = slot - now
            if delay > self.max_wait:
                raise RateLimitExceeded(delay)
            self._next_slot[client] = slot + self.interval
            self._forget_idle_clients(now)

        # Sleep outside the lock so other clients are not held up.
        if delay > 0:
            self._sleep(delay)
        return delay

    def _forget_idle_clients(self, now):
        # Keep the table small: a client whose next slot has passed is
        # indistinguishable from one never seen before.
        if len(self._next_slot) > 1024:
            self._next_slot = {
                key: slot for key, slot in self._next_slot.items() if slot > now
            }

    def reset(self):
        with self._lock:
            self._next_slot.clear()
