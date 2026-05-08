# PredicArb

Polymarket edge-trading bot. Computes `edge = benchmark_prob − market_mid`, places limit orders on the Polymarket CLOB when edge exceeds a threshold, tracks positions and fills, and supports REST polling, WebSocket streaming, multi-market concurrent trading, backtesting, and a browser dashboard.

---

## Prerequisites

- Python 3.13+
- A Polymarket account (optional for dry-run / dashboard)
- Polymarket L2 API credentials — only needed for live order placement

---

## Quickstart

```bash
# 1. Clone and install
git clone https://github.com/renatomoscati-creator/predicarb.git
cd predicarb
python3 -m venv venv && source venv/bin/activate
pip install -e .

# 2. Minimal config
echo "POLYMARKET_ENV=demo" > .env.demo

# 3. (Optional) Seed demo data and open dashboard
python3 scripts/seed_demo.py
predicarb dashboard

# 4. Run tests
python3 -m pytest
```

> Also works as `python3 -m src.cli` if not installed with pip.

---

## The Core Concept

```
edge = benchmark_prob − market_mid_price
```

You supply a "true" probability (the benchmark). PredicArb trades when Polymarket's price disagrees enough:

| Edge | Direction | Action |
|------|-----------|--------|
| `edge > min_edge` | Market too cheap | **BUY YES** |
| `edge < −min_edge` | Market too expensive | **SELL YES** |
| `|edge| < min_edge` | Within noise | No trade |

---

## Typical Workflow

```
1. list-markets       ← find token_ids
2. watch              ← sanity-check prices
3. signal             ← one-shot edge check before committing
4. run --dry-run      ← paper trade, watch DB fill up
5. collect            ← gather tick data for backtesting
6. backtest           ← validate strategy on historical data
7. run --ws           ← go live (start small)
8. monitor            ← runs alongside, records fills
9. dashboard          ← observe everything in real time
```

---

## CLI Commands

| Command | Purpose |
|---|---|
| `health` | Check API connectivity and account balance |
| `list-markets` | Browse active Polymarket markets with live bid/ask |
| `watch` | Stream a single market's orderbook live |
| `signal` | One-shot edge calculation — should I trade this? |
| `run` | Live trading loop (REST or WebSocket) |
| `run-multi` | Concurrent WS trading across N markets |
| `collect` | Stream WS ticks to DB + optional CSV |
| `backtest` | Replay tick CSV through the edge strategy |
| `monitor` | Poll open orders until filled/cancelled |
| `positions` | Print current positions and P&L |
| `orders` | Print recent orders |
| `trade` | Place a single manual limit order |
| `dashboard` | Open the live browser dashboard |

---

## Command Reference

### `health`
```bash
predicarb health
```
Shows: env, API URL, latency, and account balance (if credentials configured).

---

### `list-markets`
```bash
predicarb list-markets
predicarb list-markets --keywords "fed rate cut"
predicarb list-markets --keywords "bitcoin" --limit 20
```
Output columns: Ticker (token_id), YesBid, YesAsk, Vol24h, Title.  
The **Ticker** column is the long hex token_id used by every other command.

---

### `watch`
```bash
predicarb watch --ticker <TOKEN_ID>
predicarb watch --ticker <TOKEN_ID> --interval 1.0   # 1s refresh
```
Streams bid, ask, mid, and orderbook age. Read-only — no credentials needed.

---

### `signal`
```bash
predicarb signal --ticker <TOKEN_ID> --benchmark 0.65
```
One-shot edge calculation. Output:
```
Benchmark prob  : 0.6500
Market mid prob : 0.5800
Edge            : 0.0700
Filters passed  : True (ok)
Decision        : TRADE: BUY YES
```

**Filter flags:**
```bash
--max-spread 0.05    # reject wide bid/ask spreads (default 0.10)
--min-depth 50       # require 50+ contracts on both sides
--max-staleness 30   # reject quotes older than 30s
```

---

### `run` — Single-market live trading
```bash
# REST polling (every 5s)
predicarb run --ticker <TOKEN_ID> --benchmark 0.60 --size 50

# WebSocket stream (lower latency — recommended)
predicarb run --ticker <TOKEN_ID> --benchmark 0.60 --size 50 --ws

# Paper trade (no real orders sent)
predicarb run --ticker <TOKEN_ID> --benchmark 0.65 --size 10 --dry-run --ws
```

