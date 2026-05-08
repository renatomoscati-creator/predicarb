"""
Unit tests for OrderMonitor and new Database query methods.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.storage.db import Database
from src.storage.models import Order
from src.strategy.order_monitor import OrderMonitor
from src.strategy.position_manager import PositionManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.sqlite")
    d.init()
    return d


def _ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _insert_placed_order(db: Database, ticker: str = "0xTOKEN",
                          action: str = "buy", poly_id: str = "poly-1") -> int:
    order = Order(
        ticker=ticker, ts_utc=_ts(), side="yes", action=action,
        count=100, price=0.55, order_type="limit", status="placed",
        poly_order_id=poly_id,
    )
    return db.insert_order(order)


def _make_client(status_data: dict) -> MagicMock:
    client = MagicMock()
    client.get_order_status.return_value = status_data
    return client


def _make_monitor(db, client, pm=None) -> OrderMonitor:
    return OrderMonitor(db=db, client=client, position_manager=pm)


# ------------------------------------------------------------------
# Database: get_open_orders
# ------------------------------------------------------------------


def test_get_open_orders_returns_placed_with_poly_id(db):
    _insert_placed_order(db, poly_id="abc")
    orders = db.get_open_orders()
    assert len(orders) == 1
    assert orders[0].poly_order_id == "abc"


def test_get_open_orders_excludes_no_poly_id(db):
    order = Order(ticker="T", ts_utc=_ts(), side="yes", action="buy",
                  count=10, price=0.5, order_type="limit", status="placed",
                  poly_order_id=None)
    db.insert_order(order)
    assert db.get_open_orders() == []


def test_get_open_orders_excludes_filled(db):
    oid = _insert_placed_order(db)
    db.update_order_status(oid, "filled")
    assert db.get_open_orders() == []


def test_get_open_orders_excludes_cancelled(db):
    oid = _insert_placed_order(db)
    db.update_order_status(oid, "cancelled")
    assert db.get_open_orders() == []


# ------------------------------------------------------------------
# Database: get_recent_orders
# ------------------------------------------------------------------


def test_get_recent_orders_returns_all(db):
    _insert_placed_order(db, poly_id="a")
    _insert_placed_order(db, poly_id="b")
    orders = db.get_recent_orders(limit=10)
    assert len(orders) == 2


def test_get_recent_orders_filters_by_status(db):
    oid = _insert_placed_order(db, poly_id="a")
    db.update_order_status(oid, "filled")
    _insert_placed_order(db, poly_id="b")

    filled = db.get_recent_orders(status="filled")
    assert len(filled) == 1
    assert filled[0].status == "filled"

    placed = db.get_recent_orders(status="placed")
    assert len(placed) == 1


def test_get_recent_orders_respects_limit(db):
    for i in range(5):
        _insert_placed_order(db, poly_id=f"p{i}")
    orders = db.get_recent_orders(limit=3)
    assert len(orders) == 3


# ------------------------------------------------------------------
# Database: get_all_positions
# ------------------------------------------------------------------


def test_get_all_positions_empty(db):
    assert db.get_all_positions() == []


def test_get_all_positions_returns_all(db):
    from src.storage.models import Position
    db.upsert_position(Position(ticker="A", ts_utc=_ts(), yes_count=10, avg_cost=0.5, realized_pnl=0.0))
    db.upsert_position(Position(ticker="B", ts_utc=_ts(), yes_count=20, avg_cost=0.6, realized_pnl=1.0))
    positions = db.get_all_positions()
    assert len(positions) == 2
    tickers = {p.ticker for p in positions}
    assert tickers == {"A", "B"}


# ------------------------------------------------------------------
# OrderMonitor.check_once — no open orders
# ------------------------------------------------------------------


def test_check_once_no_open_orders(db):
    client = _make_client({})
    mon = _make_monitor(db, client)
    assert mon.check_once() == 0
    client.get_order_status.assert_not_called()


# ------------------------------------------------------------------
# OrderMonitor.check_once — LIVE (still open)
# ------------------------------------------------------------------


def test_check_once_live_order_not_filled(db):
    _insert_placed_order(db, poly_id="p1")
    client = _make_client({"status": "LIVE"})
    mon = _make_monitor(db, client)
    fills = mon.check_once()
    assert fills == 0
    # Still placed
    assert db.get_open_orders()[0].status == "placed"


# ------------------------------------------------------------------
# OrderMonitor.check_once — MATCHED (filled)
# ------------------------------------------------------------------


def test_check_once_matched_updates_status(db):
    _insert_placed_order(db, poly_id="p1")
    client = _make_client({"status": "MATCHED", "size_matched": "100", "price": "0.55"})
    mon = _make_monitor(db, client)
    fills = mon.check_once()
    assert fills == 1
    assert db.get_open_orders() == []  # no longer placed


def test_check_once_matched_inserts_fill_record(db):
    _insert_placed_order(db, poly_id="p1")
    client = _make_client({"status": "MATCHED", "size_matched": "100", "price": "0.55"})
    mon = _make_monitor(db, client)
    mon.check_once()

    import sqlite3
    conn = sqlite3.connect(db._path)
    rows = conn.execute("SELECT * FROM fills").fetchall()
    conn.close()
    assert len(rows) == 1


def test_check_once_matched_updates_position_manager(db):
    _insert_placed_order(db, poly_id="p1", action="buy")
    client = _make_client({"status": "MATCHED", "size_matched": "100", "price": "0.55"})
    pm = PositionManager(db=db, ticker="0xTOKEN")
    mon = _make_monitor(db, client, pm=pm)
    mon.check_once()
    assert pm.yes_count == 100
    assert pm.avg_cost == pytest.approx(0.55)


def test_check_once_sell_fill_updates_position(db):
    pm = PositionManager(db=db, ticker="0xTOKEN")
    pm.record_fill("BUY_YES", 0.50, 100)

    _insert_placed_order(db, poly_id="p1", action="sell")
    client = _make_client({"status": "MATCHED", "size_matched": "100", "price": "0.65"})
    mon = _make_monitor(db, client, pm=pm)
    mon.check_once()

    assert pm.yes_count == 0
    assert pm.realized_pnl == pytest.approx(15.0)  # (0.65-0.50)*100


# ------------------------------------------------------------------
# OrderMonitor.check_once — CANCELLED
# ------------------------------------------------------------------


def test_check_once_cancelled_updates_status(db):
    _insert_placed_order(db, poly_id="p1")
    client = _make_client({"status": "CANCELLED"})
    mon = _make_monitor(db, client)
    fills = mon.check_once()
    assert fills == 0
    assert db.get_open_orders() == []

    recent = db.get_recent_orders(status="cancelled")
    assert len(recent) == 1


def test_check_once_unmatched_treated_as_cancelled(db):
    _insert_placed_order(db, poly_id="p1")
    client = _make_client({"status": "UNMATCHED"})
    mon = _make_monitor(db, client)
    mon.check_once()
    assert db.get_recent_orders(status="cancelled")[0].status == "cancelled"


# ------------------------------------------------------------------
# OrderMonitor.check_once — client returns None
# ------------------------------------------------------------------


def test_check_once_client_returns_none(db):
    _insert_placed_order(db, poly_id="p1")
    client = MagicMock()
    client.get_order_status.return_value = None
    mon = _make_monitor(db, client)
    fills = mon.check_once()
    assert fills == 0
    # Order still placed — no change
    assert len(db.get_open_orders()) == 1


# ------------------------------------------------------------------
# Multiple open orders
# ------------------------------------------------------------------


def test_check_once_multiple_orders(db):
    _insert_placed_order(db, poly_id="p1")
    _insert_placed_order(db, poly_id="p2")
    _insert_placed_order(db, poly_id="p3")

    client = MagicMock()
    client.get_order_status.side_effect = [
        {"status": "MATCHED", "size_matched": "100", "price": "0.55"},
        {"status": "LIVE"},
        {"status": "CANCELLED"},
    ]
    mon = _make_monitor(db, client)
    fills = mon.check_once()
    assert fills == 1
    assert len(db.get_open_orders()) == 1  # only p2 still placed
