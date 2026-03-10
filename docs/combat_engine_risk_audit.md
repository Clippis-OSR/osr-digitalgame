# Combat Engine Risk Audit (No Behavior Change)

## 1) Executive Summary

The combat stack currently has **split rule authority** across theater (`game.py`) and grid (`turn_controller.py`/`grid_battle.py`/`targeting.py`) paths, with only partial consolidation through `combat_rules.py` and `status_lifecycle.py`. The highest replay/state risks are:

- **status lifecycle ownership is incomplete** (many direct mutations remain)
- **target-legality is implemented in multiple layers** (AI planning, command builders, execution retargeting, spell targeting)
- **death/removal cleanup does not have one universal “leave combat” entry point**
- **determinism still depends on insertion-order and set iteration in key branches**

Validators (`validation.py`) do **not** currently enforce core combat participation invariants (e.g., dead actors not occupying/threatening/acting), so illegal combat states may persist undetected.

---

## 2) High-Risk Findings

1. **Status lifecycle fragmentation (HIGH)**
   - `status_lifecycle.py` exists, but major write paths still mutate `status` directly in `grid_battle.py` and `spellcasting.py`.

2. **Target-legality drift (HIGH)**
   - AI/action declaration and execution use different legality surfaces across theater/grid/spell systems.

3. **Death/removal ghost participation risk (HIGH)**
   - `_notify_death()` handles death cleanup, but flee/surrender/charm-removal transitions bypass a single universal cleanup path.

4. **Opportunity/disengage authority duplicated (MED-HIGH)**
   - Leaving-melee/opportunity logic lives in grid move execution while theater combat has separate retreat/flee semantics.

5. **Determinism/replay instability vectors (MED-HIGH)**
   - set iteration in blocker selection and multiple decision loops over dict containers without explicit ordering.

---

## 3) File/Function Hotspots

| File | Function / Area | Approx Lines | Risk | Duplication / Drift | Suggested Owner |
|---|---|---:|---|---|---|
| `sww/grid_battle.py` | `resolve_command(... CmdMove ...)` per-step movement + opportunity attack (`old_adj - new_adj`) | 397-474 | High | Separate melee-exit/opportunity rules from theater combat | Grid Combat owner |
| `sww/grid_battle.py` | local spatial authority (`can_move_to`, `adjacent`, `in_missile_range`) | 74-105 | Med-High | Duplicates `targeting.py`/`combat_rules.py` semantics | Combat Rules owner |
| `sww/targeting.py` | `valid_melee_targets`, `valid_missile_targets` | 23-59 | High | Separate legality path from theater `_combat_target_is_valid` | Shared Targeting owner |
| `sww/turn_controller.py` | `_try_build_move` (path-level build) and `_try_build_attack` (grid legality) | 899-932 | High | Builder legality differs from theater execution legality | Turn Controller owner |
| `sww/game.py` | `_combat_target_is_valid`, `_combat_retarget_or_clear` | 1262-1315 | High | Theater-specific target legality/retarget logic | Combat Core owner |
| `sww/game.py` | combat missile/melee execution uses own retarget/filters | 11522-11531, 11738-11748 | High | Does not reuse grid legality functions | Combat Core owner |
| `sww/game.py` | flee/removal effect mutations (`effects.append('fled')`) | 11120-11123, ~11950-11963 | High | Removal path bypasses universal cleanup contract | Combat State owner |
| `sww/game.py` | `_notify_death` cleanup (death-only entry point) | 12377-12420 | High | No single shared leave-combat hook for non-death removals | Effects/State owner |
| `sww/spellcasting.py` | direct status writes (`charmed`, `shield`, `invisible`, `prot_evil`, `sanctuary`, `bless`) | 584, 631, 641, 917, 927, 937, 944 | High | Mixed with `apply_status`, causing contract split | Spell System owner |
| `sww/status_lifecycle.py` | `action_block_reason` iterates unordered sets | 12-21, 92-97 | Med-High | Non-stable reason precedence can drift logs/events | Determinism owner |
| `sww/grid_battle.py` | direct status writes (`cover`, `burning`, `entangled`, casting marker clear) | 94-97, 492-494, 689-707, 925-927 | High | Bypasses single apply/tick/remove owner | Grid Combat owner |
| `sww/validation.py` | `validate_state` lacks combat participation invariants | 289-453 | Med-High | No checks for dead-in-initiative/occupancy/targetability | Validation owner |
| `sww/combat_rules.py` | central helper module not yet adopted by grid stack | 6-45 | Med | Partial consolidation only | Combat Rules owner |

