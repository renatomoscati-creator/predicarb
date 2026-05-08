---
phase: 03-benchmark-registry
plan: 01
subsystem: benchmark
tags: [registry, factory, benchmark, constant, zq, protocol]

requires:
  - phase: 02-code-quality
    provides: ConstantBenchmark in csv_benchmark.py, ZqLiveBenchmark in live_benchmark.py
provides:
  - BenchmarkRegistry class with register() and get() methods
  - Module-level registry singleton with "constant" and "zq" pre-registered
  - Extensible factory pattern for adding new benchmark sources at runtime
affects:
  - 03-02 (cli-refactor) — registry.get() will replace _build_benchmark() if/elif dispatch
  - Any future plan adding a new benchmark source

tech-stack:
  added: []
  patterns:
    - Registry pattern with factory functions — keys map to callables that accept **kwargs
    - Factory **_ to silently swallow unrelated CLI kwargs (forward-safe dispatch)
    - Lazy imports inside factories — no heavy deps at module level

key-files:
  created:
    - src/benchmark/registry.py
    - tests/test_benchmark_registry.py
  modified: []

key-decisions:
  - "Factory **_ pattern — each factory ignores unrelated kwargs so registry.get(source, **vars(args)) is safe for CLI forwarding"
  - "Lazy imports inside factories — ConstantBenchmark and ZqLiveBenchmark imported inside the factory function, not at module level, keeping registry import fast and side-effect free"

patterns-established:
  - "Registry-factory pattern: BenchmarkRegistry._factories dict stores callables; registry.get(key, **kwargs) instantiates"
  - "TDD with 9 tests: constant, zq (mocked), unknown key, custom register, kwargs forwarding, keys() listing"

requirements-completed: [BENCH-01, BENCH-02]

duration: 2min
completed: 2026-05-08
---

# Phase 3 Plan 01: BenchmarkRegistry Summary

**Dict-backed BenchmarkRegistry with "constant" and "zq" factory registrations, replacing if/elif dispatch with extensible first-class registry singleton**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-05-08T15:45:00Z
- **Completed:** 2026-05-08T15:47:00Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 2

## Accomplishments
- Created `src/benchmark/registry.py` with `BenchmarkRegistry` class (register, get, keys)
- Module-level `registry` singleton pre-registers `"constant"` and `"zq"` factories
- Factories accept `**_` to silently ignore unrelated kwargs — CLI-forward safe
- 9 tests covering all specified behaviors (constant, zq, unknown key, custom register, kwargs, keys)
- All 145 tests pass (136 prior + 9 new)

## Task Commits

1. **Task 1: BenchmarkRegistry with constant and zq registrations** - `5385916` (feat, TDD RED+GREEN combined)

**Plan metadata:** (docs commit follows)

## Files Created/Modified
- `src/benchmark/registry.py` — BenchmarkRegistry class + module-level singleton with built-in sources
- `tests/test_benchmark_registry.py` — 9 tests for registry behavior

## Decisions Made
- Factory `**_` pattern: each factory accepts and silently ignores unrelated kwargs so callers can do `registry.get(source, **vars(args))` without filtering
- Lazy imports inside factories: `ConstantBenchmark` and `ZqLiveBenchmark` imported inside each factory function to avoid module-level network-capable imports

## Deviations from Plan

None - plan executed exactly as written. Extra tests added beyond the required 4 (added `test_constant_ignores_unrelated_kwargs`, `test_zq_missing_meeting_date_raises_value_error`, `test_custom_factory_receives_kwargs`, `test_builtin_keys_present`, `test_keys_returns_sorted_list`) for completeness — all within scope.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Registry is importable with zero side effects: `from src.benchmark.registry import registry` brings no network deps
- `registry.keys()` returns `["constant", "zq"]` — ready for CLI `--benchmark-source` choices validation
- Plan 03-02 can replace `_build_benchmark()` if/elif with `registry.get(source, **vars(args))`

---
*Phase: 03-benchmark-registry*
*Completed: 2026-05-08*
