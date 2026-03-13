# Project Audit — 2026-03

## Executive snapshot

- **Foundation is strong**: broad domain coverage (combat, dungeon, wilderness, town, inventory/equipment ownership) and an extensive automated test suite.
- **Delivery risk is now concentrated in maintainability**: a very large monolithic `Game` class and transitional compatibility layers still in place.
- **Most immediate correctness issue (now fixed in this branch)**: `Game._town_identify_items()` referenced `item_is_magic` without importing it.

## What is complete / production-ready enough

1. **Rule/content breadth is substantial**
   - Combat, spellcasting, dungeon/wilderness exploration, and town progression are all present as first-class systems.
2. **Test breadth is high**
   - The repo includes **37 test modules** under `tests/`, covering combat, inventory, loot, progression, save/load, and wilderness flows.
3. **Determinism/replay posture exists**
   - The project has replay and validation scripts plus documented determinism concerns and mitigation plans.

## What is still transitional / skeleton-like

1. **Inventory/equipment migration is intentionally incomplete**
   - The migration plan explicitly keeps legacy actor equipment fields and bridge sync helpers during transition.
   - The same doc lists pending systems (combat/shop/UI assumptions and encumbrance model replacement).
2. **Magic-item runtime is phase-1 by design**
   - Runtime supports a constrained set of effect types and explicitly treats unsupported effects as placeholders.
3. **Command layer includes many marker/no-field command types**
   - Several command dataclasses are currently signal-only (`pass`) event wrappers; this is valid, but reflects an evolving command/API contract.
4. **Manifest/documentation drift exists**
   - `MANIFEST.md` references a `p44/...` path layout that does not match the current repository structure.

## Technical debt (ranked)

### High

1. **Monolith risk in `sww/game.py`**
   - `sww/game.py` is currently ~15.7k LOC and contains most orchestration and significant business logic.
   - This is the central scalability bottleneck for onboarding, refactors, and defect isolation.

2. **Cross-system rule duplication and status handling complexity**
   - Existing combat audit already flags duplicated targeting/adjacency logic and direct status mutation across modules.
   - This creates drift risk and replay-stability risk during future changes.

### Medium

3. **Transitional compatibility layers increase cognitive load**
   - Legacy/new inventory-equipment duality is practical short-term but raises bug surface and serialization complexity.

4. **Packaging/dev ergonomics gap**
   - Running `pytest` without `PYTHONPATH=.` fails import resolution (`ModuleNotFoundError: sww`), indicating missing packaging/test-runner defaults.

### Low

5. **UI/demo path has broad exception swallowing**
   - `ui_pygame/app.py` contains broad `except Exception: pass` in demo party setup.
   - This avoids crashes but may hide integration regressions in non-primary UI paths.

6. **Backup artifact committed**
   - `sww/game.py.bak` in repo root is likely historical baggage and should be validated/removed if obsolete.

## Where we stand vs. “missing details”

- This is **not** an early skeleton project overall.
- It is a **mid-to-late integration project**: core gameplay loops are implemented, but architecture is carrying transition debt.
- “Missing details” are mostly in:
  - migration completion (legacy field retirement),
  - richer inventory/ownership UX,
  - broader magic-item behavior execution,
  - maintainability refactor of orchestration-heavy modules.

## Recommended next 3 milestones

1. **Stabilization milestone (short, 1–2 weeks)**
   - Keep test suite green under `PYTHONPATH=.` baseline.
   - Remove trivial correctness papercuts (like missing imports).
   - Add a canonical test invocation target (`Makefile`/`tox`/`pyproject`) so `pytest` works without env tweaking.

2. **Migration-completion milestone (2–4 weeks)**
   - Finish inventory/equipment migration plan items and remove legacy mirrors where safe.
   - Add explicit save/load compatibility tests for final migrated schema only.

3. **Architecture milestone (ongoing)**
   - Carve focused subsystems out of `Game` (town services, combat orchestration, loot/inventory assignment).
   - Use current fixture/replay harness as non-regression gate for each extraction.

## Checks run for this audit

- `pytest -q` (fails collection unless `PYTHONPATH=.` is set).
- `PYTHONPATH=. pytest -q` (suite runs; previously had one failing test due to missing `item_is_magic` import in `sww/game.py`; fixed in this branch).
- static grep scans for TODO/skeleton markers and broad `pass` exception swallow sites.
