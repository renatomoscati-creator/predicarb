---
phase: 02-code-quality
plan: "02"
subsystem: storage/dashboard
tags: [refactor, db, dashboard, storage-layer]
requirements: [QUAL-02, QUAL-04]

dependency_graph:
  requires: [02-01]
  provides: [unified-db-class, watched-tickers-in-schema]
  affects: [src/storage/db.py, src/dashboard.py]

tech_stack:
  added: []
  patterns:
    - "Dashboard uses src.storage.db.Database as sole SQLite wrapper"
    - "watched_tickers DDL lives in _SCHEMA, created at Database.init()"
    - "Dashboard query methods return sqlite3.Row for dict-style column access"

key_files:
  created: []
  modified:
    - src/storage/db.py
    - src/dashboard.py

decisions:
  - "Keep sqlite3 import in dashboard.py (used for type annotations in fill/update_kpis methods)"
  - "Remove threading import — no longer needed after _DB removal"
  - "Local 'from datetime import datetime, timezone' inside add_watched() — avoids top-level import changes"
  - "_run_worker keeps its own local 'from src.storage.db import Database' import (pre-existing, harmless redundancy)"

metrics:
  duration: 115s
  completed: "2026-05-08"
  tasks_completed: 2
  files_modified: 2
---

# Phase 2 Plan 2: _DB Consolidation into Database Summary

**One-liner:** Migrated dashboard's inline sqlite3 wrapper (_DB, ~95 lines) into src.storage.db.Database with 10 new query methods and watched_tickers DDL in _SCHEMA.

## Tasks Completed

| Task | Name | Commit | Key Changes |
|------|------|--------|-------------|
| 1 | Add watched_tickers DDL and dashboard query methods to Database | 88a2315 | +108 lines to db.py: DDL in _SCHEMA + 10 methods |
| 2 | Replace _DB class usage in dashboard.py with Database | fe4616a | -115/+7 lines: removed _DB class, added Database import |

## What Was Built

### Task 1 — src/storage/db.py
- Added `watched_tickers` table DDL to `_SCHEMA` (created by `Database.init()` alongside all other tables)
- Added 10 new dashboard query methods returning `list[sqlite3.Row]`:
  - `positions()` — SELECT * FROM positions ORDER BY ticker
  - `open_orders(limit=15)` — placed orders with poly_order_id, latest first
  - `signals(limit=12)` — recent signals, latest first
  - `fills(limit=12)` — recent fills, latest first
  - `ticks_latest()` — latest tick per ticker via self-join
  - `summary()` — KPI dict: total_pnl, open_long, open_short, open_orders, markets
  - `watched()` — watched_tickers LEFT JOIN latest tick prices
  - `add_watched(ticker, label, benchmark)` — INSERT OR REPLACE
  - `remove_watched(ticker)` — DELETE
  - `backtest_runs(limit=10)` — recent backtest runs, latest first

### Task 2 — src/dashboard.py
- Removed `_WATCHED_DDL` constant and entire `_DB` class (~95 lines)
- Removed `import threading` (no longer needed)
- Added `from src.storage.db import Database` at top-level
- `PredicArbDashboard.__init__`: `_DB(db_path)` → `Database(db_path)` + `self._db.init()`
- `AddTickerTab.__init__` type annotation: `"_DB"` → `"Database"`
- `BacktestTab.__init__` type annotation: `"_DB"` → `"Database"`
- `BacktestTab._run_worker`: `self._db._p` → `self._db._path`

## Verification Results

| Check | Result |
|-------|--------|
| `grep "class _DB" src/dashboard.py` | no output |
| `grep "_WATCHED_DDL" src/dashboard.py` | no output |
| `grep "from src.storage.db import Database" src/dashboard.py` | 1 hit (line 18) |
| `grep "watched_tickers" src/storage/db.py` | 4 hits (DDL + 3 method references) |
| Method count in db.py (10 methods) | 10 |
| `python3 -m pytest` | 136 passed |
| QUAL-02: single DB class in project | PASS |
| QUAL-04: all tests green | PASS |
| Phase 2 ConstantBenchmark check | PASS (1 hit in csv_benchmark.py) |

## Deviations from Plan

None — plan executed exactly as written.

The only minor note: `_run_worker` in BacktestTab already had a local `from src.storage.db import Database` import before this change (used for `db_main = Database(...)`). The top-level import added in Task 2 means this local import is now redundant, but it is harmless and was left in place per the plan's instruction H ("No other changes needed").

## Self-Check: PASSED

- src/storage/db.py: FOUND
- src/dashboard.py: FOUND
- Commit 88a2315 (Task 1): FOUND
- Commit fe4616a (Task 2): FOUND
