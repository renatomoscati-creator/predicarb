---
phase: 03-benchmark-registry
plan: 03
subsystem: benchmark
tags: [registry, benchmark, documentation, readme, extension-guide]

requires:
  - phase: 03-benchmark-registry
    plan: 01
    provides: BenchmarkRegistry with register/get/keys and module-level singleton

provides:
  - README.md with "Adding a New Benchmark Source" section
  - Built-in source reference table (constant, zq)
  - ~10-line factory extension example using registry.register()
  - Registry API quick-reference block

affects:
  - Future contributors adding new benchmark sources
  - Phase 4 docs (full API reference will extend this section)

tech-stack:
  added: []
  patterns:
    - Docs-as-contract: README extension guide tied directly to real registry API

key-files:
  created:
    - README.md
  modified: []

key-decisions:
  - "README created from scratch — file did not previously exist; minimal surrounding structure added per plan guidance"
  - "Factory convention documented as: accept **kwargs, return get_prob(ts_utc), raise ValueError for missing required params"

patterns-established:
  - "Extension pattern: create factory, call registry.register() at module level, import at startup — no CLI edits needed"

requirements-completed: [BENCH-05]

duration: 1min
completed: 2026-05-08
---

# Phase 3 Plan 03: Benchmark Extension Guide (README) Summary

**README created from scratch with benchmark extension contract: built-in source table, ~10-line factory example, and registry API reference tied to the actual BenchmarkRegistry singleton from 03-01**

## Performance

- **Duration:** ~1 min
- **Started:** 2026-05-08T17:36:05Z
- **Completed:** 2026-05-08T17:36:59Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments
- Created README.md (did not previously exist) with complete benchmark extension guide
- Section "Adding a New Benchmark Source" documents the registry.register() pattern
- Built-in source table lists `constant` and `zq` with exact flag examples
- ~10-line MyBenchmark factory example is syntactically valid and matches real registry API
- Registry API reference block (register, get, keys) with factory conventions documented
- Surrounding context added: Quickstart, CLI commands, architecture map (stubs with Phase 4 doc note)

## Task Commits

1. **Task 1: Add benchmark extension guide to README.md** - `12f7b38` (docs)

## Files Created/Modified
- `README.md` — new file; contains complete benchmark extension section plus Quickstart, CLI reference, and architecture map stubs

## Decisions Made
- README created from scratch — file did not previously exist; plan specified to create a minimal one with required sections if absent
- Added light surrounding structure (Quickstart, CLI commands table, architecture) with a note that full docs are Phase 4, keeping benchmark section as primary deliverable
- Factory convention documented explicitly: `**_` pattern, `get_prob(ts_utc: datetime) -> float`, `ValueError` for missing required params

## Deviations from Plan

None - plan executed exactly as written. README did not exist so it was created from scratch per plan instruction ("If it does not exist, create a minimal one with the required sections").

## Issues Encountered

None

## User Setup Required

None - documentation-only change.

## Next Phase Readiness
- BENCH-05 satisfied: README is the extension contract for future contributors
- The example imports from `src.benchmark.registry` and calls `registry.register()` — matches real 03-01 API exactly
- Phase 4 docs can extend the architecture section with full API reference

---
*Phase: 03-benchmark-registry*
*Completed: 2026-05-08*
