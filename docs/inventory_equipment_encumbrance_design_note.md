# Inventory / Equipment / Encumbrance Design Note

## Why the old model was insufficient

The previous model mixed multiple concerns in ways that were hard to extend safely:

- **Equipment state was too shallow** (`weapon`, `armor`, `shield`, `shield_name` only).
- **Inventory ownership was effectively party-level**, making per-character loadouts and realism awkward.
- **Encumbrance was slot-based and party-aggregated**, which obscured who carried what and limited RAW-leaning movement logic.
- Combat/shop/loot logic often mutated legacy actor fields directly, which increased coupling and migration risk.

## What the new model is

The repo now uses a layered, incremental architecture:

- **Per-character item instances** (`ItemInstance`) with stable `instance_id` and `template_id`.
- **Per-character inventory container** (`CharacterInventory`) for owned items, coin pools, supplies, and ammo.
- **Per-character equipment container** (`CharacterEquipment`) for practical OSR slots:
  - armor, shield, main hand, off hand, missile weapon, ammo kind, worn misc.
- **Inventory service layer** (`sww/inventory_service.py`) centralizes mutations/equip logic.
- **Runtime item template layer** (`sww/item_templates.py`) derives templates from extracted content data and builds item instances.
- **Weight-based encumbrance layer** (`sww/encumbrance.py`) computes actor load and movement from pounds, with party movement driven by the slowest relevant member.

## Alignment with RAW / old-school Swords & Wizardry principles

The implementation is designed to stay close to old-school play priorities:

- **Who carries what matters** (per-character inventory and load).
- **Weight matters** (coins, equipment, supplies, ammo contribute to carried weight).
- **Movement pressure is party-constrained by the slowest member**, consistent with expedition logistics.
- **Equipment model remains practical and restrained**, avoiding modern stat-slot bloat.

## Practical compromises

To keep migration safe and incremental, several practical compromises are explicit:

- Legacy actor fields are still preserved and synchronized where needed.
- Some systems still accept or emit legacy-shaped data for compatibility.
- Gear/treasure templates currently include placeholder-level coverage in some cases.
- Equip references may be stable names or instance ids depending on call path during transition.
- Encumbrance compatibility wrappers still expose legacy fields for older callers.

## Migration status

### Complete

- Core data structures for item instances, per-character inventory, and per-character equipment.
- Save/load persistence and schema migrations for new containers.
- Weight-based encumbrance core calculations.
- Inventory service and runtime item template catalog foundations.

### Transitional

- Combat, shop, and loot paths now prefer new structures in key places but still preserve old behavior through fallback/sync logic.
- Some UX paths still operate through party-loot intermediates before assignment.

### Still using legacy bridges

- Legacy equipment fields (`weapon`, `armor`, `shield`, `shield_name`) remain in use for backward compatibility.
- Compatibility wrappers and fallback lookups remain necessary in several runtime paths.

## Future work

- **Magic-item wear categories** beyond current `worn_misc` simplifications.
- **Unidentified item handling** integrated into per-character inventory (not just party-loot workflows).
- **Cursed item behavior** and equip constraints.
- **Container / pack animal support** with explicit capacity and transfer rules.
- **Richer loot UX** (batch assignment, stack splitting, equip-on-pickup decisions, clearer ownership flows).
