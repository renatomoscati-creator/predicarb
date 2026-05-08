# Phase 2 Handoff — Code Quality

**Status:** COMPLETE
**Commit:** b85dd0b
**Tests:** 136 passed, 0 failed (134 original + 2 new for ConstantBenchmark)
**Verification:** 5/5 passed

---

## What was built

Pure refactoring — no new behaviour, no regressions.

---

## What was changed

### QUAL-01: ConstantBenchmark consolidated

| File | Change |
|------|--------|
| `src/benchmark/csv_benchmark.py:78` | Added `class ConstantBenchmark` (takes `prob` arg — NOT closure capture) |
| `src/cli.py:144` | `cmd_signal`: import + `ConstantBenchmark(benchmark_prob)` |
| `src/cli.py:514` | `cmd_run_multi`: import added, call changed |
| `src/cli.py:599` | `_build_benchmark`: import added, `return ConstantBenchmark(benchmark_prob)` |
| `tests/test_constant_benchmark.py` | 2 new tests (red→green TDD) |

**Constructor shape** (carry forward): `ConstantBenchmark(prob: float)` — callers pass `benchmark_prob` explicitly.

### QUAL-02: Dashboard `_DB` replaced

| File | Change |
|------|--------|
| `src/storage/db.py:99` | `watched_tickers` DDL added to `_SCHEMA` |
| `src/storage/db.py:403–497` | 10 new methods: `positions`, `open_orders`, `signals`, `fills`, `ticks_latest`, `summary`, `watched`, `add_watched`, `remove_watched`, `backtest_runs` — all return `list[sqlite3.Row]` |
| `src/dashboard.py` | Removed `_DB` class (~95 lines) + `_WATCHED_DDL`, removed `import threading`, added `from src.storage.db import Database`, updated `__init__` to `Database(db_path) + .init()`, fixed `._p` → `._path` in BacktestTab |

### QUAL-03: Auto-resolved (Phase 1)
No code changes. Verified clean in this phase.

---

## Current codebase state

```
src/
  cli.py               # imports ConstantBenchmark from csv_benchmark; 3 inline defs gone
  dashboard.py         # no _DB class; uses src.storage.db.Database
  benchmark/
    csv_benchmark.py   # BenchmarkProvider Protocol + CsvBenchmark + ConstantBenchmark
    live_benchmark.py  # CachedLiveBenchmark + ZqLiveBenchmark
    zq_benchmark.py    # CME ZQ futures math
  storage/
    db.py              # Database class — now owns watched_tickers DDL + 10 dashboard methods
    models.py          # dataclasses (unchanged)
    writer.py
  polymarket/          # unchanged
  strategy/            # unchanged
tests/
  test_constant_benchmark.py   # NEW — 2 tests
  (134 original tests unchanged)
```

---

## Key decisions (carry forward)

- `ConstantBenchmark` lives in `src/benchmark/csv_benchmark.py` — this is where Phase 3 registry will import it from
- `Database` is now the single SQLite interface in the entire project — dashboard no longer bypasses it
- `Database._conn()` sets `row_factory = sqlite3.Row` — all dict-style column access (`r["ticker"]`) works automatically
- `Database.init()` must be called after instantiation to create all tables (including `watched_tickers`)
- Thread safety in dashboard handled by `_conn()` opening new connection per call with `check_same_thread=False`

---

## Next phase: Phase 3 — Benchmark Registry

**BENCH-01..05** targets:
- Create `src/benchmark/registry.py` with `BenchmarkRegistry` mapping string keys → factory functions
- Register `"zq"` → `ZqLiveBenchmark`, `"constant"` → `ConstantBenchmark` (already in csv_benchmark.py!)
- Refactor `_build_benchmark()` in `src/cli.py` to read from registry (not hardcoded if/elif)
- Extend `run-multi` command with `--benchmark-source` + `--zq-meeting-date` flags (currently not wired)

The `ConstantBenchmark` consolidation in Phase 2 was explicitly designed to make Phase 3 easier — registry only needs to import from `csv_benchmark`, no cleanup required.

Phase 3 research should focus on:
1. Registry pattern design (simple dict vs callable factory)
2. Exact CLI flag gap in `cmd_run_multi` vs `cmd_run`
3. How `ZqLiveBenchmark` is currently instantiated in `_build_benchmark()` — that's the template for the registry entry
