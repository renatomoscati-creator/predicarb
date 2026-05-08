"""
SQLite persistence layer.

All public methods are synchronous and thread-safe (each call opens a
short-lived connection via the module-level factory, or you can inject a
connection for testing).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, List, Optional

from src.storage.models import (
    BacktestRun,
    Fill,
    Order,
    Position,
    Signal,
    Tick,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT    NOT NULL,
    ts_utc      TEXT    NOT NULL,
    yes_bid     REAL,
    yes_ask     REAL,
    yes_mid     REAL,
    bid_size    INTEGER,
    ask_size    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ticks_ticker_ts ON ticks(ticker, ts_utc);

CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    ts_utc          TEXT    NOT NULL,
    benchmark_prob  REAL    NOT NULL,
    market_mid      REAL,
    edge            REAL,
    passed_filters  INTEGER NOT NULL,   -- 0/1
    reason          TEXT    NOT NULL,
    decision        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_ticker_ts ON signals(ticker, ts_utc);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    ts_utc          TEXT    NOT NULL,
    side            TEXT    NOT NULL,
    action          TEXT    NOT NULL,
    count           INTEGER NOT NULL,
    price           REAL    NOT NULL,
    order_type      TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    poly_order_id   TEXT,
    signal_id       INTEGER REFERENCES signals(id)
);

CREATE TABLE IF NOT EXISTS fills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL REFERENCES orders(id),
    ticker          TEXT    NOT NULL,
    ts_utc          TEXT    NOT NULL,
    side            TEXT    NOT NULL,
    count           INTEGER NOT NULL,
    price           REAL    NOT NULL,
    fee_cents       INTEGER NOT NULL,
    poly_fill_id    TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL UNIQUE,
    ts_utc       TEXT    NOT NULL,
    yes_count    INTEGER NOT NULL DEFAULT 0,
    avg_cost     REAL    NOT NULL DEFAULT 0.0,
    realized_pnl REAL    NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT    NOT NULL,
    market_file     TEXT    NOT NULL,
    benchmark_file  TEXT    NOT NULL,
    total_trades    INTEGER NOT NULL,
    win_rate        REAL    NOT NULL,
    total_pnl       REAL    NOT NULL,
    sharpe          REAL,
    max_drawdown    REAL    NOT NULL,
    params_json     TEXT    NOT NULL
);
"""


