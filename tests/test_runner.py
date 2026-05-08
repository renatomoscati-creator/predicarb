"""
Unit tests for TradingRunner.step() using mocked client and in-memory database.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.benchmark.csv_benchmark import BenchmarkProvider
from src.polymarket.models import OrderBook, OrderResult, Quote
from src.storage.db import Database
from src.storage.models import Signal, Tick
from src.strategy.edge_calculator import EdgeFilters
from src.strategy.runner import TradingRunner


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


class ConstantBenchmark(BenchmarkProvider):
    def __init__(self, prob: float) -> None:
        self._prob = prob

    def get_prob(self, ts_utc: datetime) -> float:
        return self._prob


def _make_orderbook(bid: float, ask: float) -> OrderBook:
    return OrderBook(
        ticker="0xTOKEN",
        yes_bids=[Quote(price=bid, size=500)],
        yes_asks=[Quote(price=ask, size=500)],
        last_update_ts=datetime.now(timezone.utc),
    )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.sqlite")
    d.init()
    return d


def _make_runner(db: Database, benchmark_prob: float, dry_run: bool = True, min_edge: float = 0.01) -> tuple[TradingRunner, MagicMock]:
    client = MagicMock()
    client.get_orderbook.return_value = _make_orderbook(0.45, 0.55)
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
        filters=EdgeFilters(max_spread=0.2),
        ticker="0xTOKEN",
        size=10,
        min_edge=min_edge,
        dry_run=dry_run,
    )
    return runner, client


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_step_inserts_tick(db):
    runner, _ = _make_runner(db, benchmark_prob=0.6)
    runner.step()

    ticks = db.get_ticks("0xTOKEN", limit=10)
    assert len(ticks) == 1
    assert ticks[0].yes_bid == pytest.approx(0.45)
    assert ticks[0].yes_ask == pytest.approx(0.55)
    assert ticks[0].yes_mid == pytest.approx(0.50)


def test_step_inserts_signal(db):
    runner, _ = _make_runner(db, benchmark_prob=0.65)
    result = runner.step()

    assert result is not None
    assert result.passed_filters is True
    assert result.edge == pytest.approx(0.15, rel=1e-5)


def test_step_no_trade_when_edge_below_threshold(db):
    # benchmark = 0.51, mid = 0.50, edge = 0.01 < min_edge 0.02
    runner, client = _make_runner(db, benchmark_prob=0.51, dry_run=False, min_edge=0.02)
    runner.step()

    client.place_order.assert_not_called()


def test_step_no_trade_when_filters_fail(db):
    client = MagicMock()
    # wide spread → fails filter
    client.get_orderbook.return_value = _make_orderbook(0.20, 0.80)
    runner = TradingRunner(
        client=client,
        db=db,
        benchmark=ConstantBenchmark(0.70),
        filters=EdgeFilters(max_spread=0.1),
        ticker="0xTOKEN",
        size=10,
        min_edge=0.01,
        dry_run=False,
    )
    runner.step()

    client.place_order.assert_not_called()


def test_step_dry_run_does_not_call_place_order(db):
    runner, client = _make_runner(db, benchmark_prob=0.75, dry_run=True, min_edge=0.01)
    runner.step()

    client.place_order.assert_not_called()


def test_step_live_buy_calls_place_order(db):
    runner, client = _make_runner(db, benchmark_prob=0.75, dry_run=False, min_edge=0.01)
    runner.step()

    client.place_order.assert_called_once()
    call_kwargs = client.place_order.call_args.kwargs
    assert call_kwargs["side"] == "BUY"
    assert call_kwargs["token_id"] == "0xTOKEN"
    assert call_kwargs["size"] == 10


def test_step_live_sell_calls_place_order(db):
    # benchmark 0.30, mid 0.50 → edge = -0.20 → SELL
    client = MagicMock()
    client.get_orderbook.return_value = _make_orderbook(0.45, 0.55)
    client.place_order.return_value = OrderResult(
        success=True, poly_order_id="sell-1", status="placed", message="",
        token_id="0xTOKEN", side="SELL", price=0.50, size=10,
    )
    runner = TradingRunner(
        client=client,
        db=db,
        benchmark=ConstantBenchmark(0.30),
        filters=EdgeFilters(max_spread=0.2),
        ticker="0xTOKEN",
        size=10,
        min_edge=0.01,
        dry_run=False,
    )
    runner.step()

    client.place_order.assert_called_once()
    call_kwargs = client.place_order.call_args.kwargs
    assert call_kwargs["side"] == "SELL"


def test_step_orderbook_failure_returns_none(db):
    client = MagicMock()
    client.get_orderbook.side_effect = RuntimeError("network error")
    runner = TradingRunner(
        client=client,
        db=db,
        benchmark=ConstantBenchmark(0.6),
        filters=EdgeFilters(max_spread=0.1),
        ticker="0xTOKEN",
        size=10,
        dry_run=True,
    )
    result = runner.step()

    assert result is None
    ticks = db.get_ticks("0xTOKEN", limit=10)
    assert len(ticks) == 0


def test_step_order_status_updated_after_placement(db):
    runner, client = _make_runner(db, benchmark_prob=0.75, dry_run=False, min_edge=0.01)
    runner.step()

    # Verify an order row exists with status 'placed'
    import sqlite3
    conn = sqlite3.connect(db._path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM orders").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["status"] == "placed"
    assert rows[0]["poly_order_id"] == "order-abc"
