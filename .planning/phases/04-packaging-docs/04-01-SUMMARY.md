---
phase: 04-packaging-docs
plan: "01"
subsystem: infra
tags: [python-packaging, pyproject-toml, hatchling, pip, entry-point, pep517, pep518]

# Dependency graph
requires:
  - phase: 03-benchmark-registry
    provides: "CLI entry point src.cli:main + benchmark registry wiring"
provides:
  - "pyproject.toml with hatchling build backend and predicarb CLI entry point"
  - "pip install -e . support for standard Python packaging workflow"
affects: [docs, ci, deployment]

# Tech tracking
tech-stack:
  added: [hatchling]
  patterns: ["PEP 517/518 build system with src layout", "pytest as optional dev dependency"]

key-files:
  created: [pyproject.toml]
  modified: []

key-decisions:
  - "Hatchling chosen as build backend — zero-config for src layout, modern PEP 517/518"
  - "pytest moved from runtime deps to [project.optional-dependencies] dev group"
  - "requirements.txt preserved unmodified per PKG-03 requirement"
  - "packages = ['src'] tells hatchling the importable package root is src/"

patterns-established:
  - "PEP 517/518 build config: hatchling with [tool.hatch.build.targets.wheel] packages = ['src']"
  - "Entry point wired as predicarb = 'src.cli:main'"

requirements-completed: [PKG-01, PKG-02, PKG-03]

# Metrics
duration: 48s
completed: 2026-05-08
---

# Phase 4 Plan 01: Packaging Setup Summary

**pyproject.toml with hatchling build backend, predicarb CLI entry point wired to src.cli:main, and pip install -e . verified in a fresh venv**

## Performance

- **Duration:** 48s
- **Started:** 2026-05-08T19:17:45Z
- **Completed:** 2026-05-08T19:18:33Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments
- Created pyproject.toml with hatchling build backend (PEP 517/518)
- Wired `predicarb = "src.cli:main"` entry point — installs as CLI command
- Verified `pip install -e .` succeeds in a fresh Python 3.13 venv
- Verified `predicarb --help` lists all 13 subcommands post-install
- 149 existing tests continue to pass, requirements.txt untouched

## Task Commits

Each task was committed atomically:

1. **Task 1: Create pyproject.toml** - `8ea4244` (feat)

## Files Created/Modified
- `pyproject.toml` - Hatchling build config, project metadata, runtime deps, predicarb entry point, dev optional-dep group

## Decisions Made
- Hatchling over setuptools: modern, zero-config for src/ layouts, no setup.py boilerplate
- pytest moved to `[project.optional-dependencies] dev`: it's a dev tool, not needed at runtime
- requirements.txt preserved exactly as written (PKG-03 mandate)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- PredicArb is now installable as a proper Python package via `pip install -e .`
- Phase 04-02 (docs) can reference the installed CLI in usage examples
- No blockers

---
*Phase: 04-packaging-docs*
*Completed: 2026-05-08*
