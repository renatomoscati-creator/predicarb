"""
Tests for the SQLite storage layer using an in-memory database.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.storage.db import Database
from src.storage.models import (
    BacktestRun,
    Fill,
    Order,
    Position,
    Signal,
    Tick,
)


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(tmp_path / "test.sqlite")
    d.init()
    return d


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------
# Schema creation
# ------------------------------------------------------------------

def test_init_creates_tables(db):
    # Calling init twice should be idempotent
    db.init()


# ------------------------------------------------------------------
# Ticks
# ------------------------------------------------------------------

def test_insert_and_get_tick(db):
    tick = Tick(
        ticker="0xYES_TOKEN_TEST",
        ts_utc=_ts(),
        yes_bid=0.01,
        yes_ask=0.02,
        yes_mid=0.015,
        bid_size=1000,
        ask_size=500,
    )
    row_id = db.insert_tick(tick)
    assert isinstance(row_id, int) and row_id > 0

    rows = db.get_ticks("0xYES_TOKEN_TEST", limit=10)
    assert len(rows) == 1
    assert rows[0].yes_bid == pytest.approx(0.01)
    assert rows[0].yes_mid == pytest.approx(0.015)


def test_get_ticks_respects_limit(db):
    for i in range(5):
        db.insert_tick(Tick(ticker="T", ts_utc=_ts(), yes_bid=float(i)/100,
                            yes_ask=float(i+1)/100, yes_mid=None, bid_size=None, ask_size=None))
    rows = db.get_ticks("T", limit=3)
    assert len(rows) == 3


# ------------------------------------------------------------------
# Signals
# ------------------------------------------------------------------

def test_insert_signal(db):
    sig = Signal(
        ticker="T",
        ts_utc=_ts(),
        benchmark_prob=0.05,
        market_mid=0.015,
        edge=0.035,
        passed_filters=True,
        reason="ok",
        decision="BUY_YES",
    )
    sig_id = db.insert_signal(sig)
    assert sig_id > 0


# ------------------------------------------------------------------
# Orders
# ------------------------------------------------------------------

def test_insert_and_update_order(db):
    order = Order(
        ticker="T",
        ts_utc=_ts(),
        side="yes",
        action="buy",
        count=10,
        price=0.015,
        order_type="limit",
        status="pending",
    )
    order_id = db.insert_order(order)
    assert order_id > 0

    db.update_order_status(order_id, "placed", poly_order_id="abc-123")
    db.update_order_status(order_id, "filled")


# ------------------------------------------------------------------
# Fills
# ------------------------------------------------------------------

def test_insert_fill(db):
    order = Order(ticker="T", ts_utc=_ts(), side="yes", action="buy",
                  count=5, price=0.02, order_type="limit", status="placed")
    order_id = db.insert_order(order)

    fill = Fill(
        order_id=order_id,
        ticker="T",
        ts_utc=_ts(),
        side="yes",
        count=5,
        price=0.02,
        fee_cents=1,
        poly_fill_id="fill-xyz",
    )
    fill_id = db.insert_fill(fill)
    assert fill_id > 0


# ------------------------------------------------------------------
# Positions
# ------------------------------------------------------------------

def test_upsert_position(db):
    pos = Position(ticker="T", ts_utc=_ts(), yes_count=10, avg_cost=0.015, realized_pnl=0.0)
    db.upsert_position(pos)

    fetched = db.get_position("T")
    assert fetched is not None
    assert fetched.yes_count == 10

    # Update
    pos2 = Position(ticker="T", ts_utc=_ts(), yes_count=5, avg_cost=0.02, realized_pnl=0.05)
    db.upsert_position(pos2)
    fetched2 = db.get_position("T")
    assert fetched2.yes_count == 5
    assert fetched2.realized_pnl == pytest.approx(0.05)


def test_get_position_missing(db):
    assert db.get_position("NONEXISTENT") is None


# ------------------------------------------------------------------
# Backtest runs
# ------------------------------------------------------------------

def test_insert_and_list_backtest_run(db):
    run = BacktestRun(
        ts_utc=_ts(),
        market_file="data/polymarket.csv",
        benchmark_file="data/benchmark.csv",
        total_trades=42,
        win_rate=0.6,
        total_pnl=1.23,
        sharpe=1.5,
        max_drawdown=0.1,
        params_json=json.dumps({"max_spread": 0.1}),
    )
    run_id = db.insert_backtest_run(run)
    assert run_id > 0

    runs = db.list_backtest_runs(limit=5)
    assert len(runs) == 1
    assert runs[0].total_trades == 42
    assert runs[0].win_rate == pytest.approx(0.6)
