# Phase 4 Handoff — Packaging & Docs

**Status:** COMPLETE
**Commit:** d70dc3a
**Tests:** 149 passed, 0 failed (all pre-phase tests unchanged)
**Verification:** 7/7 must-haves passed; PKG-02 confirmed live in fresh venv

---

## What was built

PredicArb is now installable as a proper Python package (`pip install -e .`) and ships complete documentation. The `predicarb` command is available after install without invoking `python3 -m src.cli`.

---

## What was changed

### 04-01: pyproject.toml packaging

| File | Change |
|------|--------|
| `pyproject.toml` | NEW — hatchling build backend, all runtime deps, `predicarb = "src.cli:main"` entry point |

**Key decisions:**
- Build backend: `hatchling` (PEP 517/518, zero-config for `src/` layout, no `setup.py`)
- `packages = ["src"]` in `[tool.hatchling.build.targets.wheel]` — whole `src/` package included
- `pytest` moved to `[project.optional-dependencies] dev` (not a runtime dep)
- `requirements.txt` left untouched per PKG-03 — `pyproject.toml` is canonical, `requirements.txt` preserved for backward compat
- Entry point: `predicarb = "src.cli:main"` wired to `main()` at `src/cli.py:1097`

**Verified live:** `pip install -e .` in fresh venv → `predicarb --help` lists all 13 subcommands.

### 04-02: README documentation

| File | Change |
|------|--------|
| `README.md` | EXPANDED — 228 lines of full docs (was ~80-line Phase 3 stub) |

**Sections added/updated:**
- **Prerequisites** — Python 3.13+, venv, optional Polymarket credentials
- **Quickstart** — rewritten for `pip install -e .` + `predicarb` command path
- **Configuration Reference** — 8-row table of all `POLYMARKET_*` env vars with defaults
- **CLI examples** — block with all subcommand invocations
- **Architecture** — module map + new Data Flow (5-step pipeline) + Concurrency Model subsections
- **Benchmark extension guide** — Phase 3 content preserved verbatim (DOC-03)
- **Removed** — "Phase 4 for full docs" placeholder banner

---

## Current codebase state

```
pyproject.toml                    # NEW — pip-installable, predicarb entry point
requirements.txt                  # UNCHANGED — preserved for backward compat
README.md                         # EXPANDED — full docs, 228 lines
src/
  cli.py:1097                     # main() — entry point target (unchanged)
  benchmark/registry.py           # Phase 3 (unchanged)
  storage/db.py                   # Phase 2 (unchanged)
  (all other modules unchanged)
```

---

## Key decisions (carry forward)

- `hatchling` chosen over `setuptools` — simpler for `src/` layout, no `src/__init__.py` workarounds
- `predicarb` entry point name is canonical — README, docs, and CLI all use it
- `requirements.txt` is kept but `pyproject.toml` is the truth for deps — any new dep goes in pyproject.toml first

---

## Next phase: Phase 5 — CI & Ship

Phase 5 targets (`CI-01`, `CI-02`):
- Set up GitHub Actions CI (pytest on push/PR)
- Ship a v1.0 release (tag, changelog, GitHub release)

Phase 5 research should check:
1. Existing `.github/` directory (likely none)
2. Whether any CI config exists
3. Current git tags (for v1.0 baseline)
