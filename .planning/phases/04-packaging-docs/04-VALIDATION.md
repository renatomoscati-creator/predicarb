---
phase: 4
slug: packaging-docs
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-08
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | none — using `python3 -m pytest` convention |
| **Quick run command** | `python3 -m pytest` |
| **Full suite command** | `python3 -m pytest` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest`
- **After every plan wave:** Run `python3 -m pytest` (full suite, 149+ tests must pass)
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 4-01-01 | 01 | 1 | PKG-01 | manual | `pip install -e . && predicarb --help` | ❌ W0 | ⬜ pending |
| 4-01-02 | 01 | 1 | PKG-02 | manual | `pip install -e . && predicarb --help \| grep 'run'` | ❌ W0 | ⬜ pending |
| 4-01-03 | 01 | 1 | PKG-03 | file-check | `test -f requirements.txt && cat pyproject.toml \| grep dependencies` | ❌ W0 | ⬜ pending |
| 4-02-01 | 02 | 2 | DOC-01 | file-check | `grep -i 'quickstart' README.md && grep -i 'prerequisites' README.md` | ✅ | ⬜ pending |
| 4-02-02 | 02 | 2 | DOC-02 | file-check | `grep -i 'POLYMARKET_ENV' README.md` | ✅ | ⬜ pending |
| 4-02-03 | 02 | 2 | DOC-03 | file-check | `grep -i 'benchmark' README.md \| wc -l` | ✅ | ⬜ pending |
| 4-02-04 | 02 | 2 | DOC-04 | file-check | `grep -i 'architecture\|module map\|data flow' README.md` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*Existing test infrastructure covers all phase requirements. No new test stubs needed in Wave 0.*

- PKG verification is manual (`pip install -e .` in fresh venv) — cannot be pytest-automated
- DOC verification uses file-existence checks + grep
- All 149 existing tests must continue to pass after packaging changes

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `pip install -e .` succeeds in fresh venv | PKG-02 | Requires subprocess + fresh venv | `python3 -m venv /tmp/test_venv && source /tmp/test_venv/bin/activate && pip install -e . && predicarb --help` |
| `predicarb` CLI entry point works | PKG-01, PKG-02 | Entry point wiring validated post-install | Run `predicarb --help` after install; expect all subcommands listed |
| README quickstart is accurate | DOC-01 | Content accuracy requires human judgement | Follow quickstart steps literally from a clean checkout |
