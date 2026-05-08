# PredicArb

## What This Is

PredicArb is a Polymarket edge-trading bot that computes the gap between a market's implied probability and an external financial benchmark, then places limit orders when that gap exceeds a configurable threshold. It supports live REST and WebSocket feed ingestion, a pluggable benchmark framework (starting with CME ZQ Fed Funds Futures), position management with risk limits, backtesting on historical tick CSVs, and a Textual TUI dashboard.

The goal is a publishable, clean project that works for **any binary prediction market event** with **any financial source of truth** as a benchmark — not just FOMC meetings.

## Core Value

The edge signal: `benchmark_prob − market_mid`. Everything else is infrastructure to compute it reliably, trade on it safely, and iterate on it with backtesting.

## Requirements

### Validated

- ✓ Polymarket CLOB integration (REST + WebSocket orderbook) — existing
- ✓ Edge calculation with spread/depth/staleness filters — existing
- ✓ Limit order placement (L2 HMAC auth) — existing
- ✓ Position manager (weighted-avg cost, risk limits) — existing
- ✓ Order monitor (fill detection, P&L) — existing
- ✓ Tick collector (WS → DB + CSV) — existing
- ✓ BacktestEngine (tick CSV replay) — existing
- ✓ MultiMarketRunner (concurrent WS across N tickers) — existing
- ✓ ZQ benchmark (CME 30-Day Fed Funds Futures, day-weighted formula) — existing
- ✓ ZQ arb historical backtest script — existing
- ✓ Textual TUI dashboard (iOS dark theme) — existing
- ✓ 134 passing tests — existing

### Active

- [ ] Clean repo: delete dead Kalshi module, NAE stubs, junk dirs, stale logs, placeholder comments
- [ ] First git commit + .gitignore
- [ ] Benchmark plugin registry: named sources registered centrally, CLI auto-discovers them
- [ ] `run-multi` supports live benchmark sources (currently constant-only)
- [ ] Consolidate duplicate `ConstantBenchmark` definitions
- [ ] Dashboard reuses `src/storage/db.Database` (remove inline `_DB` wrapper)
- [ ] `pyproject.toml` with proper packaging, entry point, deps
- [ ] README: what it does, quickstart, config reference, benchmark extension guide
- [ ] GitHub Actions CI: test on push

### Out of Scope (v1)

- New benchmark sources beyond ZQ and constant — infrastructure first, sources later
- Real-time CME data feed (Yahoo Finance ~15min lag is acceptable for structural mispricings)
- Web UI (Textual TUI is sufficient)
- Multi-venue execution (Polymarket only for now)
- Mobile app — irrelevant
- Kalshi trading (separate project if ever needed)

## Context

- Migrated from Kalshi to Polymarket (Phase 1 of prior work). The full `src/kalshi/` module still exists but is entirely dead — no CLI command, strategy, or test uses it.
- The `nae/` directory is a ghost — empty `__init__.py` stubs, never built.
- Five junk directories in root (`#/`, `already/`, `haven't/`, `if/`, `you/`) appear to be accidental venvs from a misquoted shell command. Safe to delete.
- Zero git commits. The entire codebase is untracked — establishing history is step 0.
- `BenchmarkProvider` Protocol is the right extension point. `CachedLiveBenchmark` can wrap any `Callable[[], float]`. The missing piece is a **registry** so new sources don't require editing CLI internals.
- Dashboard has a duplicate SQLite wrapper (`_DB` class) that diverges from `src/storage/db.Database` — maintenance liability.

## Constraints

- **Language**: Python 3.13, keep existing deps
- **Tests**: All 134 must stay green throughout — no regressions
- **Backward compat**: CLI interface must not break (same commands, same flags); new flags are additive
- **No new benchmark sources**: v1 is framework-only; ZQ + constant remain the only implementations
- **No real network calls in tests**: mock/fixture pattern must be preserved

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Keep ZQ as only live benchmark in v1 | User confirmed: "ZQ already works (keep)" | — Pending |
| Interactive GSD mode | User preference | — Pending |
| Delete kalshi/ entirely | Zero usage, attribution error in Settings, clean break | — Pending |
| Benchmark registry via dict + factory function | Avoids heavy plugin framework; simple enough for one-person project | — Pending |

---
*Last updated: 2026-05-08 after initial audit and project initialization*
