"""
Unit tests for TickCollector using a mocked WsStream.
"""
from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.polymarket.models import OrderBook, Quote
from src.storage.db import Database
from src.strategy.collector import TickCollector, _ob_to_tick


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_ob(bid: float, ask: float, ticker: str = "0xTOKEN") -> OrderBook:
    return OrderBook(
        ticker=ticker,
        yes_bids=[Quote(price=bid, size=500)],
        yes_asks=[Quote(price=ask, size=300)],
        last_update_ts=datetime.now(timezone.utc),
    )


async def _fake_stream(orderbooks: list[OrderBook]):
    """Async generator that yields a fixed sequence of OrderBooks then stops."""
    for ob in orderbooks:
        yield ob


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.sqlite")
    d.init()
    return d


# ------------------------------------------------------------------
# _ob_to_tick
# ------------------------------------------------------------------


def test_ob_to_tick_fields():
    ob = _make_ob(0.45, 0.55)
    tick = _ob_to_tick(ob)
    assert tick.ticker == "0xTOKEN"
    assert tick.yes_bid == pytest.approx(0.45)
    assert tick.yes_ask == pytest.approx(0.55)
    assert tick.yes_mid == pytest.approx(0.50)
    assert tick.bid_size == 500
    assert tick.ask_size == 300


def test_ob_to_tick_empty_book():
    ob = OrderBook(ticker="0xTOKEN", yes_bids=[], yes_asks=[], last_update_ts=None)
    tick = _ob_to_tick(ob)
    assert tick.yes_bid is None
    assert tick.yes_ask is None
    assert tick.yes_mid is None


# ------------------------------------------------------------------
# TickCollector.run (mocked stream)
# ------------------------------------------------------------------


def _patch_stream(orderbooks: list[OrderBook]):
    """Return a context manager that patches WsStream.stream with a fake async gen."""
    async def _stream_method(self):
        for ob in orderbooks:
            yield ob

    return patch("src.strategy.collector.WsStream.stream", _stream_method)


def test_run_inserts_ticks_to_db(db, tmp_path):
    obs = [_make_ob(0.45, 0.55), _make_ob(0.46, 0.54), _make_ob(0.47, 0.53)]

    with _patch_stream(obs):
        collector = TickCollector(db=db, ticker="0xTOKEN")
        count = asyncio.run(collector.run())

    assert count == 3
    ticks = db.get_ticks("0xTOKEN", limit=10)
    assert len(ticks) == 3


def test_run_writes_csv(db, tmp_path):
    csv_path = tmp_path / "ticks.csv"
    obs = [_make_ob(0.45, 0.55), _make_ob(0.46, 0.54)]

    with _patch_stream(obs):
        collector = TickCollector(db=db, ticker="0xTOKEN", csv_path=csv_path)
        count = asyncio.run(collector.run())

    assert count == 2
    assert csv_path.exists()
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert float(rows[0]["yes_bid"]) == pytest.approx(0.45)
    assert float(rows[1]["yes_bid"]) == pytest.approx(0.46)


def test_run_csv_appends_to_existing(db, tmp_path):
    csv_path = tmp_path / "ticks.csv"

    # First run: 1 tick
    with _patch_stream([_make_ob(0.45, 0.55)]):
        TickCollector(db=db, ticker="0xTOKEN", csv_path=csv_path)
        asyncio.run(TickCollector(db=db, ticker="0xTOKEN", csv_path=csv_path).run())

    # Second run: 2 more ticks — should append, not overwrite
    with _patch_stream([_make_ob(0.46, 0.54), _make_ob(0.47, 0.53)]):
        asyncio.run(TickCollector(db=db, ticker="0xTOKEN", csv_path=csv_path).run())

    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3  # 1 + 2


def test_run_csv_has_correct_header(db, tmp_path):
    csv_path = tmp_path / "ticks.csv"

    with _patch_stream([_make_ob(0.45, 0.55)]):
        asyncio.run(TickCollector(db=db, ticker="0xTOKEN", csv_path=csv_path).run())

    with csv_path.open() as f:
        header = f.readline().strip().split(",")
    assert "ts_utc" in header
    assert "yes_bid" in header
    assert "yes_ask" in header
    assert "yes_mid" in header


def test_run_returns_zero_on_empty_stream(db):
    with _patch_stream([]):
        count = asyncio.run(TickCollector(db=db, ticker="0xTOKEN").run())
    assert count == 0


def test_run_db_tick_values_match_orderbook(db):
    ob = _make_ob(0.48, 0.52)
    with _patch_stream([ob]):
        asyncio.run(TickCollector(db=db, ticker="0xTOKEN").run())

    ticks = db.get_ticks("0xTOKEN", limit=5)
    assert len(ticks) == 1
    t = ticks[0]
    assert t.yes_bid == pytest.approx(0.48)
    assert t.yes_ask == pytest.approx(0.52)
    assert t.yes_mid == pytest.approx(0.50)
    assert t.bid_size == 500
    assert t.ask_size == 300
