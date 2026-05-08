"""
Seed the local SQLite DB with realistic demo data so the dashboard
shows something useful out of the box.

Run from project root:
    python3 scripts/seed_demo.py
"""
from __future__ import annotations

import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "polymarket_bot.sqlite"

TICKERS = {
    "0xabc123def456aaa": "Trump wins 2026 midterms",
    "0xbcd234ef0567bbb": "Fed cuts rates in June",
    "0xcde345f01678ccc": "BTC above 100k by EOY",
}

rng = random.Random(42)


def ts(minutes_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


def run() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # ── schema (idempotent) ──────────────────────────────────────────────────
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ticks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL, ts_utc TEXT NOT NULL,
        yes_bid REAL, yes_ask REAL, yes_mid REAL,
        bid_size INTEGER, ask_size INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_ticks_ticker_ts ON ticks(ticker, ts_utc);

    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL, ts_utc TEXT NOT NULL,
        benchmark_prob REAL NOT NULL, market_mid REAL,
        edge REAL, passed_filters INTEGER NOT NULL,
        reason TEXT NOT NULL, decision TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_signals_ticker_ts ON signals(ticker, ts_utc);

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL, ts_utc TEXT NOT NULL,
        side TEXT NOT NULL, action TEXT NOT NULL,
        count INTEGER NOT NULL, price REAL NOT NULL,
        order_type TEXT NOT NULL, status TEXT NOT NULL,
        poly_order_id TEXT, signal_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS fills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL, ticker TEXT NOT NULL,
        ts_utc TEXT NOT NULL, side TEXT NOT NULL,
        count INTEGER NOT NULL, price REAL NOT NULL,
        fee_cents INTEGER NOT NULL, poly_fill_id TEXT
    );

    CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL UNIQUE, ts_utc TEXT NOT NULL,
        yes_count INTEGER NOT NULL DEFAULT 0,
        avg_cost REAL NOT NULL DEFAULT 0.0,
        realized_pnl REAL NOT NULL DEFAULT 0.0
    );
    """)

    tickers = list(TICKERS.keys())

    # ── ticks (last 30 min, one per ticker per minute) ──────────────────────
    print("Seeding ticks…")
    for ticker in tickers:
        base_bid = rng.uniform(0.40, 0.70)
        for i in range(30, 0, -1):
            drift = rng.gauss(0, 0.005)
            base_bid = max(0.05, min(0.95, base_bid + drift))
            spread = rng.uniform(0.01, 0.04)
            bid = round(base_bid, 4)
            ask = round(min(0.99, base_bid + spread), 4)
            mid = round((bid + ask) / 2, 4)
            conn.execute(
                "INSERT INTO ticks (ticker,ts_utc,yes_bid,yes_ask,yes_mid,bid_size,ask_size) "
                "VALUES (?,?,?,?,?,?,?)",
                (ticker, ts(i), bid, ask, mid,
                 rng.randint(200, 2000), rng.randint(200, 2000)),
            )

    # ── signals (last 20 min) ────────────────────────────────────────────────
    print("Seeding signals…")
    benchmarks = {t: rng.uniform(0.45, 0.75) for t in tickers}
    signal_ids: dict[str, list[int]] = {t: [] for t in tickers}

    for i in range(20, 0, -1):
        for ticker in tickers:
            bench = benchmarks[ticker]
            mid = round(rng.uniform(0.40, 0.80), 4)
            edge = round(bench - mid, 4)
            passed = abs(edge) >= 0.02
            if edge > 0.02:
                decision = "BUY_YES"
            elif edge < -0.02:
                decision = "SELL_YES"
            else:
                decision = "NO_TRADE"
            cur = conn.execute(
                "INSERT INTO signals (ticker,ts_utc,benchmark_prob,market_mid,edge,"
                "passed_filters,reason,decision) VALUES (?,?,?,?,?,?,?,?)",
                (ticker, ts(i), bench, mid, edge,
                 int(passed), "ok" if passed else "edge_too_small", decision),
            )
            if passed:
                signal_ids[ticker].append(cur.lastrowid)

    # ── orders + fills ───────────────────────────────────────────────────────
    print("Seeding orders and fills…")
    order_scenarios = [
        # (status, action, side, minutes_ago)
        ("filled",    "buy",  "BUY",  18),
        ("filled",    "sell", "SELL", 14),
        ("filled",    "buy",  "BUY",  10),
        ("filled",    "sell", "SELL",  7),
        ("placed",    "buy",  "BUY",   3),  # open order
        ("dry_run",   "buy",  "BUY",   2),
        ("cancelled", "sell", "SELL",  1),
    ]

    fill_order_ids: list[tuple[int, str, str, float, int, float]] = []

    for idx, (ticker) in enumerate(tickers):
        sig_pool = signal_ids[ticker]
        for status, action, side, age in order_scenarios:
            price = round(rng.uniform(0.42, 0.72), 4)
            count = rng.randint(50, 300)
            sig_id = sig_pool[idx % len(sig_pool)] if sig_pool else None
            poly_id = f"0xORDER{rng.randint(10000,99999)}" if status != "dry_run" else None
            cur = conn.execute(
                "INSERT INTO orders (ticker,ts_utc,side,action,count,price,"
                "order_type,status,poly_order_id,signal_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ticker, ts(age), side, action, count, price,
                 "limit", status, poly_id, sig_id),
            )
            if status == "filled":
                fill_order_ids.append((cur.lastrowid, ticker, side, price, count, age - 0.5))

    for order_id, ticker, side, price, count, age in fill_order_ids:
        fill_price = round(price + rng.gauss(0, 0.002), 4)
        conn.execute(
            "INSERT INTO fills (order_id,ticker,ts_utc,side,count,price,fee_cents,poly_fill_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (order_id, ticker, ts(age), side, count, fill_price,
             rng.randint(1, 20), f"0xFILL{rng.randint(10000,99999)}"),
        )

    # ── positions ────────────────────────────────────────────────────────────
    print("Seeding positions…")
    position_data = [
        ("0xabc123def456aaa",  150, 0.5820,  +3.47),
        ("0xbcd234ef0567bbb", -100, 0.6340,  -1.82),
        ("0xcde345f01678ccc",  300, 0.4910, +12.30),
    ]
    for ticker, net, avg, pnl in position_data:
        conn.execute(
            "INSERT INTO positions (ticker,ts_utc,yes_count,avg_cost,realized_pnl) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "ts_utc=excluded.ts_utc, yes_count=excluded.yes_count, "
            "avg_cost=excluded.avg_cost, realized_pnl=excluded.realized_pnl",
            (ticker, ts(rng.uniform(0.5, 3)), net, avg, pnl),
        )

    conn.commit()
    conn.close()
    print(f"\nDone. DB: {DB_PATH}")
    print("Run:  python3 -m src.cli dashboard")


if __name__ == "__main__":
    run()
