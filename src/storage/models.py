"""
Row-level dataclasses for each SQLite table.
All timestamps are stored as ISO-8601 UTC strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Tick:
    ticker: str
    ts_utc: str          # ISO-8601
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    yes_mid: Optional[float]
    bid_size: Optional[int]
    ask_size: Optional[int]
    id: Optional[int] = field(default=None, repr=False)


@dataclass
class Signal:
    ticker: str
    ts_utc: str
    benchmark_prob: float
    market_mid: Optional[float]
    edge: Optional[float]
    passed_filters: bool
    reason: str
    decision: str        # 'BUY_YES' | 'SELL_YES' | 'NO_TRADE'
    id: Optional[int] = field(default=None, repr=False)


@dataclass
class Order:
    ticker: str
    ts_utc: str
    side: str            # 'yes' | 'no'
    action: str          # 'buy' | 'sell'
    count: int           # number of contracts
    price: float         # limit price in [0, 1]
    order_type: str      # 'limit' | 'market'
    status: str          # 'pending' | 'placed' | 'filled' | 'cancelled' | 'rejected'
    poly_order_id: Optional[str] = None
    signal_id: Optional[int] = None
    id: Optional[int] = field(default=None, repr=False)


@dataclass
class Fill:
    order_id: int
    ticker: str
    ts_utc: str
    side: str
    count: int
    price: float
    fee_cents: int
    poly_fill_id: Optional[str] = None
    id: Optional[int] = field(default=None, repr=False)


@dataclass
class Position:
    ticker: str
    ts_utc: str          # last update time
    yes_count: int       # net YES contracts (negative = short)
    avg_cost: float      # average cost basis per contract
    realized_pnl: float
    id: Optional[int] = field(default=None, repr=False)


@dataclass
class BacktestRun:
    ts_utc: str
    market_file: str
    benchmark_file: str
    total_trades: int
    win_rate: float
    total_pnl: float
    sharpe: Optional[float]
    max_drawdown: float
    params_json: str     # JSON blob of EdgeFilters + sizing params
    id: Optional[int] = field(default=None, repr=False)
