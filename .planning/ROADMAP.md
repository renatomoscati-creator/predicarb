# Roadmap: PredicArb

## Overview

PredicArb is a working, tested Polymarket edge-trading bot. This roadmap takes it from a private working codebase to a clean, publishable project. The five phases move in a strict dependency order: first purge dead code so the repo is honest (Phase 1), then fix internal quality issues that would embarrass a reader (Phase 2), then build the benchmark registry that unlocks run-multi live signals (Phase 3), then package and document so anyone can install and extend it (Phase 4), and finally wire CI so it stays green (Phase 5).

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Repo Hygiene** - Delete all dead code and junk, create .gitignore, make first clean git commit
- [x] **Phase 2: Code Quality** - Eliminate duplicate ConstantBenchmark, replace dashboard _DB wrapper, confirm all tests stay green (completed 2026-05-08)
- [ ] **Phase 3: Benchmark Registry** - Add BenchmarkRegistry, wire CLI through it, extend run-multi with live benchmark support
- [ ] **Phase 4: Packaging & Docs** - pyproject.toml with predicarb entry point, complete README with quickstart/config/extension guide/architecture
- [ ] **Phase 5: CI & Ship** - GitHub Actions CI on push/PR for Python 3.13, project ready to publish

## Phase Details

### Phase 1: Repo Hygiene
**Goal**: The repository contains only live, referenced code — no dead modules, no junk directories, no stale logs — and its full history starts from a single honest commit.
**Depends on**: Nothing (first phase)
**Requirements**: HYG-01, HYG-02, HYG-03, HYG-04, HYG-05, HYG-06, HYG-07, HYG-08
**Success Criteria** (what must be TRUE):
  1. Running `find . -maxdepth 1 -type d` shows no junk directories (`#/`, `already/`, `haven't/`, `if/`, `you/`)
  2. `src/kalshi/`, `nae/`, `logs/kalshi_bot.log`, `src/benchmark/fedwatch_placeholder.py` do not exist
  3. `src/storage/models.py` and `src/cli.py` contain no PLACEHOLDER comments
  4. `.gitignore` exists and covers `venv/`, `data/`, `logs/`, `*.pyc`, `.DS_Store`, `.env.*`
  5. `git log --oneline` shows exactly one commit with a clean working tree (`git status` is clean)
**Plans**: TBD

### Phase 2: Code Quality
**Goal**: The codebase has a single source of truth for ConstantBenchmark, the dashboard reads positions and orders through the shared Database abstraction, and all 134 tests remain green after every change.
**Depends on**: Phase 1
**Requirements**: QUAL-01, QUAL-02, QUAL-03, QUAL-04
**Success Criteria** (what must be TRUE):
  1. `grep -rn "class ConstantBenchmark" src/` returns exactly one definition
  2. `src/dashboard.py` contains no `_DB` class definition; dashboard data calls go through `src/storage/db.Database`
  3. No reference to `settings.kalshi_access_key` or any `kalshi` import exists anywhere in the live codebase
  4. `python3 -m pytest` exits 0 with 134 tests passing
**Plans**: 2 plans

Plans:
- [x] 02-PLAN-01.md — Consolidate ConstantBenchmark to csv_benchmark.py; verify QUAL-03 clean
- [ ] 02-PLAN-02.md — Replace dashboard _DB with Database; add watched_tickers to storage layer

### Phase 3: Benchmark Registry
**Goal**: A central registry maps benchmark source keys to factory functions; the CLI reads from it; `run-multi` supports live benchmark sources; adding a new benchmark requires no CLI edits.
**Depends on**: Phase 2
**Requirements**: BENCH-01, BENCH-02, BENCH-03, BENCH-04, BENCH-05
**Success Criteria** (what must be TRUE):
  1. `src/benchmark/registry.py` exists and exports a `BenchmarkRegistry` with `"zq"` and `"constant"` registered
  2. `python3 -m src.cli run --benchmark-source zq --zq-meeting-date 2026-06-18 --dry-run` resolves benchmark without error
  3. `python3 -m src.cli run-multi --benchmark-source constant --dry-run` resolves benchmark and starts without error
  4. Adding a new benchmark source requires only registering it in `registry.py` — no changes to `_build_benchmark()` in `cli.py`
  5. `python3 -m pytest` exits 0 (no regressions introduced)
**Plans**: 3 plans

Plans:
- [ ] 03-01-PLAN.md — Create BenchmarkRegistry + register constant/zq + TDD tests (BENCH-01, BENCH-02)
- [ ] 03-02-PLAN.md — Refactor _build_benchmark() to use registry + wire run-multi flags (BENCH-03, BENCH-04)
- [ ] 03-03-PLAN.md — Add benchmark extension guide section to README.md (BENCH-05)

### Phase 4: Packaging & Docs
**Goal**: PredicArb is installable as a Python package via `pip install -e .` and ships a README that lets a new user run their first trade in under ten minutes.
**Depends on**: Phase 3
**Requirements**: PKG-01, PKG-02, PKG-03, DOC-01, DOC-02, DOC-03, DOC-04
**Success Criteria** (what must be TRUE):
  1. `pip install -e .` succeeds in a fresh venv; `predicarb --help` then lists all CLI commands
  2. `requirements.txt` still exists and `pyproject.toml` is the canonical dependency source
  3. `README.md` exists with sections: what it is, prerequisites, quickstart, all CLI commands with examples
  4. README contains a config reference listing every env var with its default
  5. README contains a benchmark extension example showing how to add a new source in ~10 lines
**Plans**: TBD

### Phase 5: CI & Ship
**Goal**: Every push to main and every pull request automatically runs the full test suite on Python 3.13; the project is in a state ready to be made public.
**Depends on**: Phase 4
**Requirements**: CI-01, CI-02
**Success Criteria** (what must be TRUE):
  1. `.github/workflows/test.yml` exists and triggers on `push` to `main` and on `pull_request`
  2. CI matrix specifies `python-version: ["3.13"]` and `os: ubuntu-latest`
  3. A pushed commit causes the GitHub Actions check to run and pass (green badge)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Repo Hygiene | 1/1 | Complete | 2026-05-08 |
| 2. Code Quality | 2/2 | Complete    | 2026-05-08 |
| 3. Benchmark Registry | 0/3 | Not started | - |
| 4. Packaging & Docs | 0/? | Not started | - |
| 5. CI & Ship | 0/? | Not started | - |
