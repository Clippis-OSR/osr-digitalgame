# Per-Character Inventory & Equipment Migration Plan

## Target architecture

This refactor moves from party-only slot encumbrance toward per-character carried weight while preserving existing combat/shop/save behavior during transition.

### New core model primitives

- `ItemInstance`
  - Stable, JSON-friendly owned item record.
  - Uses `item_id` + `instance_id` to avoid brittle display-name coupling.
  - Supports quantity + authored/derived weight and freeform metadata.
- `CharacterInventory`
  - Lives on each `Actor`.
  - Holds carried `items`, carried `coins_gp`, and a `capacity_lb` target.
  - Includes temporary helper methods for derived total weight/state.
- `CharacterEquipment`
  - OSR-practical equipment abstraction:
    - `armor`
    - `shield`
    - `main_hand`
    - `off_hand`
    - `missile_weapon`
    - `ammo`
    - `worn_misc`
  - Intentionally avoids a large MMO-like slot matrix.

### Actor compatibility

`Actor` now has:
- `inventory: CharacterInventory`
- `equipment: CharacterEquipment`

Legacy fields are intentionally retained during migration:
- `weapon`
- `armor`
- `shield`
- `shield_name`

This ensures existing game logic keeps running while downstream systems are ported.

## Save/load migration strategy

- Save schema bumped to v12.
- Added actor serialization for `inventory` and `equipment` in all actor containers:
  - party members
  - retainers
  - dungeon room foes
- Added migration `11 -> 12` that seeds defaults for legacy saves.
- Migration seeds `equipment` from legacy fields where possible (`weapon`, `armor`, `shield_name`).

## Incremental rollout steps (next passes)

1. **Equipment resolution layer**
   - Introduce helper accessors that prefer `Actor.equipment` and gracefully fall back to legacy fields.
   - Update combat weapon-kind/damage/range lookup through that layer.

2. **Per-character encumbrance engine**
   - Add weight-based encumbrance calculations per actor (STR + race/class policy as needed).
   - Keep old party slot model available behind compatibility wrappers until all callers switch.

3. **Town/shop + loot assignment updates**
   - Route purchases and found items into target actor inventory/equipment.
   - Preserve simple UX with explicit actor-target choice where needed.

4. **Movement/exploration integration**
   - Replace party slot state with derived movement impacts from current party members' per-character loads.

5. **Legacy field retirement**
   - After all callsites are switched and fixtures updated, remove `weapon/armor/shield/shield_name`.

## Systems that still require updates

- Combat helpers currently reading `actor.weapon`.
- Shop/equipment purchase flows currently directly mutating legacy fields.
- Encumbrance calculations in `sww/encumbrance.py` (party slot model).
- Any UI/status displays that assume one flat weapon/armor/shield tuple.

## Notes

- This pass is scaffolding-only: no gameplay behavior changes are intended yet.
- TODO markers were added in model code to track legacy-field retirement.
