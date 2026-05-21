"""Thread-safe token bucket for per-host rate limiting.

Used by the shared HTTP client. Each external host gets its own bucket sized to
the host's published rate limit minus a safety margin (see settings.*_RPS /
LDA_RPM). Single-process only — a Redis-backed bucket would be needed for
multi-worker deploys, but the pipeline runs in one Actions job so this is fine.
"""
from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: int | None = None):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity if capacity is not None else max(1, int(rate_per_sec * 2)))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self, n: float = 1.0) -> None:
        """Block until `n` tokens are available, then consume them."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = n - self._tokens
                wait = deficit / self.rate
            time.sleep(wait)
