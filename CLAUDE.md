# PredicArb — Claude Code Context

## What this project is
Polymarket arbitrage/edge trading bot. Fetches live orderbook data via REST and WebSocket, computes edge vs a user-supplied benchmark probability, places limit orders on the Polymarket CLOB, tracks positions and fills, and supports backtesting on historical tick CSVs. Includes a full Textual TUI dashboard.

## Commands
```bash
python3 -m src.cli <command>          # main CLI entry point
python3 scripts/seed_demo.py          # seed SQLite with demo data for dashboard testing
python3 -m pytest                     # 121 tests, all must pass
```

## Key CLI subcommands
| Command | Purpose |
|---|---|
| `dashboard` | Launch TUI dashboard (Textual, dark iOS theme) |
| `run` | REST polling live runner; add `--ws` for WebSocket mode |
| `run-multi` | Run N tickers concurrently via WS |
| `collect` | Stream WS ticks to DB + optional CSV |
| `backtest` | Replay tick CSV through edge calculator |
| `monitor` | Poll open orders, update positions on fill |
| `positions` | Print current positions |
| `orders` | Print recent orders |
| `health` | Check API connectivity |
| `list-markets` | List active Polymarket markets with live bid/ask |

## Live benchmark sources (`run --benchmark-source`)
| Source | Flag | Description |
|---|---|---|
| `constant` | `--benchmark 0.35` | Fixed probability (default) |
| `zq` | `--zq-meeting-date 2026-06-18` | CME ZQ futures implied P(cut at meeting), refreshed every `--benchmark-ttl` seconds |

## Project name capitalisation
**PredicArb** — capital A in the middle. Never `PrediCarb`, `predicarb`, `Predicarb`.

## Architecture — module map
```
src/
  cli.py                    # argparse entry point, all cmd_* functions
  config.py                 # Settings dataclass, loads .env.demo / .env.prod
  dashboard.py              # Textual TUI (PredicArbDashboard)
  logging_config.py

  polymarket/
    client.py               # PolymarketClient: REST orderbook, place_order, get_order_status
    auth.py                 # PolymarketSigner (L2 HMAC headers)
    models.py               # OrderBook, Quote, OrderResult, HealthCheck, AccountSummary
    ws_stream.py            # WsStream: async generator yielding OrderBook from WS

  storage/
    db.py                   # Database class — all SQLite queries, single source of truth
    models.py               # Tick, Signal, Order, Fill, Position, BacktestRun dataclasses
    writer.py               # TickWriter: background thread draining queue → SQLite

  strategy/
    edge_calculator.py      # compute_edge(orderbook, benchmark, filters) → EdgeResult
    runner.py               # TradingRunner: REST poll loop; step(ob=None) accepts injected OB
    ws_runner.py            # WsTradingRunner: wraps TradingRunner, driven by WsStream
    multi_runner.py         # MultiMarketRunner: asyncio.gather over N WsTradingRunners
    position_manager.py     # PositionManager: double-entry accounting, risk limits
    order_monitor.py        # OrderMonitor: polls placed orders, records fills
    collector.py            # TickCollector: WS → DB + optional CSV
    backtest.py             # BacktestEngine: replay CSV, compute metrics

  benchmark/
    csv_benchmark.py        # BenchmarkProvider ABC + CsvBenchmark implementation
    zq_benchmark.py         # CME 30-Day Fed Funds Futures (ZQ) implied probability via Yahoo Finance
    live_benchmark.py       # CachedLiveBenchmark + ZqLiveBenchmark — TTL-cached BenchmarkProvider wrapper
    fedwatch_placeholder.py # Legacy placeholder (wraps CsvBenchmark)

scripts/
  fed_arb_scanner.py        # CME ZQ vs Polymarket timing-mismatch scanner; --interval for continuous mode
  zq_arb_backtest.py        # Historical backtest: daily ZQ probs + Polymarket prices → simulated P&L
```

## DB schema (SQLite at data/polymarket_bot.sqlite)
Tables: `ticks`, `signals`, `orders`, `fills`, `positions`, `backtest_runs`, `watched_tickers`
- `positions` uses `ON CONFLICT(ticker) DO UPDATE` (upsert)
- `watched_tickers` is created by the dashboard's `_DB` class on first launch
- `orders.status` values: `placed`, `filled`, `cancelled`, `rejected`, `dry_run`, `error`
- Polymarket fill statuses: `MATCHED` → filled, `CANCELLED`/`UNMATCHED` → cancelled, `LIVE`/`DELAYED` → open

## Core data flow
1. **Tick** — WsStream or REST → `OrderBook`
2. **Signal** — `compute_edge(ob, benchmark, filters)` → `EdgeResult` (edge = benchmark_prob − market_mid)
3. **Order** — if `|edge| ≥ min_edge` and position limits pass → `client.place_order()` → DB
4. **Fill** — `OrderMonitor.check_once()` polls `get_order_status()` → `record_fill()` → `PositionManager`
5. **Position** — weighted-average cost, realised P&L accumulated on close

