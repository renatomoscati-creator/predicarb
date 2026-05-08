# Requirements: PredicArb

**Defined:** 2026-05-08
**Core Value:** The edge signal: `benchmark_prob − market_mid`. Everything else is infrastructure to compute it reliably, trade on it safely, and iterate with backtesting.

## v1 Requirements

### Hygiene

- [ ] **HYG-01**: Junk directories (`#/`, `already/`, `haven't/`, `if/`, `you/`) deleted from repo root
- [ ] **HYG-02**: Dead `src/kalshi/` module (client, auth, models, ws_stream) deleted
- [ ] **HYG-03**: Dead `nae/` directory deleted
- [ ] **HYG-04**: Dead benchmark files (`fedwatch_placeholder.py`, `kalshi_fedwatch.py`) deleted
- [ ] **HYG-05**: Stale `logs/kalshi_bot.log` deleted
- [ ] **HYG-06**: PLACEHOLDER comments in `src/storage/models.py` and `src/cli.py` resolved/removed
- [ ] **HYG-07**: `.gitignore` created covering `venv/`, `data/`, `logs/`, `*.pyc`, `.DS_Store`, `.env.*`
- [ ] **HYG-08**: First git commit establishes history with clean working tree

### Code Quality

- [x] **QUAL-01**: Duplicate `ConstantBenchmark` class (defined 3× inline in `src/cli.py`) consolidated into a single module-level definition
- [x] **QUAL-02**: Dashboard `_DB` inline class replaced with `src/storage/db.Database` (dashboard reads positions/orders/ticks/backtest_runs from the same DB abstraction)
- [x] **QUAL-03**: `KalshiClient` reference to non-existent `settings.kalshi_access_key` eliminated (deletion of `src/kalshi/` handles this)
- [x] **QUAL-04**: All 134 existing tests remain green throughout the cleanup

### Benchmark Framework

- [x] **BENCH-01**: `BenchmarkRegistry` added to `src/benchmark/registry.py` — maps string keys to factory functions
- [x] **BENCH-02**: ZQ benchmark registered under key `"zq"`, constant benchmark under `"constant"` — both via registry
- [ ] **BENCH-03**: `_build_benchmark()` in `src/cli.py` reads from registry; adding a new source requires only registering it, not editing CLI internals
- [ ] **BENCH-04**: `run-multi` command supports `--benchmark-source` and `--zq-meeting-date` (same flag surface as `run`)
- [x] **BENCH-05**: Benchmark registry is documented with a clear extension example in README

### Packaging

- [ ] **PKG-01**: `pyproject.toml` created with project metadata, dependencies (from `requirements.txt`), and `predicarb` entry point mapped to `src/cli.py:main`
- [ ] **PKG-02**: Project installable via `pip install -e .` — `predicarb` command works after install
- [ ] **PKG-03**: `requirements.txt` preserved for backward compat but `pyproject.toml` is canonical

### Documentation

- [ ] **DOC-01**: `README.md` covers: what PredicArb is, prerequisites, quickstart (`pip install -e .` + demo run), all CLI commands with examples
- [ ] **DOC-02**: README includes a config reference section (all env vars with defaults)
- [ ] **DOC-03**: README includes a benchmark extension guide (how to add a new source in ~10 lines)
- [ ] **DOC-04**: README includes architecture overview (module map, data flow)

### CI

- [ ] **CI-01**: GitHub Actions workflow `.github/workflows/test.yml` runs `pytest` on push to `main` and on PRs
- [ ] **CI-02**: CI matrix covers Python 3.13 on ubuntu-latest

## v2 Requirements

### Benchmark Sources

- **BSRC-01**: Betfair/Kalshi odds adapter — pull implied probability from another prediction market
- **BSRC-02**: Generic asset price oracle — `price > threshold` → probability (BTC, SPX, etc.)
- **BSRC-03**: Macro indicator adapter — CPI surprise, NFP vs consensus → binary probability

### Advanced Features

- **ADV-01**: Web dashboard (replace Textual TUI for remote access)
- **ADV-02**: Slack/Telegram alerts on fill, large edge signal, risk limit breach
- **ADV-03**: Multi-venue execution (Kalshi + Polymarket simultaneous)
- **ADV-04**: Real-time CME data feed (replace Yahoo Finance ~15min lag)

## Out of Scope

| Feature | Reason |
|---------|--------|
| New benchmark sources (Betfair, on-chain, macro) | v1 is framework-only; ZQ + constant sufficient to prove pattern |
| Mobile app | Web-first |
| Kalshi trading | Separate project; keep kalshi module deletion clean |
| Real-time CME feed | Yahoo Finance sufficient for structural mispricings |
| OAuth / user auth | Single-user CLI tool |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| HYG-01 | Phase 1 | Pending |
| HYG-02 | Phase 1 | Pending |
| HYG-03 | Phase 1 | Pending |
| HYG-04 | Phase 1 | Pending |
| HYG-05 | Phase 1 | Pending |
| HYG-06 | Phase 1 | Pending |
| HYG-07 | Phase 1 | Pending |
| HYG-08 | Phase 1 | Pending |
| QUAL-01 | Phase 2 | Complete (02-01) |
| QUAL-02 | Phase 2 | Complete |
| QUAL-03 | Phase 2 | Complete (02-01) |
| QUAL-04 | Phase 2 | Complete (02-01) |
| BENCH-01 | Phase 3 | Complete |
| BENCH-02 | Phase 3 | Complete |
| BENCH-03 | Phase 3 | Pending |
| BENCH-04 | Phase 3 | Pending |
| BENCH-05 | Phase 3 | Complete |
| PKG-01 | Phase 4 | Pending |
| PKG-02 | Phase 4 | Pending |
| PKG-03 | Phase 4 | Pending |
| DOC-01 | Phase 4 | Pending |
| DOC-02 | Phase 4 | Pending |
| DOC-03 | Phase 4 | Pending |
| DOC-04 | Phase 4 | Pending |
| CI-01 | Phase 5 | Pending |
| CI-02 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 26 total
- Mapped to phases: 26
- Unmapped: 0 ✓

---
*Requirements defined: 2026-05-08*
*Last updated: 2026-05-08 — traceability expanded to individual requirement rows after roadmap creation*
