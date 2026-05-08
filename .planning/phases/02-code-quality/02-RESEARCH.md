# Phase 2: Code Quality — Research

**Source:** Phase 1 handoff (rich) — researcher skipped per CLAUDE.md protocol
**Date:** 2026-05-08
**Status:** Ready for planning

---

## Current Codebase State

### QUAL-01: ConstantBenchmark deduplication

`grep -n "class ConstantBenchmark" src/cli.py` returns 3 hits:

| Line | Context |
|------|---------|
| 161  | Inside `cmd_signal()` — bare class, no Protocol inheritance |
| 545  | Inside `cmd_run()` — inherits `BenchmarkProvider` |
| 625  | Inside `cmd_run()` (WS branch) — inherits `BenchmarkProvider` |

**Fix:** Add `class ConstantBenchmark(BenchmarkProvider)` to `src/benchmark/csv_benchmark.py` (already has `BenchmarkProvider` Protocol). Import it in `src/cli.py` and remove all 3 inline definitions. The `cmd_signal()` variant doesn't inherit `BenchmarkProvider` so it needs the proper version.

**Risk:** `csv_benchmark.py` only exports `BenchmarkProvider` and `CsvBenchmark` currently. Adding `ConstantBenchmark` is additive — no circular deps since `cli.py` already imports from `src.benchmark.csv_benchmark`.

### QUAL-02: Dashboard `_DB` → `src/storage/db.Database`

`_DB` class lives at `src/dashboard.py:117`. It has these methods:

| Method | SQL |
|--------|-----|
| `positions()` | `SELECT * FROM positions ORDER BY ticker` |
| `open_orders()` | `SELECT * FROM orders WHERE status='placed'...` |
| `signals(limit)` | `SELECT * FROM signals ORDER BY ts_utc DESC LIMIT ?` |
| `fills(limit)` | `SELECT * FROM fills ORDER BY ts_utc DESC LIMIT ?` |
| `ticks_latest()` | Complex JOIN — latest tick per ticker |
| `summary()` | Aggregates from positions + orders |
| `watched()` | JOIN watched_tickers + ticks for live prices |
| `add_watched(ticker, label, benchmark)` | INSERT OR REPLACE into watched_tickers |
| `remove_watched(ticker)` | DELETE FROM watched_tickers |
| `update_watched_benchmark(ticker, benchmark)` | UPDATE watched_tickers |

`watched_tickers` DDL (currently in dashboard):
```sql
CREATE TABLE IF NOT EXISTS watched_tickers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker    TEXT    NOT NULL UNIQUE,
    label     TEXT    NOT NULL DEFAULT '',
    benchmark REAL    NOT NULL DEFAULT 0.50,
    added_at  TEXT    NOT NULL
);
```

**Fix strategy:**
1. Add `watched_tickers` DDL to `src/storage/db._SCHEMA` (string constant, executescript'd at init)
2. Add query methods to `src/storage/db.Database` that match _DB's interface exactly (or close enough that dashboard callers work with minimal change)
3. Dashboard `PredicArbDashboard.__init__` currently calls `self._db = _DB(db_path)` (line 982). Change to `self._db = Database(db_path)` from `src.storage.db`
4. Remove `class _DB` and `_WATCHED_DDL` from dashboard
5. Tab widgets receive `_db` via constructor — they use `"_DB"` as a string annotation (forward ref). Update annotations to `Database`

**Risk areas:**
- `_DB._exec` uses `executescript` for DDL, `execute` for DML. `Database` uses `sqlite3.connect` context managers — verify DDL is run at init via `_SCHEMA`.
- `_DB._q` returns `list[sqlite3.Row]` with dict-style access. `Database` methods must also return `Row` objects (not plain tuples) so dashboard column access like `r["yes_bid"]` still works.
- Thread safety: `_DB` has a `threading.Lock()`. Dashboard is Textual (async) + worker threads. `Database` must be thread-safe too — check if it has its own lock or uses WAL.
- The `watched()` method does a complex LEFT JOIN. This query should live in `Database` verbatim.

### QUAL-03: Kalshi references

**Already resolved** in Phase 1. `src/kalshi/` deleted (commit 66d3be4). Verification:
```
grep -rn "kalshi_access_key\|from src.kalshi\|import kalshi" src/
```
Returns nothing — no action needed.

### QUAL-04: Tests stay green

134 tests currently passing. Must remain 134 after Phase 2 changes. Run after each task:
```bash
python3 -m pytest
```

No new tests are required by Phase 2 requirements — but test coverage for `ConstantBenchmark` in its new home would be a natural addition.

---

## Key Design Constraints

1. **`src/storage/db.Database`** — this is the canonical SQLite interface. Dashboard must go through it, not around it.
2. **`watched_tickers` table** — must survive migration. Dashboard currently creates it via `_DB.__init__` → `_exec(_WATCHED_DDL)`. After migration, `Database.__init__` creates it via `_SCHEMA`.
3. **Thread safety** — dashboard's data refresh loop runs in a Textual worker. The Database class must handle concurrent access. Check if it uses WAL mode or a lock.
4. **Row access pattern** — dashboard code accesses columns by name (`r["ticker"]`, `r["yes_bid"]`). The replacement methods must return `sqlite3.Row` objects.
5. **No circular imports** — `src/benchmark/csv_benchmark.py` → `BenchmarkProvider` Protocol. `src/cli.py` already imports from it. Adding `ConstantBenchmark` there is safe.

---

## File Change Map

| File | Change |
|------|--------|
| `src/benchmark/csv_benchmark.py` | Add `ConstantBenchmark(BenchmarkProvider)` class |
| `src/cli.py` | Import `ConstantBenchmark`; remove 3 inline definitions (lines 161, 545, 625) |
| `src/storage/db.py` | Add `watched_tickers` DDL to `_SCHEMA`; add query methods: `positions()`, `open_orders()`, `signals()`, `fills()`, `ticks_latest()`, `summary()`, `watched()`, `add_watched()`, `remove_watched()`, `update_watched_benchmark()` |
| `src/dashboard.py` | Remove `_DB` class + `_WATCHED_DDL`; import `Database`; update type annotations; ensure `Row` dict-access preserved |

---

## Verification Commands

```bash
# QUAL-01
grep -rn "class ConstantBenchmark" src/

# QUAL-02
grep -n "_DB\|class _DB" src/dashboard.py

# QUAL-03
grep -rn "kalshi_access_key" src/

# QUAL-04
python3 -m pytest
```