## Key design decisions
- `TradingRunner.step(ob=None)`: if `ob` is provided, skips REST fetch (used by `WsTradingRunner`)
- Position updates on live orders are **deferred to OrderMonitor** — `runner._execute()` does NOT call `record_fill` after placing (avoids optimistic fill bug)
- Dry-run **does** call `record_fill` so position limits are exercised during paper trading
- `MultiMarketRunner` uses `asyncio.gather(..., return_exceptions=True)` — one market crashing doesn't stop others
- `TickWriter` is a daemon thread draining a `queue.Queue` into SQLite (non-blocking for WS handler)
- All timestamps stored as UTC ISO strings; dashboard displays in `Europe/Rome` via `zoneinfo`

## Dashboard (src/dashboard.py)
- Theme: iOS dark palette — `#000000` bg, `#2C2C2E` cards, `#0A84FF` blue, `#30D158` green, `#FF453A` red
- 3 tabs: **Overview** (KPIs + 4 panels + ticks), **Markets** (add/remove watched tickers), **Backtest** (run engine, view history)
- Button handlers live **inside their tab widget** (`MarketsTab.on_button_pressed`, `BacktestTab.on_button_pressed`) — NOT on the App. This is required for Textual event routing to work reliably
- Backtest runs in a background thread via `@work(thread=True)` on `BacktestTab`
- `_DB` class in dashboard is a lightweight direct sqlite3 wrapper (separate from `src.storage.db.Database`)
- Clock ticks every 1s; data refreshes every N seconds (default 5, `--interval` flag)

## Environment / config
- Config file: `.env.demo` (or `.env.prod`) at project root
- Minimum content: `POLYMARKET_ENV=demo`
- API credentials (`POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE`, `POLYMARKET_ADDRESS`) are optional — only needed for placing real orders
- Python 3.13, venv at `venv/`, activate with `source venv/bin/activate`
- Key deps: `requests`, `websockets`, `textual>=0.61`, `python-dotenv`, `cryptography`

## Testing conventions
- All tests use `tmp_path` fixtures for isolated SQLite DBs
- WsStream mocked by replacing `ws_runner._ws` with a `MagicMock` whose `.stream` is an `async def` generator
- `TickCollector` patched via `patch("src.strategy.collector.WsStream.stream", _stream_method)` where `_stream_method` is `async def method(self)`
- No real network calls in any test
- Run with `python3 -m pytest` (not `python` — Python 3 only)

## Polymarket API notes
- `GET /book?token_id=X` — full order book; bids/asks may contain stale stub orders at extreme prices (0.01/0.99), do not use for mid-price
- `GET /price?token_id=X&side=buy` — best bid (BUY side); `side=sell` — best ask (SELL side). Use these for real prices.
- `GET /midpoint?token_id=X` — mid-price directly
- Gamma API `clobTokenIds` is a **JSON string**, not a list — requires `json.loads()` before indexing
- Gamma API `timestamp` on orderbook is already **milliseconds** (not seconds) — do NOT multiply by 1000
- `GET /prices-history?market={token_id}&startTs=...&endTs=...&fidelity=1440` — max window ~14 days; paginate backwards for longer history

## ZQ Futures methodology
- CME 30-Day Fed Funds Futures: price = 100 − expected_avg_EFFR for delivery month
- Meeting implied rate: day-weighted formula `effr_month = (D-1)/N × pre + (N-D+1)/N × post`
- Late-month meetings (day ≥ 20, e.g. July 29-30, Oct 28-29): use NEXT month's ZQ as post-meeting reference (day-weighting is numerically unstable with only 1-2 post-days)
- Yahoo Finance tickers: `ZQ{code}{yy}.CBT` where month codes are F G H J K M N Q U V X Z
- ~15 min data delay via Yahoo Finance; sufficient for structural mispricings but not sub-minute timing

## Phases completed
1. Kalshi → Polymarket migration
2. Order placement (CLOB L2 auth)
3. Live REST trading runner
4. Backtest engine
5. Tick collector (WS → DB/CSV)
6. Position manager (risk limits, weighted-avg cost)
7. Order monitor + observability CLI
8. WebSocket-based single-market runner
9. Multi-market concurrent runner
10. Textual dashboard with dark iOS theme
11. Live ZQ benchmark + Fed arb scanner (CME ZQ vs Polymarket timing-mismatch strategy)
12. ZQ arb historical backtest (daily ZQ probs + Poly prices → entry/exit simulation, win rate, P&L)
13. Intraday backtest mode (--fidelity 60/1 for hourly/minute bars, forward-fill ZQ→Poly alignment, hours_held tracking)
