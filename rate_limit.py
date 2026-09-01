#!/usr/bin/env python3
"""
rate_limit.py — shared Yahoo-call throttle

Extracted out of app.py as part of splitting out option_chain.py. Yahoo
throttles aggressively from shared cloud IPs, so RateLimiter enforces a
minimum gap between calls. app.py's own market-data functions
(_yf_download_with_retry, get_weekly_trend, get_spy_regime, ...) and
option_chain.py's chain-fetch functions both need to share ONE limiter
instance per speed tier — two independent limiters would each allow their
own gap and let the COMBINED call rate exceed what either alone was tuned
for. Hence the module-level singletons at the bottom rather than each
caller constructing its own.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, min_gap: float = 0.35):
        self._min_gap = min_gap
        self._lock    = threading.Lock()
        self._last_ts = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.time() - self._last_ts
            if elapsed < self._min_gap:
                time.sleep(self._min_gap - elapsed)
            self._last_ts = time.time()


def is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "too many requests" in msg or "rate limit" in msg or "429" in msg


# Shared instances. app.py and option_chain.py both import these rather than
# constructing their own, so rate limiting actually coordinates across the
# whole app instead of each module getting an independent budget.
RATE_LIMITER      = RateLimiter(min_gap=0.35)   # data + options calls
RATE_LIMITER_SLOW = RateLimiter(min_gap=0.80)   # weekly trend + earnings —
                                                 # per-ticker, cached 15-60min,
                                                 # kept off the fast lane so it
                                                 # doesn't crowd data fetches.