**All flags:**
```bash
--benchmark 0.60         # your fair-value probability
--benchmark-source zq    # use live CME futures instead (see ZQ section)
--size 50                # contracts per order (1 contract = $1)
--min-edge 0.03          # minimum |edge| to trade (default 0.02)
--interval 5.0           # REST polling interval in seconds (default 5)
--max-spread 0.10        # reject wide spreads
--min-depth N            # minimum depth on both sides
--max-staleness 60       # reject stale quotes
--max-long 200           # never hold more than N YES contracts
--max-short 0            # max short position (default 0 = no shorting)
--max-loss 50.0          # halt if realized P&L drops below -N
--dry-run                # log decisions without placing orders
--ws                     # use WebSocket instead of REST
```

---

### `run-multi` — Multi-market concurrent trading
```bash
predicarb run-multi \
  --tickers <ID1> <ID2> <ID3> \
  --benchmark 0.55 \
  --size 25 \
  --min-edge 0.03 \
  --dry-run
```
All markets run concurrently via WebSocket. One crash doesn't stop the others.  
Accepts the same filter and risk flags as `run`.

---

### `collect` — Gather tick data
```bash
predicarb collect --ticker <TOKEN_ID> --csv data/my_market.csv
```
Streams live orderbook ticks to SQLite and optionally a CSV file (columns: `ts_utc, yes_bid, yes_ask, bid_size, ask_size`). Press Ctrl-C to stop. Pass the CSV to `backtest`.

---

### `backtest` — Replay historical ticks
```bash
predicarb backtest \
  --ticker <TOKEN_ID> \
  --tick-csv data/my_market.csv \
  --benchmark 0.65 \
  --size 100 \
  --min-edge 0.02 \
  --verbose
```
Reports: total trades, win rate, total P&L, Sharpe, max drawdown. Result saved to DB.

With a time-varying benchmark CSV (`ts_utc,prob`):
```bash
predicarb backtest --tick-csv data/ticks.csv --benchmark-csv data/bench.csv --size 100
```

---

### `monitor` — Track fill status
```bash
predicarb monitor             # background loop every 10s
predicarb monitor --once      # single pass (good for cron)
predicarb monitor --interval 30
```
`run` places orders but doesn't wait for fills. Run `monitor` alongside it. When an order fills, monitor records the fill and updates P&L.

---

### `positions` and `orders`
```bash
predicarb positions

predicarb orders
predicarb orders --limit 50
predicarb orders --status filled
predicarb orders --status placed    # still open
```

Status values: `placed`, `filled`, `cancelled`, `rejected`, `dry_run`, `error`

---

### `trade` — Manual single order
```bash
predicarb trade \
  --ticker <TOKEN_ID> \
  --side BUY \
  --price 0.58 \
  --size 20 \
  --tif GTC    # GTC (default), FOK, or GTD
```
Requires L2 credentials in `.env.demo`.

---

### `dashboard`
```bash
predicarb dashboard                        # web dashboard at localhost:8080
predicarb dashboard --port 9000            # custom port
predicarb dashboard --no-browser           # don't auto-open browser
predicarb dashboard --tui                  # Textual terminal dashboard instead
predicarb dashboard --tui --interval 10    # TUI refresh interval
```
First launch shows a 2-step onboarding wizard (name + timezone, then API keys). Settings are stored locally in `data/user_prefs.json`.

**Dashboard tabs:**
- **Markets** — live positions, open orders, recent signals, fills, tick feed
- **Watchlist** — add/remove markets to monitor
- **Backtest** — run backtests and view history
- **Settings (⚙)** — name, timezone, API keys
- **Guide** — full in-app command reference

---

## Benchmark Sources

### Constant (default)
Fixed probability — useful for paper trading and backtesting:
```bash
--benchmark-source constant --benchmark 0.42
```

### ZQ — CME 30-Day Fed Funds Futures
Auto-computes market-implied P(rate cut) for a specific FOMC meeting from CME ZQ futures via Yahoo Finance (~15 min delay):
```bash
predicarb run \
  --ticker <FOMC_MARKET_TOKEN_ID> \
  --benchmark-source zq \
  --zq-meeting-date 2026-09-17 \
  --size 50 \
  --benchmark-ttl 120    # re-fetch every 120s (default 60s)
```

---

## Configuration

All configuration is loaded from `.env.demo` (or `.env.prod`) in the project root. Shell env vars take precedence over the file.

