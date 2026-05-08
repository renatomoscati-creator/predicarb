---
phase: 02-code-quality
plan: "01"
subsystem: benchmark
tags: [refactor, deduplication, benchmark, test]
dependency_graph:
  requires: []
  provides: [ConstantBenchmark canonical class]
  affects: [src/cli.py, src/benchmark/csv_benchmark.py]
tech_stack:
  added: []
  patterns: [Protocol duck-typing, constructor-arg over closure]
key_files:
  created:
    - tests/test_constant_benchmark.py
  modified:
    - src/benchmark/csv_benchmark.py
    - src/cli.py
decisions:
  - ConstantBenchmark uses constructor arg (prob: float) instead of closure capture — enables import-time instantiation without call-site context
  - No explicit BenchmarkProvider inheritance — Protocol structural typing is sufficient (consistent with CsvBenchmark)
  - Local imports inside functions maintained (cmd_signal, cmd_run_multi, _build_benchmark) — project convention preserved
metrics:
  duration: 73 seconds
  completed: 2026-05-08
  tasks_completed: 3
  files_modified: 3
---

# Phase 02 Plan 01: ConstantBenchmark Consolidation Summary

Single canonical `ConstantBenchmark(prob: float)` class added to `src/benchmark/csv_benchmark.py`; three closure-capturing inline definitions removed from `src/cli.py`.

## What Changed

### src/benchmark/csv_benchmark.py
- Lines 78-87: Added `ConstantBenchmark` class after `CsvBenchmark`
- Constructor takes `prob: float`; `get_prob()` ignores timestamp and returns `self._prob`
- No explicit `BenchmarkProvider` inheritance — duck-typing via Protocol is sufficient

### src/cli.py
- `cmd_signal` (was ~line 161): removed 3-line inline class; added `from src.benchmark.csv_benchmark import ConstantBenchmark`; changed call from `ConstantBenchmark()` to `ConstantBenchmark(benchmark_prob)`
- `cmd_run_multi` (was ~line 545): removed 3-line inline class; extended existing import to add `ConstantBenchmark`; changed call from `ConstantBenchmark()` to `ConstantBenchmark(benchmark_prob)`
- `_build_benchmark` (was ~line 625): removed 3-line inline class; extended existing import to add `ConstantBenchmark`; changed `return ConstantBenchmark()` to `return ConstantBenchmark(benchmark_prob)`

Net delta: +11 lines in csv_benchmark.py, -17 lines in cli.py (net -6).

### tests/test_constant_benchmark.py
New file with 2 tests verifying fixed-prob return and boundary values (0.0, 0.5, 1.0).

## ConstantBenchmark Constructor Signature

```python
class ConstantBenchmark:
    def __init__(self, prob: float) -> None:
        self._prob = prob

    def get_prob(self, ts_utc: datetime) -> float:  # noqa: ARG002
        return self._prob
```

Key differences from the removed inline versions:
- Accepts `prob` as constructor argument (canonical form) rather than capturing `benchmark_prob` from enclosing scope
- No `BenchmarkProvider` explicit inheritance (Protocol structural typing handles compatibility)

## Test Count

| Metric | Count |
|--------|-------|
| Tests before plan | 134 |
| New tests added | 2 |
| Tests after plan | 136 |
| Tests passing | 136 |

## Requirements Status

| Requirement | Status | Evidence |
|-------------|--------|---------|
| QUAL-01: Single ConstantBenchmark definition | RESOLVED | `grep -rn "class ConstantBenchmark" src/` returns exactly one line: `src/benchmark/csv_benchmark.py:78` |
| QUAL-03: No kalshi references | CONFIRMED RESOLVED | `grep -rn "kalshi_access_key\|from src.kalshi\|import kalshi" src/` returns no output |
| QUAL-04: All tests green | RESOLVED | 136 tests pass, 0 failures |

## Deviations from Plan

None - plan executed exactly as written.

## Self-Check

Files exist:
- `src/benchmark/csv_benchmark.py` — YES (modified)
- `tests/test_constant_benchmark.py` — YES (created)
- `src/cli.py` — YES (modified)

Commits exist:
- `9abae1a` — feat(02-01): add ConstantBenchmark to csv_benchmark.py
- `34c24aa` — refactor(02-01): replace 3 inline ConstantBenchmark definitions in cli.py

## Self-Check: PASSED
