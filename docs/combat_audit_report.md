# Combat Engine Audit (A2/A3/A4 readiness)

Scope requested:
1) duplicated adjacency / disengage / opportunity-attack logic
2) direct status mutation from multiple modules
3) death/removal cleanup paths that do not fully remove combat participation
4) target validation logic duplicated across AI, command resolution, and spellcasting
5) unordered iteration in combat resolution that could cause replay drift

## 1) Duplicated adjacency / disengage / opportunity logic hotspots

- **`sww/grid_battle.py` duplicates adjacency/move legality and own opportunity attack trigger path**
  - `can_move_to()` and `adjacent()` implement local adjacency/movement checks. (`74-85`, `99-100`)
  - `resolve_command(CmdMove)` implements its own adjacency-leave opportunity trigger via `old_adj - new_adj`. (`435-474`)
  - `CmdTakeCover` separately checks adjacent cover tiles. (`482-494`)
- **`sww/targeting.py` has another adjacency and missile-range authority**
  - `adjacent()`, `in_missile_range()`, `valid_melee_targets()`, `valid_missile_targets()`. (`15-59`)
- **`sww/game.py` has theater-of-mind engagement rules and penalties separate from grid module**
  - missile/melee engagement and retarget behaviors in combat round execution (`11522-11531`, `11738-11742`)
  - an independent central module exists (`sww/combat_rules.py`), but grid logic does not consume it. (`6-31`)

### Why this is risky
- Same concepts (adjacent, engagement, who can strike, opportunity windows) are spread across at least 3 places with different abstractions (grid tiles vs distance-ft theater).

## 2) Direct status mutation from multiple modules (bypassing one status contract)

- **`sww/grid_battle.py` direct writes/removals**
  - direct status dict mutation for `cover`, `burning`, `entangled`, and spell-casting markers. (`94-97`, `492-494`, `689-707`, `925-927`)
- **`sww/spellcasting.py` mixed style** (partly centralized, partly direct)
  - direct status writes remain for `charmed`, `shield`, `invisible`, `prot_evil`, `sanctuary`, `bless`. (`584`, `631`, `641`, `917`, `927`, `937`, `944`)
  - only some effects use `apply_status` (`held`, `asleep`, `web`). (`524`, `589`, `656`)
- **`sww/game.py` still has direct status/effects writes in several combat branches**
  - pre-write of status dicts and direct effect list append paths when routing/fleeing occur. (`11120-11123`, `11958`)

### Why this is risky
- Duration semantics (`int` counters vs `{"rounds":...}` vs `{"turns":...}`) remain inconsistent, and direct writes can skip normalization/aliasing.

## 3) Death/removal cleanup paths that may leave combat participation residue

- **Cleanup exists but is not universally used for non-death removal states**
  - `_notify_death()` invokes `cleanup_actor_battle_status()` and effect cleanup manager. (`12377-12420`)
- **Flee/surrender transitions frequently append effects directly without central cleanup call**
  - AI flee path: appends `"fled"` directly. (`11120-11123`)
  - pending flee resolution / morale outcomes also mutate effects directly. (`11950-11963` region)
  - spell charm path marks target as `fled` directly. (`586`)

### Why this is risky
- “Out of fight” state is represented by effects flags in many places, but cleanup of transient combat statuses is strongly tied to death-only hooks.

## 4) Target validation duplication across AI, command resolution, spellcasting

- **Theater-of-mind AI + execution retargeting in `game.py`**
  - target validity logic (`_combat_target_is_valid`) and deterministic retargeting (`_combat_retarget_or_clear`). (`1272-1296`)
- **Grid command/controller target validation in `turn_controller.py` and `targeting.py`**
  - AI declaration checks use `valid_melee_targets` / `valid_missile_targets`. (`306-313`)
  - targeting authority implemented independently in `targeting.py`. (`23-59`)
- **Spell target selection/validation duplicated in spell systems**
  - grid spell targeting uses `spells_grid.can_target` in `targeting.py` and `grid_battle.py`.
  - theater spell application uses local chooser/validation and immunity/resistance checks in `spellcasting.py` (`choose_foe/ally` flows around `560-660`, plus campaign spell status writes `911-946`).

### Why this is risky
- Three target-validation surfaces (theater combat, grid combat commands, spellcasting resolution) can diverge in subtle edge cases.

## 5) Unordered iteration risks that can affect replay determinism

- **Set iteration used for status block precedence**
  - `action_block_reason()` iterates `ACTION_BLOCKING_STATUSES`/`CAST_BLOCKING_STATUSES` sets, which are unordered. (`12-21`, `92-96` in `status_lifecycle.py`)
  - first-found reason can vary and alter logs/events.
- **Multiple dict `.values()` / `.items()` traversals in combat control without explicit ordering**
  - e.g. `state.units.values()` and `living.items()` loops in `turn_controller.py` and `grid_battle.py` for selecting/partitioning combatants. (`turn_controller.py:300-313, 512-525`; `grid_battle.py:49-55` and several `state.units.values()` loops)
- **Set difference sorted in one place but not globally normalized elsewhere**
  - opportunity leavers in grid battle are sorted (`452`), which is good local hardening, but similar patterns elsewhere are mixed.

### Why this is risky
- Determinism currently relies on insertion-order assumptions and ad hoc sorting; replay drift risk rises when upstream container construction changes.

---

## Risk ranking

1. **High** — Direct status mutation across modules (`game.py`, `grid_battle.py`, `spellcasting.py`) with mixed schema (`int`/`rounds`/`turns`) and mixed cleanup behavior.
2. **High** — Target-validation duplication across theater AI, grid controller, and spellcasting.
3. **Medium-High** — Death-only centralized cleanup while flee/surrender/removal paths often bypass cleanup contract.
4. **Medium** — Duplicated adjacency/opportunity logic across grid and theater layers.
5. **Medium** — Unordered iteration in block-reason and container traversal (determinism/log drift risk, lower direct rules risk).

---

## Smallest safe refactor sequence (no behavior change intent)

1. **Determinism guard pass (A4-first hardening)**
   - Freeze iteration order in status block-reason checks and combatant traversals used for decisions/logs.
   - Add replay assertions around deterministic ordering before logic movement.

2. **Status write funnel (A2 core)**
   - Route all status writes through a single helper API (including `shield`, `invisible`, `prot_evil`, `sanctuary`, `bless`, grid item statuses).
   - Keep payload shape identical per effect for now; only centralize write/tick/cleanup access.

3. **Removal cleanup unification (A2)**
   - Introduce a single “leave combat” helper invoked by death, flee, surrender, charm-removal, and map-removal paths.
   - Internally call existing death cleanup pieces conditionally, preserving current side effects/events.

4. **Target validation seam extraction (A3/A1 bridge)**
   - Extract a read-only target-eligibility service consumed by:
     - theater AI retarget (`_combat_target_is_valid` / `_combat_retarget_or_clear`)
     - grid `valid_*_targets`
     - spell target prechecks
   - Start with wrappers that delegate to existing logic to avoid behavior change.

5. **Adjacency/opportunity authority consolidation (A1 follow-on)**
   - Define shared adjacency/disengage/opportunity primitives with adapters for grid positions and theater distance.
   - Migrate call-sites incrementally under fixture wall checks, one primitive at a time.

---

## Notes for fixture wall execution (A4)

- Keep strict replay enabled while migrating each seam.
- Prefer converting failures from RNG-mismatch to state/snapshot-diff first (order hardening before rule changes).
- Re-bless fixtures only after intentional state delta review per migrated primitive.
