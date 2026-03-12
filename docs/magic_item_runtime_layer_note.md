# Magic item runtime layer (incremental)

This project now supports a **first safe runtime layer** for magic items through the same item-template and item-instance path used by mundane gear.

## Why narrow-by-design

The implementation intentionally avoids a universal "do everything" effect engine. Instead, templates carry small JSON-friendly effect payloads and runtime helpers expose those payloads for systems that opt in.

That keeps this phase:
- save-safe,
- readable,
- compatible with existing mundane and legacy inventory/equipment flows.

## Template fields supported

`ItemTemplate` now supports:
- `magic: bool`
- `cursed: bool`
- `identified_name: str | None`
- `unidentified_name: str | None`
- `wear_category: str | None`
- `effects: list[dict]`
- `charges: int | None`
- `metadata: dict` (rules payload)

Instances inherit these in `ItemInstance.metadata` during construction so old call sites can read a stable payload without deep template coupling.

## Supported runtime effect types (phase 1)

- `attack_bonus`
- `damage_bonus`
- `ac_bonus`
- `save_bonus`
- `movement_bonus`
- `granted_tag`
- `sticky_equip`
- `light_source`
- `consumable_heal`
- `spell_cast`

Compatibility aliases currently normalize:
- `heal` -> `consumable_heal`
- `cast_spell` -> `spell_cast`

## Unsupported placeholders

Unsupported effect types are intentionally not executed here. They are surfaced explicitly via helper detection (`item_unsupported_effect_types`) so downstream systems can fail fast or ignore by policy.

This is deliberate: effect *transport* and effect *execution* are decoupled until combat/spell integrations are implemented in focused slices.


## Identification lives on instances

Identification and curse-knowledge are stored per owned `ItemInstance`, not on the template, because two copies of the same template may be known differently during play.

Runtime helpers for UI/log surfaces:
- `item_known_name(instance, template)`
- `item_short_description(instance, template)`
- `item_inspect_text(instance, template)`

Unidentified fallback names currently include:
- `Unfamiliar Ring`
- `Cloudy Potion`
- `Scroll with strange script`
- `Runed Sword`


## Town identification service assumptions

Current baseline is conservative and deterministic:
- Magic consumables (potion/scroll/ammo): **15 gp**
- Other magic items: **25 gp**
- Mundane unidentified oddities: **8 gp**

Behavior choices in this phase:
- Identification targets a specific **item instance** in an actor inventory.
- Curses are **not** revealed by default when identified in town.
- Charges are **not** fully revealed by default.
- Potions and scrolls use the same town service flow (with lower fee band).

Future extension hooks: sages, wizard guild discounts, and class-specific identification perks.