---

## 4) PASS/FAIL Matrix by Risk Class

| Risk Class | Status | Rationale |
|---|---|---|
| 1. Opportunity attacks / disengage / leaving melee | **FAIL** | Implemented in grid move path (`grid_battle.py`) while theater retreat/flee logic is separate (`game.py`) with no single disengage owner. |
| 2. Status lifecycle and cleanup | **FAIL** | `status_lifecycle.py` exists but many direct status writes remain in `grid_battle.py` and `spellcasting.py`. |
| 3. Death/removal cleanup and ghost participation | **FAIL** | `_notify_death` centralizes death cleanup only; flee/surrender/charm-removal do not consistently pass a single leave-combat cleanup function. |
| 4. Target legality drift (AI/intents/execution) | **FAIL** | Target legality is split across `targeting.py`, `turn_controller.py`, theater `_combat_target_is_valid`, and spell targeting paths. |
| 5. Determinism/replay instability | **FAIL** | Unordered set iteration in blocker selection and decision loops relying on dict insertion order remain in combat-critical paths. |

---

## 5) Smallest Safe Refactor Sequence

1. **Determinism-first guard rail pass (A4 pre-step)**
   - Eliminate unordered decision points (set iteration order, dict traversal in decision sites).
   - Add replay assertions around tie-breakers before moving logic.

2. **Status write funnel pass (A2 core, no schema change)**
   - Route all status writes (including grid/spell direct writes) through `apply_status` wrappers.
   - Preserve existing payload shapes (`int`, `rounds`, `turns`) in this pass.

3. **Unified leave-combat hook (A2 cleanup)**
   - Create one helper for death/flee/surrender/charm-removal/map-removal cleanup.
   - Internally call existing `_notify_death` pieces only where appropriate to avoid behavior change.

4. **Read-only target legality seam (A3 bridge)**
   - Introduce a shared eligibility API; initially delegate to existing logic per mode.
   - Have AI planning/intents/execution call through this seam without changing outcomes.

5. **Opportunity/disengage authority consolidation (A1 follow-on)**
   - Extract common disengage/leaving-melee primitives usable by grid and theater adapters.
   - Migrate one callsite at a time under strict replay.

---

## 6) Test Plan

1. **Replay determinism gate**
   - Run strict fixtures before/after each micro-change.
   - Fail migration if RNG mismatch appears before expected snapshot change.

2. **Combat edge suite**
   - Expand dedicated fixtures for: melee exit/opportunity, flee/surrender removal, retarget legality, and dead-unit non-participation.

3. **Invariant validation additions**
   - Add `validate_state` checks for:
     - dead units not in active initiative/occupancy/threat sets
     - removed/fled units not legal targets/actors
     - no stale casting marker on removed/dead units.

4. **Cross-surface legality parity tests**
   - Same scenario asserted through:
     - AI declaration path
     - intent command builder path
     - execution-time retarget path
     - spell target path.

---

## 7) “Do Not Touch Yet” Areas

- **Strict-replay consumption choreography in `game.py` combat phases** (target-pick compatibility shims around melee/missile execution).
- **Initiative/phase ordering and legacy replay compatibility comments** in theater combat loop.
- **Effect manager internals that are already stable in fixtures**, unless explicitly covered by new parity tests.
- **Fixture blessing pipeline** (do not re-bless until mismatch type is shifted from RNG to intentional snapshot diff).

---

## Explicit Yes/No Answers

- **Is melee exit detection implemented in more than one place?** **Yes.**
- **Is movement checked per-step or only per-path?** **Per-step at execution** (`grid_battle.py`), though path is prebuilt in controller.
- **Is there a single owner for status apply/tick/remove?** **No.**
- **Can a unit be dead but still occupy a tile, threaten, target, or act?** **Yes (risk exists).** Cleanup is path-dependent and not universally validated.
- **Does AI call the same legality functions as execution?** **No.**
- **Are any combat decisions based on unordered container iteration?** **Yes.**
