"""
LiveBenchmark — BenchmarkProvider backed by live CME ZQ futures data.

Wraps the ZqFuturesBenchmark with a TTL cache so TradingRunner gets a fresh
probability on each poll without triggering a Yahoo Finance HTTP call on every tick.

Thread-safe: uses a lock so concurrent step() calls don't double-fetch.
Falls back to the last good value if the fetch fails (stale-while-revalidate).

Usage
-----
    from datetime import date
    from src.benchmark.live_benchmark import ZqLiveBenchmark

    bench = ZqLiveBenchmark(meeting_date=date(2026, 6, 18), n_cuts=1, ttl_seconds=60)
    prob = bench.get_prob(datetime.now(timezone.utc))   # 0.323 etc.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class CachedLiveBenchmark:
    """
    BenchmarkProvider backed by any callable that returns a probability float.
    Caches the result for *ttl_seconds* and falls back to the last good value
    on transient failures.
    """

    def __init__(
        self,
        fetch_fn: Callable[[], float],
        ttl_seconds: float = 300.0,   # 5 min — Yahoo Finance has ~15min delay; 3 checks per data epoch
        name: str = "live",
    ) -> None:
        self._fetch_fn = fetch_fn
        self._ttl = ttl_seconds
        self._name = name
        self._lock = threading.Lock()
        self._cached_prob: Optional[float] = None
        self._cached_at: Optional[float] = None   # time.monotonic()

    def _is_fresh(self) -> bool:
        if self._cached_at is None or self._cached_prob is None:
            return False
        return (time.monotonic() - self._cached_at) < self._ttl

    def _refresh(self) -> float:
        prob = self._fetch_fn()
        self._cached_prob = prob
        self._cached_at = time.monotonic()
        logger.info("[%s] benchmark refreshed: prob=%.4f", self._name, prob)
        return prob

    def get_prob(self, ts_utc: datetime) -> float:
        with self._lock:
            if self._is_fresh():
                return self._cached_prob  # type: ignore[return-value]
            try:
                return self._refresh()
            except Exception as exc:
                if self._cached_prob is not None:
                    logger.warning(
                        "[%s] fetch failed (%s); using stale value %.4f",
                        self._name, exc, self._cached_prob,
                    )
                    return self._cached_prob
                raise RuntimeError(
                    f"[{self._name}] no cached value and fetch failed: {exc}"
                ) from exc

    @property
    def last_prob(self) -> Optional[float]:
        return self._cached_prob

    @property
    def last_fetch_age_seconds(self) -> Optional[float]:
        if self._cached_at is None:
            return None
        return time.monotonic() - self._cached_at


def ZqLiveBenchmark(
    meeting_date: date,
    n_cuts: int = 1,
    ttl_seconds: float = 60.0,
) -> CachedLiveBenchmark:
    """
    BenchmarkProvider using CME 30-Day Fed Funds Futures (ZQ) to compute
    P(at least n_cuts of 25bps at the given FOMC meeting).

    Data source: Yahoo Finance delayed quotes (~15 min lag).
    For real-time timing trades on data releases, swap to a live CME feed.

    meeting_date : The FOMC decision date (day-weighted formula applied).
    n_cuts       : Minimum cut count for probability (default 1 = any cut).
    ttl_seconds  : Cache TTL (default 60s — balance freshness vs rate limits).
    """
    from src.benchmark.zq_benchmark import fetch_meeting_snapshot

    def _fetch() -> float:
        snap = fetch_meeting_snapshot(meeting_date)
        return snap.prob_at_least_n_cuts(n_cuts)

    return CachedLiveBenchmark(
        _fetch,
        ttl_seconds=ttl_seconds,
        name=f"zq-{meeting_date.isoformat()}-≥{n_cuts}cut",
    )