class Database:
    """
    Lightweight SQLite wrapper.

    Usage:
        db = Database(Path("data/polymarket_bot.sqlite"))
        db.init()
        tick_id = db.insert_tick(tick)
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # Ticks
    # ------------------------------------------------------------------

    def insert_tick(self, tick: Tick) -> int:
        sql = """
        INSERT INTO ticks (ticker, ts_utc, yes_bid, yes_ask, yes_mid, bid_size, ask_size)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        with self._conn() as conn:
            cur = conn.execute(
                sql,
                (tick.ticker, tick.ts_utc, tick.yes_bid, tick.yes_ask,
                 tick.yes_mid, tick.bid_size, tick.ask_size),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_ticks(self, ticker: str, limit: int = 100) -> List[Tick]:
        sql = "SELECT * FROM ticks WHERE ticker=? ORDER BY ts_utc DESC LIMIT ?"
        with self._conn() as conn:
            rows = conn.execute(sql, (ticker, limit)).fetchall()
        return [
            Tick(
                id=r["id"], ticker=r["ticker"], ts_utc=r["ts_utc"],
                yes_bid=r["yes_bid"], yes_ask=r["yes_ask"], yes_mid=r["yes_mid"],
                bid_size=r["bid_size"], ask_size=r["ask_size"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def get_recent_signals(self, limit: int = 20) -> List[Signal]:
        """Return recent signals across all tickers."""
        sql = "SELECT * FROM signals ORDER BY ts_utc DESC LIMIT ?"
        with self._conn() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [
            Signal(
                id=r["id"], ticker=r["ticker"], ts_utc=r["ts_utc"],
                benchmark_prob=r["benchmark_prob"], market_mid=r["market_mid"],
                edge=r["edge"], passed_filters=bool(r["passed_filters"]),
                reason=r["reason"], decision=r["decision"],
            )
            for r in rows
        ]

    def get_recent_ticks(self, limit: int = 30) -> List[Tick]:
        """Return recent ticks across all tickers (latest first)."""
        sql = "SELECT * FROM ticks ORDER BY ts_utc DESC LIMIT ?"
        with self._conn() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [
            Tick(
                id=r["id"], ticker=r["ticker"], ts_utc=r["ts_utc"],
                yes_bid=r["yes_bid"], yes_ask=r["yes_ask"], yes_mid=r["yes_mid"],
                bid_size=r["bid_size"], ask_size=r["ask_size"],
            )
            for r in rows
        ]

    def insert_signal(self, sig: Signal) -> int:
        sql = """
        INSERT INTO signals
            (ticker, ts_utc, benchmark_prob, market_mid, edge, passed_filters, reason, decision)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._conn() as conn:
            cur = conn.execute(
                sql,
                (sig.ticker, sig.ts_utc, sig.benchmark_prob, sig.market_mid,
                 sig.edge, int(sig.passed_filters), sig.reason, sig.decision),
            )
            return cur.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def insert_order(self, order: Order) -> int:
        sql = """
        INSERT INTO orders
            (ticker, ts_utc, side, action, count, price, order_type, status,
             poly_order_id, signal_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._conn() as conn:
            cur = conn.execute(
                sql,
                (order.ticker, order.ts_utc, order.side, order.action,
                 order.count, order.price, order.order_type, order.status,
                 order.poly_order_id, order.signal_id),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_open_orders(self) -> List[Order]:
        """Return all orders with status='placed' that have a poly_order_id."""
        sql = "SELECT * FROM orders WHERE status='placed' AND poly_order_id IS NOT NULL ORDER BY ts_utc"
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        return [
            Order(
                id=r["id"], ticker=r["ticker"], ts_utc=r["ts_utc"],
                side=r["side"], action=r["action"], count=r["count"],
                price=r["price"], order_type=r["order_type"], status=r["status"],
                poly_order_id=r["poly_order_id"], signal_id=r["signal_id"],
            )
            for r in rows
        ]

    def get_recent_orders(self, limit: int = 20, status: Optional[str] = None) -> List[Order]:
        """Return recent orders, optionally filtered by status."""
        if status is not None:
            sql = "SELECT * FROM orders WHERE status=? ORDER BY ts_utc DESC LIMIT ?"
            args = (status, limit)
        else:
            sql = "SELECT * FROM orders ORDER BY ts_utc DESC LIMIT ?"
            args = (limit,)
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [
            Order(
                id=r["id"], ticker=r["ticker"], ts_utc=r["ts_utc"],
                side=r["side"], action=r["action"], count=r["count"],
                price=r["price"], order_type=r["order_type"], status=r["status"],
                poly_order_id=r["poly_order_id"], signal_id=r["signal_id"],
            )
            for r in rows
        ]

    def get_all_positions(self) -> List[Position]:
        """Return all positions."""
        sql = "SELECT * FROM positions ORDER BY ticker"
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        return [
            Position(
                id=r["id"], ticker=r["ticker"], ts_utc=r["ts_utc"],
                yes_count=r["yes_count"], avg_cost=r["avg_cost"],
                realized_pnl=r["realized_pnl"],
            )
            for r in rows
        ]

    def update_order_status(
        self,
        order_id: int,
        status: str,
        poly_order_id: Optional[str] = None,
    ) -> None:
        if poly_order_id is not None:
            sql = "UPDATE orders SET status=?, poly_order_id=? WHERE id=?"
            args = (status, poly_order_id, order_id)
        else:
            sql = "UPDATE orders SET status=? WHERE id=?"
            args = (status, order_id)
        with self._conn() as conn:
            conn.execute(sql, args)

    # ------------------------------------------------------------------
    # Fills
    # ------------------------------------------------------------------

    def get_recent_fills(self, limit: int = 20) -> List[Fill]:
        """Return recent fills across all tickers."""
        sql = "SELECT * FROM fills ORDER BY ts_utc DESC LIMIT ?"
        with self._conn() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [
            Fill(
                id=r["id"], order_id=r["order_id"], ticker=r["ticker"],
                ts_utc=r["ts_utc"], side=r["side"], count=r["count"],
                price=r["price"], fee_cents=r["fee_cents"],
                poly_fill_id=r["poly_fill_id"],
            )
            for r in rows
        ]

    def insert_fill(self, fill: Fill) -> int:
        sql = """
        INSERT INTO fills (order_id, ticker, ts_utc, side, count, price, fee_cents, poly_fill_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._conn() as conn:
            cur = conn.execute(
                sql,
                (fill.order_id, fill.ticker, fill.ts_utc, fill.side,
                 fill.count, fill.price, fill.fee_cents, fill.poly_fill_id),
            )
            return cur.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Positions (upsert — one row per ticker)
    # ------------------------------------------------------------------

    def upsert_position(self, pos: Position) -> None:
        sql = """
        INSERT INTO positions (ticker, ts_utc, yes_count, avg_cost, realized_pnl)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            ts_utc       = excluded.ts_utc,
            yes_count    = excluded.yes_count,
            avg_cost     = excluded.avg_cost,
            realized_pnl = excluded.realized_pnl
        """
        with self._conn() as conn:
            conn.execute(
                sql,
                (pos.ticker, pos.ts_utc, pos.yes_count, pos.avg_cost, pos.realized_pnl),
            )

    def get_position(self, ticker: str) -> Optional[Position]:
        sql = "SELECT * FROM positions WHERE ticker=?"
        with self._conn() as conn:
            row = conn.execute(sql, (ticker,)).fetchone()
        if row is None:
            return None
        return Position(
            id=row["id"], ticker=row["ticker"], ts_utc=row["ts_utc"],
            yes_count=row["yes_count"], avg_cost=row["avg_cost"],
            realized_pnl=row["realized_pnl"],
        )

    # ------------------------------------------------------------------
    # Backtest runs
    # ------------------------------------------------------------------

    def insert_backtest_run(self, run: BacktestRun) -> int:
        sql = """
        INSERT INTO backtest_runs
            (ts_utc, market_file, benchmark_file, total_trades, win_rate,
             total_pnl, sharpe, max_drawdown, params_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._conn() as conn:
            cur = conn.execute(
                sql,
                (run.ts_utc, run.market_file, run.benchmark_file, run.total_trades,
                 run.win_rate, run.total_pnl, run.sharpe, run.max_drawdown, run.params_json),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def list_backtest_runs(self, limit: int = 20) -> List[BacktestRun]:
        sql = "SELECT * FROM backtest_runs ORDER BY ts_utc DESC LIMIT ?"
        with self._conn() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [
            BacktestRun(
                id=r["id"], ts_utc=r["ts_utc"], market_file=r["market_file"],
                benchmark_file=r["benchmark_file"], total_trades=r["total_trades"],
                win_rate=r["win_rate"], total_pnl=r["total_pnl"], sharpe=r["sharpe"],
                max_drawdown=r["max_drawdown"], params_json=r["params_json"],
            )
            for r in rows
        ]
