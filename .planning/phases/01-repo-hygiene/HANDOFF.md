# Phase 1 Handoff — Repo Hygiene

**Status:** COMPLETE  
**Commit:** 66d3be4  
**Tests:** 134 passed, 0 failed

---

## What was built

Phase 1 was pure deletion + cleanup. No new logic. All 134 tests still pass.

---

## What was deleted

| Item | Reason |
|------|--------|
| `#/` `already/` `haven't/` `if/` `you/` | Junk dirs — accidental venvs from misquoted shell command |
| `src/kalshi/` | Dead code — migrated to Polymarket months ago; nothing imports it; `KalshiClient` would `AttributeError` on instantiation |
| `nae/` | Empty stubs — never imported anywhere |
| `src/benchmark/fedwatch_placeholder.py` | Zero-value wrapper around `CsvBenchmark`, superseded by `zq_benchmark.py` |
| `src/benchmark/kalshi_fedwatch.py` | Kalshi elections API — never wired to CLI or tests |
| `logs/kalshi_bot.log` | Old Kalshi artifact; `logs/` dir kept |

## What was changed

| File | Change |
|------|--------|
| `src/storage/models.py:51` | Removed `# PLACEHOLDER: confirm Polymarket order ID field name` from `poly_order_id` |
| `src/storage/models.py:65` | Removed `# PLACEHOLDER: confirm Polymarket fill ID field name` from `poly_fill_id` |
| `src/storage/models.py:82` | Removed `# PLACEHOLDER: renamed from kalshi_file` from `market_file` |
| `src/cli.py:752` | `--event-ticker` help → `"Filter by event tag (Gamma API 'tag' param)."` |
| `src/cli.py:758` | `--series-ticker` help → `"Filter by series (not directly supported by Gamma API; ignored)."` |
| `src/cli.py:766` | `--ticker` (watch) help → `"YES outcome token_id to watch (from list-markets)."` |
| `src/cli.py:783` | `--ticker` (signal) help → `"YES outcome token_id to compute edge for (from list-markets)."` |
| `.gitignore` | Created: venv, data, logs, graphify-out, pyc, __pycache__, .DS_Store, .env.*, *.sqlite, egg-info, dist, build, .coverage, htmlcov |

---

## Current codebase state

```
src/
  cli.py              # ~1100 lines, argparse entry, all cmd_* functions
  config.py           # Settings dataclass
  dashboard.py        # Textual TUI — still has _DB problem (Phase 2 target)
  benchmark/
    csv_benchmark.py  # BenchmarkProvider Protocol + CsvBenchmark
    live_benchmark.py # CachedLiveBenchmark + ZqLiveBenchmark
    zq_benchmark.py   # CME ZQ futures math
    # fedwatch_placeholder.py and kalshi_fedwatch.py DELETED
  polymarket/         # client, auth, models, ws_stream (unchanged)
  storage/
    db.py             # Database class (SQLite)
    models.py         # Tick, Signal, Order, Fill, Position, BacktestRun — PLACEHOLDERs removed
    writer.py
  strategy/           # edge_calculator, runner, ws_runner, multi_runner, etc. (unchanged)
scripts/              # fed_arb_scanner.py, zq_arb_backtest.py, seed_demo.py (unchanged)
tests/                # 134 tests, all pass
```

---

## Key decisions made (carry forward)

- `ConstantBenchmark` best placed in `src/benchmark/csv_benchmark.py` next to `BenchmarkProvider` Protocol — avoids circular deps when `cli.py` imports it
- `watched_tickers` table must migrate from dashboard `_DB` into `src/storage/db._SCHEMA` — don't drop this table
- Interactive mode (not YOLO) — user approves at phase checkpoints
- ZQ only in v1 — no new benchmark sources this milestone

---

## Open items / tech debt

- `src/dashboard.py` has `class _DB:` (~line 40) — separate sqlite3 wrapper with ~8 methods. Needs replacement with `src/storage/db.Database` (Phase 2, QUAL-02)
- `ConstantBenchmark` defined inline 3× in `src/cli.py` — duplicate logic (Phase 2, QUAL-01)

---

## Next phase: Phase 2 — Code Quality (QUAL-01..04)

**QUAL-01** — Deduplicate `ConstantBenchmark`:
- Add `class ConstantBenchmark(BenchmarkProvider)` to `src/benchmark/csv_benchmark.py`
- Remove 3 inline definitions from `src/cli.py` (~lines 162, 545, 625)

**QUAL-02** — Replace dashboard `_DB` with `src/storage/db.Database`:
- Add `watched_tickers` table to `src/storage/db._SCHEMA`
- Wire all `_DB` method calls to `Database` equivalents
- Dashboard instantiates `Database` at startup, passes to tabs

**QUAL-03** — Auto-resolved (kalshi module deleted in Phase 1)

**QUAL-04** — Verification: `python3 -m pytest` must show 134 passed

Grep to find inline `ConstantBenchmark` defs:
```
grep -n "class ConstantBenchmark" src/cli.py
```

Grep to find `_DB` usages in dashboard:
```
grep -n "_DB\|class _DB" src/dashboard.py | head -30
```