| Variable | Default | Required | Description |
|---|---|---|---|
| `POLYMARKET_ENV` | `demo` | Yes | `demo` or `prod` — determines which `.env.*` file loads |
| `POLYMARKET_API_BASE_URL` | `https://clob.polymarket.com` | No | CLOB REST API base URL |
| `POLYMARKET_GAMMA_API_BASE_URL` | `https://gamma-api.polymarket.com` | No | Gamma API (market listings) |
| `POLYMARKET_LOG_LEVEL` | `INFO` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `POLYMARKET_API_KEY` | *(none)* | For live orders | L2 HMAC API key |
| `POLYMARKET_API_SECRET` | *(none)* | For live orders | L2 HMAC secret |
| `POLYMARKET_API_PASSPHRASE` | *(none)* | For live orders | L2 HMAC passphrase |
| `POLYMARKET_ADDRESS` | *(none)* | For live orders | Wallet address (`0x...`) |

Minimal `.env.demo` for dry-run / dashboard:
```
POLYMARKET_ENV=demo
```

---

## Adding a Custom Benchmark Source

PredicArb uses a central `BenchmarkRegistry`. Adding a source requires only registering a factory — no CLI changes needed.

```python
# src/benchmark/my_source.py
from datetime import datetime
from src.benchmark.registry import registry

class MyBenchmark:
    def __init__(self, api_url: str) -> None:
        self._url = api_url

    def get_prob(self, ts_utc: datetime) -> float:
        import requests
        return requests.get(self._url).json()["probability"]

def _my_factory(my_api_url: str = "", **_):
    if not my_api_url:
        raise ValueError("--my-api-url is required for --benchmark-source my-source")
    return MyBenchmark(my_api_url)

registry.register("my-source", _my_factory)
```

Then `import src.benchmark.my_source` in `src/cli.py`. Pass `--benchmark-source my-source` to any `run` command.

### Registry API
```python
registry.register(key: str, factory: Callable[..., Any]) -> None
registry.get(key: str, **kwargs) -> BenchmarkProvider   # raises KeyError for unknown key
registry.keys() -> list[str]                             # sorted list of registered sources
```

---

## Architecture

```
src/
  cli.py                    # argparse entry point, all cmd_* functions
  config.py                 # Settings dataclass, loads .env.demo / .env.prod
  dashboard.py              # Textual TUI (PredicArbDashboard)
  web_dashboard.py          # FastAPI browser dashboard
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
    runner.py               # TradingRunner: REST poll loop
    ws_runner.py            # WsTradingRunner: wraps TradingRunner, driven by WsStream
    multi_runner.py         # MultiMarketRunner: asyncio.gather over N WsTradingRunners
    position_manager.py     # PositionManager: double-entry accounting, risk limits
    order_monitor.py        # OrderMonitor: polls placed orders, records fills
    collector.py            # TickCollector: WS → DB + optional CSV
    backtest.py             # BacktestEngine: replay CSV, compute metrics

  benchmark/
    registry.py             # BenchmarkRegistry singleton
    csv_benchmark.py        # BenchmarkProvider Protocol + ConstantBenchmark
    zq_benchmark.py         # CME 30-Day Fed Funds Futures implied probability
    live_benchmark.py       # ZqLiveBenchmark — TTL-cached wrapper

scripts/
  fed_arb_scanner.py        # CME ZQ vs Polymarket timing-mismatch scanner
  zq_arb_backtest.py        # Historical backtest: daily ZQ probs + Polymarket prices
```

### Data Flow

1. **Tick** — `WsStream` or REST poll → `OrderBook`
2. **Signal** — `compute_edge(orderbook, benchmark, filters)` → `EdgeResult` → `signals` table
3. **Order** — if `|edge| ≥ min_edge` and position limits pass → `place_order()` → `orders` table
4. **Fill** — `OrderMonitor.check_once()` polls order status → `record_fill()` → `PositionManager`
5. **Position** — weighted-average cost, realised P&L accumulated on close

### Concurrency Model

- `MultiMarketRunner`: one `asyncio` task per ticker via `asyncio.gather(..., return_exceptions=True)`
- `TickWriter`: daemon thread draining `queue.Queue` → SQLite (non-blocking for WS handler)
- Browser dashboard backtest: `BackgroundTasks` in FastAPI

---

## License

MIT
