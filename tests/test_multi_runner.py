"""
Unit tests for MultiMarketRunner.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.benchmark.csv_benchmark import BenchmarkProvider
from src.polymarket.models import OrderBook, OrderResult, Quote
from src.storage.db import Database
from src.strategy.edge_calculator import EdgeFilters
from src.strategy.multi_runner import MultiMarketRunner
from src.strategy.runner import TradingRunner
from src.strategy.ws_runner import WsTradingRunner


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class ConstantBenchmark(BenchmarkProvider):
    def __init__(self, prob: float) -> None:
        self._prob = prob

    def get_prob(self, ts_utc: datetime) -> float:
        return self._prob


def _make_ob(bid: float, ask: float, ticker: str) -> OrderBook:
    return OrderBook(
        ticker=ticker,
        yes_bids=[Quote(price=bid, size=500)],
        yes_asks=[Quote(price=ask, size=500)],
        last_update_ts=datetime.now(timezone.utc),
    )


def _make_ws_runner(
    db: Database,
    ticker: str,
    benchmark_prob: float = 0.70,
    dry_run: bool = True,
    min_edge: float = 0.02,
) -> WsTradingRunner:
    client = MagicMock()
    client.place_order.return_value = OrderResult(
        success=True,
        poly_order_id="order-abc",
        status="placed",
        message="",
        token_id=ticker,
        side="BUY",
        price=0.50,
        size=10,
    )
    trading_runner = TradingRunner(
        client=client,
        db=db,
        benchmark=ConstantBenchmark(benchmark_prob),
        filters=EdgeFilters(max_spread=0.20),
        ticker=ticker,
        size=10,
        min_edge=min_edge,
        dry_run=dry_run,
    )
    return WsTradingRunner(runner=trading_runner, ws_url="ws://test")


def _inject_stream(ws_runner: WsTradingRunner, orderbooks: list[OrderBook]) -> None:
    """Replace ws_runner's WsStream with a fake that yields fixed orderbooks."""
    async def _stream():
        for ob in orderbooks:
            yield ob

    mock_ws = MagicMock()
    mock_ws.stream = _stream
    ws_runner._ws = mock_ws


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.sqlite")
    d.init()
    return d


# ------------------------------------------------------------------
# MultiMarketRunner.run — basics
# ------------------------------------------------------------------


def test_empty_runners_returns_empty_dict(db):
    multi = MultiMarketRunner({})
    counts = asyncio.run(multi.run())
    assert counts == {}


def test_single_market_returns_tick_count(db):
    ticker = "0xA"
    ws_runner = _make_ws_runner(db, ticker)
    obs = [_make_ob(0.45, 0.55, ticker)] * 4
    _inject_stream(ws_runner, obs)

    multi = MultiMarketRunner({ticker: ws_runner})
    counts = asyncio.run(multi.run())
    assert counts == {ticker: 4}


def test_two_markets_run_concurrently(db):
    t_a, t_b = "0xA", "0xB"
    r_a = _make_ws_runner(db, t_a)
    r_b = _make_ws_runner(db, t_b)
    _inject_stream(r_a, [_make_ob(0.45, 0.55, t_a)] * 3)
    _inject_stream(r_b, [_make_ob(0.46, 0.56, t_b)] * 5)

    multi = MultiMarketRunner({t_a: r_a, t_b: r_b})
    counts = asyncio.run(multi.run())

    assert counts[t_a] == 3
    assert counts[t_b] == 5


def test_max_ticks_per_market_limits_each_independently(db):
    t_a, t_b = "0xA", "0xB"
    r_a = _make_ws_runner(db, t_a)
    r_b = _make_ws_runner(db, t_b)
    _inject_stream(r_a, [_make_ob(0.45, 0.55, t_a)] * 20)
    _inject_stream(r_b, [_make_ob(0.45, 0.55, t_b)] * 20)

    multi = MultiMarketRunner({t_a: r_a, t_b: r_b})
    counts = asyncio.run(multi.run(max_ticks_per_market=7))

    assert counts[t_a] == 7
    assert counts[t_b] == 7


def test_three_markets_all_counted(db):
    tickers = ["0xA", "0xB", "0xC"]
    runners = {}
    for i, t in enumerate(tickers):
        r = _make_ws_runner(db, t)
        _inject_stream(r, [_make_ob(0.45, 0.55, t)] * (i + 2))
        runners[t] = r

    counts = asyncio.run(MultiMarketRunner(runners).run())
    assert counts["0xA"] == 2
    assert counts["0xB"] == 3
    assert counts["0xC"] == 4


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------


def test_ticks_from_both_markets_stored_in_db(db):
    t_a, t_b = "0xA", "0xB"
    r_a = _make_ws_runner(db, t_a)
    r_b = _make_ws_runner(db, t_b)
    _inject_stream(r_a, [_make_ob(0.45, 0.55, t_a)] * 2)
    _inject_stream(r_b, [_make_ob(0.60, 0.70, t_b)] * 3)

    asyncio.run(MultiMarketRunner({t_a: r_a, t_b: r_b}).run())

    assert len(db.get_ticks(t_a)) == 2
    assert len(db.get_ticks(t_b)) == 3


def test_orders_from_both_markets_stored_in_db(db):
    """With strong positive edge, both markets record dry_run orders."""
    t_a, t_b = "0xA", "0xB"
    # benchmark=0.80, mid=0.50, edge=0.30 → BUY for both
    r_a = _make_ws_runner(db, t_a, benchmark_prob=0.80)
    r_b = _make_ws_runner(db, t_b, benchmark_prob=0.80)
    _inject_stream(r_a, [_make_ob(0.45, 0.55, t_a)])
    _inject_stream(r_b, [_make_ob(0.45, 0.55, t_b)])

    asyncio.run(MultiMarketRunner({t_a: r_a, t_b: r_b}).run())

    orders = db.get_recent_orders(status="dry_run")
    tickers_in_orders = {o.ticker for o in orders}
    assert t_a in tickers_in_orders
    assert t_b in tickers_in_orders


def test_no_orders_when_edge_below_threshold(db):
    t_a, t_b = "0xA", "0xB"
    # benchmark == mid → edge == 0 → no trade
    r_a = _make_ws_runner(db, t_a, benchmark_prob=0.50)
    r_b = _make_ws_runner(db, t_b, benchmark_prob=0.50)
    _inject_stream(r_a, [_make_ob(0.45, 0.55, t_a)])
    _inject_stream(r_b, [_make_ob(0.45, 0.55, t_b)])

    asyncio.run(MultiMarketRunner({t_a: r_a, t_b: r_b}).run())

    assert db.get_recent_orders() == []


# ------------------------------------------------------------------
# Fault isolation
# ------------------------------------------------------------------


def test_exception_in_one_market_others_continue(db):
    """If a runner raises, MultiMarketRunner logs the error and keeps other markets running."""
    t_a, t_b = "0xA", "0xB"
    r_a = _make_ws_runner(db, t_a)
    r_b = _make_ws_runner(db, t_b)

    # r_a will raise immediately
    async def _bad_stream():
        raise RuntimeError("WS blew up")
        yield  # pragma: no cover — makes it an async generator

    mock_bad = MagicMock()
    mock_bad.stream = _bad_stream
    r_a._ws = mock_bad

    _inject_stream(r_b, [_make_ob(0.45, 0.55, t_b)] * 3)

    counts = asyncio.run(MultiMarketRunner({t_a: r_a, t_b: r_b}).run())

    # Bad market counted as 0; good market counted normally
    assert counts[t_a] == 0
    assert counts[t_b] == 3
