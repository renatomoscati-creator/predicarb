---
phase: 03-benchmark-registry
plan: "02"
subsystem: cli
tags: [benchmark, registry, refactor, cli, run-multi]
dependency_graph:
  requires:
    - "03-01 (BenchmarkRegistry with constant + zq factories)"
  provides:
    - "Registry-delegating _build_benchmark() — no source dispatch in CLI"
    - "run-multi --benchmark-source / --zq-meeting-date / --benchmark-ttl flags"
  affects:
    - src/cli.py
    - tests/test_cli_benchmark_registry.py
tech_stack:
  added: []
  patterns:
    - "Registry delegation: CLI forwards **kwargs to registry.get(), factories own validation"
    - "TDD: RED test for KeyError on unknown source → GREEN via registry.get()"
    - "Shared benchmark instance: _build_benchmark() called once, shared across all run-multi tickers"
key_files:
  created:
    - tests/test_cli_benchmark_registry.py
  modified:
    - src/cli.py
decisions:
  - "_build_benchmark() is now a pure pass-through to registry.get() — validation lives in factories"
  - "run-multi --benchmark changed from required to optional — registry validates at runtime"
  - "Shared benchmark instance for run-multi (one _build_benchmark call before ticker loop)"
metrics:
  duration: "80 seconds"
  completed_date: "2026-05-08"
  tasks_completed: 2
  files_modified: 2
---

# Phase 03 Plan 02: CLI Registry Dispatch Summary

**One-liner:** Registry-delegating `_build_benchmark()` + `run-multi` benchmark flags wired via TDD with 4 new tests.

## What Was Built

Refactored `_build_benchmark()` in `src/cli.py` from a manual `if source == "zq" / else ConstantBenchmark` dispatch to a single `registry.get(source, **kwargs)` call. Added three flags (`--benchmark-source`, `--zq-meeting-date`, `--benchmark-ttl`) to the `run-multi` argparse subparser, and replaced the hardcoded `ConstantBenchmark(benchmark_prob)` per-ticker instantiation in `cmd_run_multi` with a single shared `_build_benchmark(args)` call.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Refactor _build_benchmark() to use registry (TDD) | 5811abf | src/cli.py, tests/test_cli_benchmark_registry.py |
| 2 | Wire run-multi argparse flags and cmd_run_multi | fb79167 | src/cli.py |

## Verification Results

- `grep "_build_benchmark" src/cli.py | grep "if source"` → no results (no if/elif in function body)
- `python3 -m src.cli run-multi --help | grep benchmark-source` → flag present
- `python3 -m pytest` → 149 passed (145 pre-existing + 4 new)

## Deviations from Plan

None — plan executed exactly as written.

## TDD Flow

- **RED:** Created `tests/test_cli_benchmark_registry.py` with 4 tests. Test 4 (unknown source → KeyError) failed correctly since old code raised `ValueError` for all non-zq sources.
- **GREEN:** Replaced `_build_benchmark()` body with `registry.get(source, ...)`. All 4 tests pass.
- **REFACTOR:** No cleanup needed.

## Self-Check: PASSED

- tests/test_cli_benchmark_registry.py: FOUND
- src/cli.py: FOUND
- 03-02-SUMMARY.md: FOUND
- Commit 5811abf: FOUND
- Commit fb79167: FOUND
