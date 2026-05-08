"""
Unit tests for BacktestEngine.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.benchmark.csv_benchmark import BenchmarkProvider
from src.storage.db import Database
from src.strategy.backtest import BacktestEngine, _row_to_orderbook
from src.strategy.edge_calculator import EdgeFilters


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class ConstBenchmark(BenchmarkProvider):
    def __init__(self, prob: float) -> None:
        self._prob = prob

    def get_prob(self, ts_utc: datetime) -> float:
        return self._prob


def _write_tick_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = ["ts_utc", "yes_bid", "yes_ask", "bid_size", "ask_size"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _ts(i: int = 0) -> str:
    return f"2025-01-01T10:{i:02d}:00Z"


def _engine(benchmark_prob: float, min_edge: float = 0.01) -> BacktestEngine:
    return BacktestEngine(
        benchmark=ConstBenchmark(benchmark_prob),
        filters=EdgeFilters(max_spread=0.2),
        ticker="0xTOKEN",
        size=100,
        min_edge=min_edge,
    )


# ------------------------------------------------------------------
# _row_to_orderbook
# ------------------------------------------------------------------


def test_row_to_orderbook_valid():
    row = {"ts_utc": "2025-01-01T10:00:00Z", "yes_bid": "0.45", "yes_ask": "0.55",
           "bid_size": "1000", "ask_size": "500"}
    ob = _row_to_orderbook(row, "0xTOKEN")
    assert ob is not None
    assert ob.best_yes_bid.price == pytest.approx(0.45)
    assert ob.best_yes_ask.price == pytest.approx(0.55)


def test_row_to_orderbook_missing_field():
    ob = _row_to_orderbook({"ts_utc": "2025-01-01T10:00:00Z"}, "0xTOKEN")
    assert ob is None


def test_row_to_orderbook_defaults_bid_ask_size():
    row = {"ts_utc": "2025-01-01T10:00:00Z", "yes_bid": "0.45", "yes_ask": "0.55"}
    ob = _row_to_orderbook(row, "0xTOKEN")
    assert ob is not None
    assert ob.best_yes_bid.size == 500
    assert ob.best_yes_ask.size == 500


# ------------------------------------------------------------------
# BacktestEngine.run
# ------------------------------------------------------------------


def test_no_trades_when_no_edge(tmp_path):
    # benchmark = 0.50, mid = 0.50 → edge = 0 → no trade
    csv_path = tmp_path / "ticks.csv"
    _write_tick_csv(csv_path, [
        {"ts_utc": _ts(0), "yes_bid": "0.45", "yes_ask": "0.55", "bid_size": "500", "ask_size": "500"},
    ])
    trades, run = _engine(0.50).run(csv_path)
    assert len(trades) == 0
    assert run.total_trades == 0
    assert run.win_rate == 0.0
    assert run.total_pnl == 0.0


def test_buy_yes_trades(tmp_path):
    # benchmark = 0.70, ask = 0.55 → edge = 0.15 → BUY YES, pnl = 0.15 * 100 = 15
    csv_path = tmp_path / "ticks.csv"
    _write_tick_csv(csv_path, [
        {"ts_utc": _ts(i), "yes_bid": "0.45", "yes_ask": "0.55", "bid_size": "500", "ask_size": "500"}
        for i in range(3)
    ])
    trades, run = _engine(0.70).run(csv_path)
    assert len(trades) == 3
    assert all(t.decision == "BUY_YES" for t in trades)
    assert all(t.fill_price == pytest.approx(0.55) for t in trades)
    assert all(t.pnl == pytest.approx(15.0) for t in trades)
    assert run.total_pnl == pytest.approx(45.0)
    assert run.win_rate == pytest.approx(1.0)


def test_sell_yes_trades(tmp_path):
    # benchmark = 0.30, bid = 0.45 → edge = -0.15 → SELL YES, pnl = (0.45-0.30)*100 = 15
    csv_path = tmp_path / "ticks.csv"
    _write_tick_csv(csv_path, [
        {"ts_utc": _ts(i), "yes_bid": "0.45", "yes_ask": "0.55", "bid_size": "500", "ask_size": "500"}
        for i in range(2)
    ])
    trades, run = _engine(0.30).run(csv_path)
    assert len(trades) == 2
    assert all(t.decision == "SELL_YES" for t in trades)
    assert all(t.fill_price == pytest.approx(0.45) for t in trades)
    assert all(t.pnl == pytest.approx(15.0) for t in trades)


def test_min_edge_filters_marginal_trades(tmp_path):
    # benchmark = 0.52, ask = 0.55 → edge = -0.03 < min_edge 0.05 → no trade
    csv_path = tmp_path / "ticks.csv"
    _write_tick_csv(csv_path, [
        {"ts_utc": _ts(0), "yes_bid": "0.45", "yes_ask": "0.55", "bid_size": "500", "ask_size": "500"},
    ])
    engine = BacktestEngine(
        benchmark=ConstBenchmark(0.52),
        filters=EdgeFilters(max_spread=0.2),
        ticker="0xTOKEN",
        size=100,
        min_edge=0.05,
    )
    trades, run = engine.run(csv_path)
    assert len(trades) == 0


def test_spread_filter_blocks_trades(tmp_path):
    # spread = 0.4, max_spread = 0.1 → rejected
    csv_path = tmp_path / "ticks.csv"
    _write_tick_csv(csv_path, [
        {"ts_utc": _ts(0), "yes_bid": "0.20", "yes_ask": "0.80", "bid_size": "500", "ask_size": "500"},
    ])
    trades, _ = _engine(0.90).run(csv_path)
    assert len(trades) == 0


def test_empty_csv_no_trades(tmp_path):
    csv_path = tmp_path / "ticks.csv"
    _write_tick_csv(csv_path, [])
    trades, run = _engine(0.70).run(csv_path)
    assert len(trades) == 0
    assert run.total_trades == 0


def test_sharpe_computed_for_varied_trades(tmp_path):
    # Two trades with different P&Ls so std > 0 and Sharpe is defined.
    # benchmark=0.70, tick1: ask=0.55 → pnl=+15; tick2: ask=0.65 → pnl=+5
    csv_path = tmp_path / "ticks.csv"
    _write_tick_csv(csv_path, [
        {"ts_utc": _ts(0), "yes_bid": "0.45", "yes_ask": "0.55", "bid_size": "500", "ask_size": "500"},
        {"ts_utc": _ts(1), "yes_bid": "0.55", "yes_ask": "0.65", "bid_size": "500", "ask_size": "500"},
    ])
    _, run = _engine(0.70).run(csv_path)
    assert run.sharpe is not None


def test_sharpe_none_for_single_trade(tmp_path):
    csv_path = tmp_path / "ticks.csv"
    _write_tick_csv(csv_path, [
        {"ts_utc": _ts(0), "yes_bid": "0.45", "yes_ask": "0.55", "bid_size": "500", "ask_size": "500"},
    ])
    _, run = _engine(0.70).run(csv_path)
    assert run.sharpe is None  # can't compute std from 1 sample


def test_max_drawdown_computed(tmp_path):
    # 2 winning trades (pnl +15 each) then 1 losing BUY_YES (benchmark<ask).
    # For BUY_YES with negative pnl: need benchmark > mid (edge>0) but ask > benchmark.
    # bid=0.60, ask=0.75, mid=0.675, spread=0.15 ≤ 0.2; benchmark=0.70
    # edge = 0.70 - 0.675 = 0.025 > 0 → BUY at ask=0.75; pnl=(0.70-0.75)*100=-5
    csv_path = tmp_path / "ticks.csv"
    rows = [
        {"ts_utc": _ts(0), "yes_bid": "0.45", "yes_ask": "0.55", "bid_size": "500", "ask_size": "500"},
        {"ts_utc": _ts(1), "yes_bid": "0.45", "yes_ask": "0.55", "bid_size": "500", "ask_size": "500"},
        {"ts_utc": _ts(2), "yes_bid": "0.60", "yes_ask": "0.75", "bid_size": "500", "ask_size": "500"},
    ]
    _write_tick_csv(csv_path, rows)
    _, run = _engine(0.70).run(csv_path)
    # cumulative pnl: +15 → +30 → +25; peak=30, drawdown=5
    assert run.max_drawdown == pytest.approx(5.0)


def test_backtest_run_persisted_to_db(tmp_path):
    csv_path = tmp_path / "ticks.csv"
    _write_tick_csv(csv_path, [
        {"ts_utc": _ts(i), "yes_bid": "0.45", "yes_ask": "0.55", "bid_size": "500", "ask_size": "500"}
        for i in range(3)
    ])
    db = Database(tmp_path / "test.sqlite")
    db.init()

    _, run = _engine(0.70).run(csv_path)
    run_id = db.insert_backtest_run(run)

    runs = db.list_backtest_runs(limit=5)
    assert len(runs) == 1
    assert runs[0].total_trades == 3
    assert runs[0].total_pnl == pytest.approx(45.0)
