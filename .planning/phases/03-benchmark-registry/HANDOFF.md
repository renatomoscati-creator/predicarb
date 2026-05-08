# Phase 3 Handoff — Benchmark Registry

**Status:** COMPLETE
**Commit:** a88cb7a
**Tests:** 149 passed, 0 failed (136 pre-phase + 9 registry tests + 4 CLI dispatch tests)
**Verification:** 9/9 must-haves passed

---

## What was built

Central benchmark registry that decouples CLI from benchmark implementations. Adding a new benchmark source now requires zero CLI changes — only a `registry.register()` call.

---

## What was changed

### 03-01: BenchmarkRegistry core

| File | Change |
|------|--------|
| `src/benchmark/registry.py` | NEW — `BenchmarkRegistry` class + module-level `registry` singleton |
| `tests/test_benchmark_registry.py` | NEW — 9 TDD tests (constant, zq with mock, unknown key, custom register, kwargs forwarding, keys()) |

**Registry API (carry forward):**
- `registry.register(key: str, factory: Callable[..., Any])` — registers a source
- `registry.get(key: str, **kwargs)` — instantiates, raises `KeyError` if unknown
- `registry.keys()` — sorted list of registered sources
- Factories use `**_` to silently ignore unrelated kwargs — callers can pass `**vars(args)` safely
- Lazy imports inside each factory — `from src.benchmark.registry import registry` has zero network side effects

**Registered built-in sources:**
- `"constant"` → `_constant_factory` → `ConstantBenchmark(benchmark)` from `src/benchmark/csv_benchmark.py`
- `"zq"` → `_zq_factory` → `ZqLiveBenchmark(meeting_date, ttl_seconds)` from `src/benchmark/live_benchmark.py`

### 03-02: CLI refactor + run-multi flags

| File | Change |
|------|--------|
| `src/cli.py:597–607` | `_build_benchmark()` replaced `if source == "zq" / else ConstantBenchmark` with `registry.get(source, ...)` |
| `src/cli.py:911–934` | `run-multi` argparse: added `--benchmark-source` (choices: constant/zq), `--zq-meeting-date`, `--benchmark-ttl`; `--benchmark` changed to optional |
| `src/cli.py:526` | `cmd_run_multi`: replaced per-ticker `ConstantBenchmark(benchmark_prob)` with shared `_build_benchmark(args)` before ticker loop |
| `tests/test_cli_benchmark_registry.py` | NEW — 4 TDD tests (constant dispatch, zq dispatch, unknown source KeyError, run-multi accepts flags) |

### 03-03: README extension guide

| File | Change |
|------|--------|
| `README.md` | NEW — created from scratch; includes Quickstart, CLI commands table, "Adding a New Benchmark Source" section with factory example + registry.register() usage, built-in sources reference |

---

## Current codebase state

```
src/
  cli.py                   # _build_benchmark() = registry.get(); run-multi has benchmark flags
  benchmark/
    registry.py            # NEW — BenchmarkRegistry + registry singleton
    csv_benchmark.py       # BenchmarkProvider Protocol + CsvBenchmark + ConstantBenchmark
    live_benchmark.py      # CachedLiveBenchmark + ZqLiveBenchmark
    zq_benchmark.py        # CME ZQ futures math
  storage/
    db.py                  # Database (unchanged from Phase 2)
  (other modules unchanged)
tests/
  test_benchmark_registry.py      # NEW — 9 tests
  test_cli_benchmark_registry.py  # NEW — 4 tests
  test_constant_benchmark.py      # Phase 2 — 2 tests
  (145 original tests unchanged)
README.md                  # NEW — created this phase
```

---

## Key decisions (carry forward)

- `_build_benchmark()` passes individual kwargs explicitly (not `**vars(args)`) to keep the call site readable: `registry.get(source, benchmark=args.benchmark, zq_meeting_date=args.zq_meeting_date, benchmark_ttl=args.benchmark_ttl)`
- Factory `**_` signature means adding new CLI args never breaks existing factories
- `run-multi`'s `--benchmark-source` defaults to `"constant"` (same as `run`)
- A single benchmark instance is shared across all markets in `cmd_run_multi` (created once before ticker loop)
- `KeyError` from `registry.get()` propagates naturally — no wrapper needed, message includes key name + valid choices

---

## Next phase: Phase 4 — Packaging & Docs

Phase 4 targets (`PKG-*` requirements):
- Package the project properly (`pyproject.toml` or `setup.py`)
- Expand README.md stubs written this phase into full documentation
- Add CLI `--help` polish

Phase 4 research should focus on:
1. Current packaging state (is there any `pyproject.toml`/`setup.cfg`?)
2. What the README stubs reference (Phase 4 for full docs)
3. CLI help text coverage
