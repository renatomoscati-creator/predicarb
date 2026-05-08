"""
Backtest engine.

Replays historical tick data (CSV), simulates fills at taker prices,
and computes strategy metrics: total P&L, win rate, Sharpe ratio,
and maximum drawdown.

Tick CSV format (header required):
    ts_utc,yes_bid,yes_ask[,bid_size,ask_size]
    2025-01-01T10:00:00Z,0.45,0.55,1000,500

Benchmark can be a constant float or a CsvBenchmark instance.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from src.benchmark.csv_benchmark import BenchmarkProvider
from src.polymarket.models import OrderBook, Quote
from src.storage.models import BacktestRun
from src.strategy.edge_calculator import EdgeFilters, compute_edge


# ------------------------------------------------------------------
# Internal trade record
# ------------------------------------------------------------------


@dataclass
class TradeRecord:
    ts_utc: str
    decision: str    # "BUY_YES" | "SELL_YES"
    fill_price: float
    benchmark_prob: float
    size: int
    edge: float
    pnl: float       # edge * size (expected value at fill)


# ------------------------------------------------------------------
# Tick CSV loader
# ------------------------------------------------------------------


def _parse_ts(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _load_ticks(path: Path) -> List[dict]:
    """Return list of raw dicts from tick CSV."""
    rows = []
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _row_to_orderbook(row: dict, ticker: str) -> Optional[OrderBook]:
    try:
        ts = _parse_ts(row["ts_utc"])
        bid = float(row["yes_bid"])
        ask = float(row["yes_ask"])
        bid_size = int(float(row.get("bid_size") or 500))
        ask_size = int(float(row.get("ask_size") or 500))
    except (KeyError, ValueError, TypeError):
        return None

    return OrderBook(
        ticker=ticker,
        yes_bids=[Quote(price=bid, size=bid_size)],
        yes_asks=[Quote(price=ask, size=ask_size)],
        last_update_ts=ts,
    )


# ------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------


def _compute_metrics(trades: List[TradeRecord], ticker: str, tick_csv: Path,
                     benchmark_csv: str, filters: EdgeFilters, size: int,
                     min_edge: float, ts_start: str) -> BacktestRun:
    n = len(trades)
    if n == 0:
        return BacktestRun(
            ts_utc=ts_start,
            market_file=str(tick_csv),
            benchmark_file=benchmark_csv,
            total_trades=0,
            win_rate=0.0,
            total_pnl=0.0,
            sharpe=None,
            max_drawdown=0.0,
            params_json=json.dumps({
                "max_spread": filters.max_spread,
                "min_depth": filters.min_depth,
                "max_staleness_seconds": filters.max_staleness_seconds,
                "size": size,
                "min_edge": min_edge,
            }),
        )

    pnls = [t.pnl for t in trades]
    total_pnl = sum(pnls)
    win_rate = sum(1 for p in pnls if p > 0) / n

    # Sharpe: mean / std (trade-level, not annualised — no time-series assumptions)
    mean_pnl = total_pnl / n
    if n > 1:
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1)
        std_pnl = math.sqrt(variance)
        sharpe = mean_pnl / std_pnl if std_pnl > 0 else None
    else:
        sharpe = None

    # Max drawdown on cumulative P&L curve
    cum_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum_pnl += p
        if cum_pnl > peak:
            peak = cum_pnl
        dd = peak - cum_pnl
        if dd > max_dd:
            max_dd = dd

    return BacktestRun(
        ts_utc=ts_start,
        market_file=str(tick_csv),
        benchmark_file=benchmark_csv,
        total_trades=n,
        win_rate=win_rate,
        total_pnl=total_pnl,
        sharpe=sharpe,
        max_drawdown=max_dd,
        params_json=json.dumps({
            "max_spread": filters.max_spread,
            "min_depth": filters.min_depth,
            "max_staleness_seconds": filters.max_staleness_seconds,
            "size": size,
            "min_edge": min_edge,
        }),
    )


# ------------------------------------------------------------------
# Engine
# ------------------------------------------------------------------


class BacktestEngine:
    """
    Replay tick data and simulate a trading strategy.

    Parameters
    ----------
    benchmark       BenchmarkProvider (CsvBenchmark or ConstantBenchmark).
    filters         EdgeFilters for spread / depth / staleness checks.
    ticker          Market token_id label (used for OrderBook construction).
    size            Contracts per simulated order.
    min_edge        Minimum |edge| required to simulate a trade.
    benchmark_label Human-readable label for the benchmark source (stored in DB).
    """

    def __init__(
        self,
        benchmark: BenchmarkProvider,
        filters: EdgeFilters,
        ticker: str,
        size: int,
        min_edge: float = 0.02,
        benchmark_label: str = "constant",
    ) -> None:
        self._benchmark = benchmark
        self._filters = filters
        self._ticker = ticker
        self._size = size
        self._min_edge = min_edge
        self._benchmark_label = benchmark_label

    def run(self, tick_csv: Path) -> tuple[List[TradeRecord], BacktestRun]:
        """
        Replay `tick_csv` and return (trades, BacktestRun).

        The BacktestRun is NOT persisted here — the caller can store it.
        """
        raw_rows = _load_ticks(tick_csv)
        ts_start = datetime.now(timezone.utc).isoformat()

        trades: List[TradeRecord] = []

        for row in raw_rows:
            ob = _row_to_orderbook(row, self._ticker)
            if ob is None:
                continue

            result = compute_edge(
                orderbook=ob,
                benchmark=self._benchmark,
                filters=self._filters,
            )

            if not result.passed_filters or result.edge is None:
                continue
            if abs(result.edge) < self._min_edge:
                continue

            bid = ob.best_yes_bid
            ask = ob.best_yes_ask
            if bid is None or ask is None:
                continue

            if result.edge > 0:
                decision = "BUY_YES"
                fill_price = ask.price          # taker buy → fill at ask
                pnl = (result.benchmark_prob - fill_price) * self._size
            else:
                decision = "SELL_YES"
                fill_price = bid.price          # taker sell → fill at bid
                pnl = (fill_price - result.benchmark_prob) * self._size

            trades.append(TradeRecord(
                ts_utc=row.get("ts_utc", ""),
                decision=decision,
                fill_price=fill_price,
                benchmark_prob=result.benchmark_prob,
                size=self._size,
                edge=result.edge,
                pnl=pnl,
            ))

        run = _compute_metrics(
            trades=trades,
            ticker=self._ticker,
            tick_csv=tick_csv,
            benchmark_csv=self._benchmark_label,
            filters=self._filters,
            size=self._size,
            min_edge=self._min_edge,
            ts_start=ts_start,
        )
        return trades, run
