# Per-Character Inventory & Equipment Migration Plan

## Target architecture (transitional)

This project is moving from party-only inventory assumptions to per-character ownership with explicit equipment state.

### Core dataclasses

- `ItemInstance`
  - `instance_id`, `template_id`, `name`, `category`
  - `quantity`, `identified`, `equipped`, `magic_bonus`, `metadata`
- `CharacterInventory`
  - `items`
  - coin fields: `coins_gp`, `coins_sp`, `coins_cp`
  - expedition supplies: `torches`, `rations`, `ammo`
- `CharacterEquipment`
  - `armor`, `shield`, `main_hand`, `off_hand`, `missile_weapon`, `ammo_kind`, `worn_misc`

## Equipment reference policy (for now)

To minimize disruption, equipment fields currently store **stable names** (e.g., `"Sword"`, `"Leather"`) because existing combat/shop flows mostly resolve by name.

Future passes may switch these fields to item-instance ids once runtime systems are migrated.

## Migration strategy

- Keep legacy actor fields (`weapon`, `armor`, `shield`, `shield_name`) until all call sites are ported.
- Use explicit bridge helpers on `Actor`:
  - `ensure_inventory_initialized()`
  - `ensure_equipment_initialized()`
  - `sync_legacy_equipment_to_new()`
  - `sync_new_equipment_to_legacy()`
- Save schema upgraded to v13, including key normalization migration from older scaffolding payloads.

## Systems still pending

- Combat helpers reading `actor.weapon` directly.
- Shop/town purchase flows mutating legacy fields.
- Encumbrance model replacement (party slot-based -> per-character weight-based).
- UI surfaces that assume the old weapon/armor/shield tuple.


## Item template runtime layer (current coverage)

A thin data-driven template catalog now bridges extracted data into unified runtime templates.

- Fully data-derived now:
  - `weapon` from `weapons_melee.json` + `weapons_missile.json`
  - `armor` / `shield` from `armor_table_24.json`
  - `ammo` entries from missile weapon data when represented as ammo rows
- Explicit bespoke templates now:
  - `potion`, `scroll`, `ring`, `wand` from `items_magic_v1.json`
- Placeholder/lightweight for later expansion:
  - `gear` from `equipment_general.json` (currently value-focused, sparse weight data)
  - `treasure` generic template for runtime convenience

This keeps the bridge practical without forcing immediate full content-system replacement.
