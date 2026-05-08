from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.benchmark.csv_benchmark import BenchmarkProvider
from src.polymarket.models import OrderBook


@dataclass
class EdgeFilters:
    max_spread: float
    min_depth: Optional[int] = None
    max_staleness_seconds: Optional[float] = None


@dataclass
class EdgeResult:
    market_mid: Optional[float]
    benchmark_prob: float
    edge: Optional[float]
    passed_filters: bool
    reason: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def compute_edge(
    orderbook: OrderBook,
    benchmark: BenchmarkProvider,
    filters: EdgeFilters,
) -> EdgeResult:
    now = _utcnow()
    benchmark_prob = benchmark.get_prob(now)

    bid = orderbook.best_yes_bid
    ask = orderbook.best_yes_ask

    if not bid or not ask:
        return EdgeResult(
            market_mid=None,
            benchmark_prob=benchmark_prob,
            edge=None,
            passed_filters=False,
            reason="missing_side",
        )

    spread = ask.price - bid.price
    if spread > filters.max_spread:
        mid = (bid.price + ask.price) / 2.0
        return EdgeResult(
            market_mid=mid,
            benchmark_prob=benchmark_prob,
            edge=benchmark_prob - mid,
            passed_filters=False,
            reason="spread_too_wide",
        )

    if filters.min_depth is not None:
        depth = min(bid.size, ask.size)
        if depth < filters.min_depth:
            mid = (bid.price + ask.price) / 2.0
            return EdgeResult(
                market_mid=mid,
                benchmark_prob=benchmark_prob,
                edge=benchmark_prob - mid,
                passed_filters=False,
                reason="insufficient_depth",
            )

    if filters.max_staleness_seconds is not None and orderbook.last_update_ts:
        age = (now - orderbook.last_update_ts).total_seconds()
        if age > filters.max_staleness_seconds:
            mid = (bid.price + ask.price) / 2.0
            return EdgeResult(
                market_mid=mid,
                benchmark_prob=benchmark_prob,
                edge=benchmark_prob - mid,
                passed_filters=False,
                reason="stale_quotes",
            )

    mid = (bid.price + ask.price) / 2.0
    edge = benchmark_prob - mid
    return EdgeResult(
        market_mid=mid,
        benchmark_prob=benchmark_prob,
        edge=edge,
        passed_filters=True,
        reason="ok",
    )
