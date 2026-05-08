"""
Unit tests for WsTradingRunner and the TradingRunner.step(ob) injection.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.benchmark.csv_benchmark import BenchmarkProvider
from src.polymarket.models import OrderBook, OrderResult, Quote
from src.storage.db import Database
from src.strategy.edge_calculator import EdgeFilters
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


def _make_ob(bid: float, ask: float, ticker: str = "0xTOKEN") -> OrderBook:
    return OrderBook(
        ticker=ticker,
        yes_bids=[Quote(price=bid, size=500)],
        yes_asks=[Quote(price=ask, size=500)],
        last_update_ts=datetime.now(timezone.utc),
    )


def _make_runner(
    db: Database,
    benchmark_prob: float = 0.70,
    dry_run: bool = True,
    min_edge: float = 0.02,
) -> tuple[TradingRunner, MagicMock]:
    client = MagicMock()
    client.get_orderbook.return_value = _make_ob(0.45, 0.55)
    client.place_order.return_value = OrderResult(
        success=True,
        poly_order_id="order-abc",
        status="placed",
        message="",
        token_id="0xTOKEN",
        side="BUY",
        price=0.50,
        size=10,
    )
    runner = TradingRunner(
        client=client,
        db=db,
        benchmark=ConstantBenchmark(benchmark_prob),
        filters=EdgeFilters(max_spread=0.20),
        ticker="0xTOKEN",
        size=10,
        min_edge=min_edge,
        dry_run=dry_run,
    )
    return runner, client


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.sqlite")
    d.init()
    return d


# ------------------------------------------------------------------
# TradingRunner.step(ob) — injected orderbook
# ------------------------------------------------------------------


def test_step_injected_ob_skips_rest_fetch(db):
    """step(ob=...) must NOT call client.get_orderbook."""
    runner, client = _make_runner(db)
    ob = _make_ob(0.45, 0.55)
    runner.step(ob=ob)
    client.get_orderbook.assert_not_called()


def test_step_no_ob_fetches_from_client(db):
    """step() without ob argument fetches via client (REST path preserved)."""
    runner, client = _make_runner(db)
    runner.step()
    client.get_orderbook.assert_called_once_with("0xTOKEN")


def test_step_injected_ob_returns_result(db):
    runner, _ = _make_runner(db, benchmark_prob=0.60)
    ob = _make_ob(0.45, 0.55)  # mid=0.50, edge=0.10
    result = runner.step(ob=ob)
    assert result is not None
    assert result.edge is not None


def test_step_injected_ob_persists_tick(db):
    runner, _ = _make_runner(db)
    ob = _make_ob(0.45, 0.55)
    runner.step(ob=ob)
    ticks = db.get_ticks("0xTOKEN")
    assert len(ticks) == 1
    assert ticks[0].yes_bid == pytest.approx(0.45)
    assert ticks[0].yes_ask == pytest.approx(0.55)


def test_step_injected_ob_buy_signal_dry_run(db):
    """With strong positive edge and dry_run=True, step records a dry_run order."""
    runner, client = _make_runner(db, benchmark_prob=0.80, dry_run=True, min_edge=0.02)
    ob = _make_ob(0.45, 0.55)  # mid=0.50, edge=0.30 (benchmark - mid)
    runner.step(ob=ob)
    # No real order placed
    client.place_order.assert_not_called()
    # dry_run order recorded in DB
    orders = db.get_recent_orders(status="dry_run")
    assert len(orders) == 1
    assert orders[0].action == "buy"


# ------------------------------------------------------------------
# WsTradingRunner.run
# ------------------------------------------------------------------


def _make_ws_runner(db: Database, **kwargs) -> tuple[WsTradingRunner, MagicMock]:
    runner, client = _make_runner(db, **kwargs)
    ws_runner = WsTradingRunner(runner=runner, ws_url="ws://test")
    return ws_runner, client


def _fake_stream(orderbooks: list[OrderBook]):
    """Return an async generator method that yields from orderbooks."""
    async def _stream_method(self):
        for ob in orderbooks:
            yield ob
    return _stream_method


def test_ws_runner_returns_zero_for_empty_stream(db):
    ws_runner, _ = _make_ws_runner(db)
    with patch("src.strategy.ws_runner.WsStream.stream", _fake_stream([])):
        count = asyncio.run(ws_runner.run())
    assert count == 0


def test_ws_runner_returns_tick_count(db):
    obs = [_make_ob(0.45, 0.55)] * 5
    ws_runner, _ = _make_ws_runner(db)
    with patch("src.strategy.ws_runner.WsStream.stream", _fake_stream(obs)):
        count = asyncio.run(ws_runner.run())
    assert count == 5


def test_ws_runner_stops_at_max_ticks(db):
    obs = [_make_ob(0.45, 0.55)] * 10
    ws_runner, _ = _make_ws_runner(db)
    with patch("src.strategy.ws_runner.WsStream.stream", _fake_stream(obs)):
        count = asyncio.run(ws_runner.run(max_ticks=3))
    assert count == 3


def test_ws_runner_persists_ticks_to_db(db):
    obs = [_make_ob(0.45, 0.55), _make_ob(0.46, 0.56), _make_ob(0.47, 0.57)]
    ws_runner, _ = _make_ws_runner(db)
    with patch("src.strategy.ws_runner.WsStream.stream", _fake_stream(obs)):
        asyncio.run(ws_runner.run())
    ticks = db.get_ticks("0xTOKEN", limit=10)
    assert len(ticks) == 3


def test_ws_runner_no_trade_below_min_edge(db):
    """With zero edge (benchmark == mid), no orders are placed."""
    obs = [_make_ob(0.45, 0.55)]  # mid=0.50
    ws_runner, client = _make_ws_runner(db, benchmark_prob=0.50, min_edge=0.02)
    with patch("src.strategy.ws_runner.WsStream.stream", _fake_stream(obs)):
        asyncio.run(ws_runner.run())
    client.place_order.assert_not_called()


def test_ws_runner_buy_on_positive_edge_dry_run(db):
    """Positive edge → BUY_YES dry_run order recorded."""
    obs = [_make_ob(0.45, 0.55)]  # mid=0.50, benchmark=0.80 → edge=0.30
    ws_runner, client = _make_ws_runner(db, benchmark_prob=0.80, dry_run=True, min_edge=0.02)
    with patch("src.strategy.ws_runner.WsStream.stream", _fake_stream(obs)):
        asyncio.run(ws_runner.run())
    client.place_order.assert_not_called()  # dry_run: no real call
    orders = db.get_recent_orders(status="dry_run")
    assert len(orders) == 1
    assert orders[0].action == "buy"


def test_ws_runner_sell_on_negative_edge_dry_run(db):
    """Negative edge → SELL_YES dry_run order recorded."""
    obs = [_make_ob(0.55, 0.65)]  # mid=0.60, benchmark=0.30 → edge=-0.30
    ws_runner, client = _make_ws_runner(db, benchmark_prob=0.30, dry_run=True, min_edge=0.02)
    with patch("src.strategy.ws_runner.WsStream.stream", _fake_stream(obs)):
        asyncio.run(ws_runner.run())
    orders = db.get_recent_orders(status="dry_run")
    assert len(orders) == 1
    assert orders[0].action == "sell"


def test_ws_runner_multiple_ticks_multiple_signals(db):
    """One buy and one no-trade tick → only one dry_run order."""
    obs = [
        _make_ob(0.45, 0.55),  # edge=0.30 → BUY
        _make_ob(0.48, 0.52),  # edge=0.28 → BUY
        _make_ob(0.78, 0.82),  # mid=0.80, benchmark=0.80 → edge=0 → NO_TRADE
    ]
    ws_runner, _ = _make_ws_runner(db, benchmark_prob=0.80, dry_run=True, min_edge=0.02)
    with patch("src.strategy.ws_runner.WsStream.stream", _fake_stream(obs)):
        asyncio.run(ws_runner.run())
    orders = db.get_recent_orders()
    buy_orders = [o for o in orders if o.action == "buy"]
    assert len(buy_orders) == 2
