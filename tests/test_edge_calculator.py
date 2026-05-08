from datetime import datetime, timezone

import pytest

from src.benchmark.csv_benchmark import BenchmarkProvider
from src.polymarket.models import OrderBook, Quote
from src.strategy.edge_calculator import EdgeFilters, compute_edge


class ConstantBenchmark(BenchmarkProvider):
    def __init__(self, prob: float) -> None:
        self._prob = prob

    def get_prob(self, ts_utc: datetime) -> float:
        return self._prob


def _make_orderbook(bid_price: float, ask_price: float) -> OrderBook:
    return OrderBook(
        ticker="0xYES_TOKEN",
        yes_bids=[Quote(price=bid_price, size=10)],
        yes_asks=[Quote(price=ask_price, size=10)],
        last_update_ts=datetime.now(timezone.utc),
    )


def test_compute_edge_happy_path() -> None:
    ob = _make_orderbook(0.4, 0.6)
    benchmark = ConstantBenchmark(0.7)
    filters = EdgeFilters(max_spread=0.3)

    result = compute_edge(orderbook=ob, benchmark=benchmark, filters=filters)

    assert result.passed_filters is True
    assert result.market_mid == pytest.approx(0.5, rel=1e-6)
    assert result.edge == pytest.approx(0.2, rel=1e-6)


def test_compute_edge_spread_too_wide() -> None:
    ob = _make_orderbook(0.3, 0.8)
    benchmark = ConstantBenchmark(0.6)
    filters = EdgeFilters(max_spread=0.1)

    result = compute_edge(orderbook=ob, benchmark=benchmark, filters=filters)

    assert result.passed_filters is False
    assert result.reason == "spread_too_wide"
    assert result.market_mid == pytest.approx(0.55, rel=1e-6)


def test_compute_edge_missing_side() -> None:
    ob = OrderBook(
        ticker="0xYES_TOKEN",
        yes_bids=[],
        yes_asks=[Quote(price=0.55, size=10)],
        last_update_ts=datetime.now(timezone.utc),
    )
    result = compute_edge(
        orderbook=ob,
        benchmark=ConstantBenchmark(0.6),
        filters=EdgeFilters(max_spread=0.1),
    )
    assert result.passed_filters is False
    assert result.reason == "missing_side"
    assert result.market_mid is None


def test_compute_edge_insufficient_depth() -> None:
    ob = _make_orderbook(0.48, 0.52)
    result = compute_edge(
        orderbook=ob,
        benchmark=ConstantBenchmark(0.6),
        filters=EdgeFilters(max_spread=0.1, min_depth=100),
    )
    assert result.passed_filters is False
    assert result.reason == "insufficient_depth"


def test_compute_edge_stale_quotes() -> None:
    from datetime import timedelta
    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=120)
    ob = OrderBook(
        ticker="0xYES_TOKEN",
        yes_bids=[Quote(price=0.48, size=10)],
        yes_asks=[Quote(price=0.52, size=10)],
        last_update_ts=stale_ts,
    )
    result = compute_edge(
        orderbook=ob,
        benchmark=ConstantBenchmark(0.6),
        filters=EdgeFilters(max_spread=0.1, max_staleness_seconds=60.0),
    )
    assert result.passed_filters is False
    assert result.reason == "stale_quotes"
