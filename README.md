# PredicArb

Polymarket arbitrage/edge trading bot. It computes an edge signal (`benchmark_prob − market_mid`) against a user-supplied benchmark probability, places limit orders on the Polymarket CLOB when edge exceeds a threshold, tracks positions and fills, and supports REST polling, WebSocket streaming, multi-market concurrent trading, backtesting on historical tick CSVs, and a full Textual TUI dashboard.

---

## Prerequisites

- Python 3.13+
- A Polymarket account (optional for dry-run and dashboard)
- Polymarket API credentials (`POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE`, `POLYMARKET_ADDRESS`) — only needed for live order placement

---

## Quickstart

```bash
# 1. Clone and install
git clone https://github.com/you/predicarb.git
cd predicarb
python3 -m venv venv
source venv/bin/activate
pip install -e .

# 2. Create a minimal config
echo "POLYMARKET_ENV=demo" > .env.demo

# 3. (Optional) Seed demo data and launch dashboard
python3 scripts/seed_demo.py
predicarb dashboard

# 4. Run a dry-run trade signal on a live market
predicarb run --token-id <CLOB_TOKEN_ID> --benchmark 0.55 --dry-run

# 5. Run tests
python3 -m pytest
```

> You can also use `python3 -m src.cli` if you haven't installed with pip.

---

## CLI Commands

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

**Example invocations:**

```bash
predicarb dashboard
predicarb run --token-id <ID> --benchmark 0.55 --dry-run
predicarb run --token-id <ID> --benchmark-source zq --zq-meeting-date 2026-06-18
predicarb run-multi --token-ids <ID1> <ID2> --benchmark-source zq --zq-meeting-date 2026-06-18
predicarb collect --token-id <ID>
predicarb backtest --csv path/to/ticks.csv --benchmark 0.55
predicarb monitor
predicarb positions
predicarb orders
predicarb health
predicarb list-markets
predicarb <command> --help   # full flag reference for any command
```

---

## Configuration Reference

All configuration is loaded from `.env.demo` (or `.env.prod`) in the project root. Environment variables set in your shell take precedence over the file.

| Variable | Default | Required | Description |
|---|---|---|---|
| `POLYMARKET_ENV` | `demo` | Yes | Environment: `demo` or `prod`. Determines which `.env.*` file loads. |
| `POLYMARKET_API_BASE_URL` | `https://clob.polymarket.com` | No | CLOB REST API base URL |
| `POLYMARKET_GAMMA_API_BASE_URL` | `https://gamma-api.polymarket.com` | No | Gamma API base URL (market listings) |
| `POLYMARKET_LOG_LEVEL` | `INFO` | No | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `POLYMARKET_API_KEY` | *(none)* | For live orders | L2 HMAC API key from Polymarket dashboard |
| `POLYMARKET_API_SECRET` | *(none)* | For live orders | L2 HMAC API secret |
| `POLYMARKET_API_PASSPHRASE` | *(none)* | For live orders | L2 HMAC passphrase |
| `POLYMARKET_ADDRESS` | *(none)* | For live orders | Wallet address (`0x...`) |

Minimal `.env.demo` for dry-run / dashboard:

```
POLYMARKET_ENV=demo
```

---

## Adding a New Benchmark Source

PredicArb uses a central `BenchmarkRegistry` to map source keys to factory functions. Adding a new source requires **only** adding an entry to the registry — no changes to the CLI are needed.

### Built-in sources

| Key | Flag example | Notes |
|-----|-------------|-------|
| `constant` | `--benchmark-source constant --benchmark 0.42` | Fixed probability, useful for backtesting |
| `zq` | `--benchmark-source zq --zq-meeting-date 2026-06-18` | CME 30-Day Fed Funds Futures implied P(cut), ~15 min delay via Yahoo Finance |

### Extension example (~10 lines)

Create your factory and register it at import time. The factory receives `**kwargs`
forwarded from the CLI namespace and must return an object with `get_prob(ts_utc: datetime) -> float`.

```python
# src/benchmark/my_source.py
from datetime import datetime
from src.benchmark.registry import registry


class MyBenchmark:
    """Returns a fixed probability drawn from an external API."""

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

Then import your module at startup (e.g., add `import src.benchmark.my_source` to `src/cli.py`
or your entry point) so the registration runs. From that point, passing
`--benchmark-source my-source` to any `run` or `run-multi` command will use your factory.

No edits to `_build_benchmark()` or any CLI parser are required.

### Registry API reference

```python
from src.benchmark.registry import registry

registry.register(key: str, factory: Callable[..., Any]) -> None
registry.get(key: str, **kwargs) -> BenchmarkProvider   # raises KeyError for unknown key
registry.keys() -> list[str]                             # sorted list of registered sources
```

Factory conventions:
- Accept `**kwargs` (tolerant of unrecognised keys — use `**_` to discard extras)
- Return any object with `get_prob(ts_utc: datetime) -> float`
- Raise `ValueError` for missing required parameters

---

## Architecture

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
    registry.py             # BenchmarkRegistry singleton — extensible source dispatch
    csv_benchmark.py        # BenchmarkProvider Protocol + ConstantBenchmark
    zq_benchmark.py         # CME 30-Day Fed Funds Futures implied probability
    live_benchmark.py       # ZqLiveBenchmark — TTL-cached wrapper

scripts/
  fed_arb_scanner.py        # CME ZQ vs Polymarket timing-mismatch scanner; --interval for continuous mode
  zq_arb_backtest.py        # Historical backtest: daily ZQ probs + Polymarket prices → simulated P&L
```

### Data Flow

1. **Tick** — `WsStream` (WebSocket) or REST poll → `OrderBook` (bid/ask ladder)
2. **Signal** — `compute_edge(orderbook, benchmark, filters)` → `EdgeResult`
   - `edge = benchmark_prob − market_mid`
   - Signal recorded to `signals` table via `Database`
3. **Order** — if `|edge| ≥ min_edge` and position limits pass → `PolymarketClient.place_order()` → `orders` table
4. **Fill** — `OrderMonitor.check_once()` polls `get_order_status()` → `record_fill()` → `PositionManager`
   - Fill deferred to monitor (no optimistic fill on placement)
5. **Position** — `PositionManager` accumulates weighted-average cost; realised P&L on close

### Concurrency Model

- `MultiMarketRunner`: one `asyncio` task per ticker via `asyncio.gather(..., return_exceptions=True)` — one market crashing doesn't stop others
- `TickWriter`: daemon thread draining `queue.Queue` → SQLite (non-blocking for WS handler)
- `BacktestTab` in dashboard: background thread via `@work(thread=True)`

---

## License

MIT
