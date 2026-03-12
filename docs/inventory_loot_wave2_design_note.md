# Inventory/Loot Wave 2 Design Note

This note summarizes the second inventory/loot integration wave and the design intent behind current behavior.

## Why unidentified magic items matter (OSR play)

Unidentified magic items preserve risk, discovery, and resource friction:
- Players must decide whether to spend town gold/time on identification now vs. later.
- Loot value is not fully known at pickup time.
- Spell and town-service choices become meaningful logistical decisions.

In this project, item identification is represented at the **item instance** level, so two copies of the same template can have different knowledge states.

## Why curses matter (OSR play)

Curses create consequences for opportunistic equipping and unknown-item use:
- they discourage blind optimization,
- they create emergent problem-solving moments,
- they justify town/spell mitigation loops (Identify, Remove Curse, temple/sage services).

Current implementation keeps curse effects explicit and narrow (not a global hidden-effects engine).

## Why loot is routed through a loot pool

The loot pool is an intermediate ownership stage between generation and actor inventory:
- encounter/room generation can stay deterministic and side-effect-light,
- assignment can be a separate player choice,
- outcomes can route to actor inventory, stash, or discard,
- migration away from legacy `party_items` can happen incrementally.

This decouples reward generation from immediate equip/carry mutations.

## Why town identification and town stash exist

### Town identification
Identification is intentionally modeled as a town service to preserve OSR expedition cadence:
- unknown items can be carried back and assessed later,
- gold sink + downtime decision remains meaningful,
- known/unknown states are save-safe and instance-specific.

### Town stash
The stash separates *carried expedition load* from *safe town storage*:
- actor inventories drive expedition encumbrance,
- stash items are intentionally excluded from carried load,
- stash access is town-scoped to keep wilderness/dungeon logistics meaningful.

## Why wear categories are layered on top of core equipment slots

Core combat equipment remains intentionally small (`armor`, `shield`, `main_hand`, `off_hand`, `missile_weapon`, `ammo_kind`).

Wear-category limits are added as a lightweight policy layer over `worn_misc` because:
- it enforces classic constraints (rings/cloaks/etc.) without introducing a full slot matrix,
- it keeps save data and mutation logic simple,
- it avoids destabilizing combat calculations tied to core slots.

## Status by area

### Complete (for this wave)
- Per-instance identified/unidentified state in inventory + loot flow.
- Narrow curse handling integration (sticky unequip restrictions + remove-curse suppression path).
- Loot-pool intermediary for generation -> assignment.
- Town stash model + save/load persistence.
- Basic wear-category limits over `worn_misc`.
- Treasure runtime bridge into loot-pool-compatible rows.

### Transitional
- Legacy `party_items` compatibility remains in parts of the flow.
- Some town/loot UX paths are improved but still menu-text oriented.
- Treasure tables are partially materialized into runtime item rows.

### Intentionally simplified
- Not all S&W tables/subtables are fully implemented as rich item mechanics.
- Curse behavior is intentionally scoped (no universal curse engine).
- Identification output remains concise (not a full in-engine appraisal report).
- Wear-category constraints are coarse limits, not a full equipment anatomy model.

## Future work

- Broader magic item table coverage (deeper named-item/subtable fidelity).
- Item appraisal and richer sage-service options (quality tiers, partial info, discounts).
- Potion experimentation workflows and outcomes.
- Scroll deciphering/readability mechanics beyond simple known/unknown state.
- Container rules (capacity, nested storage constraints, retrieval friction).
- Pack animals and wagons as logistics multipliers.
- Hireling gear management and ownership delegation workflows.

