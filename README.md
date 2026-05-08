# PredicArb

Polymarket arbitrage/edge trading bot. Fetches live orderbook data via REST and WebSocket, computes edge vs a user-supplied benchmark probability, places limit orders on the Polymarket CLOB, tracks positions and fills, and supports backtesting on historical tick CSVs. Includes a full Textual TUI dashboard.

> **Note:** Full setup, deployment, and API reference docs are planned for Phase 4. The sections below are complete and accurate for the benchmark extension contract and CLI quick-reference.

---

## Quickstart

```bash
# 1. Clone and set up venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Create a minimal config
echo "POLYMARKET_ENV=demo" > .env.demo

# 3. Seed demo data and launch dashboard
python3 scripts/seed_demo.py
python3 -m src.cli dashboard

# 4. Run tests
python3 -m pytest
```

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

```bash
python3 -m src.cli <command> --help   # full flag reference for any command
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
    backtest.py             # BacktestEngine: replay CSV, compute metrics

  benchmark/
    registry.py             # BenchmarkRegistry singleton — extensible source dispatch
    csv_benchmark.py        # BenchmarkProvider Protocol + ConstantBenchmark
    zq_benchmark.py         # CME 30-Day Fed Funds Futures implied probability
    live_benchmark.py       # ZqLiveBenchmark — TTL-cached wrapper
```

---

## License

MIT
