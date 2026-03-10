Swords & Wizardry Complete — Text CRPG (Python)
================================================

This is a single-player, text-based CRPG implementing core Swords & Wizardry Complete mechanics.
It ships with data *extracted from the provided PDF* (equipment table, undead turning table, monster saves table,
spell descriptions, and a large chunk of monster stat blocks).

Run:
  python main.py

Notes (important):
- All extracted spells are available as data and can be prepared/cast. A subset have automated mechanical effects
  (e.g., Cure Light Wounds, Cure Disease, Magic Missile, Sleep, Fireball, Bless). For other spells, the game shows the rule text
  and tracks duration/targets, but may require manual adjudication (the engine logs what to apply).
- Monster list extraction is best-effort from PDF text. Some monsters may be missing or have OCR-ish artifacts.

- Treasure tables (79-107) extracted into data/treasure_tables_79_107.json and used for hoard generation.

- Wilderness encounter tables (54-69) extracted into data/encounter_tables_54_69.json and used for terrain-based encounters.
- Dungeon turns now track torch duration (1 hour = 6 turns) based on the equipment description.

- Combat now uses the extracted attack tables (Tables 29-32) in data/attack_tables_29_32.json.

- Weapon and armor tables (22-24) extracted into data/weapons_melee.json, data/weapons_missile.json, and data/armor_table_24.json.

- Combat now supports missile attacks (with range penalty prompt) and weapon-based damage.

- Clerics can Turn Undead as a combat action using the extracted turning table.

- Added formation (front/back) with melee protection rules, Backstab action for Thief/Assassin, and shooting-into-melee restriction with risk.

- Added surprise rolls (1-2 in 6), movement/disengage with opportunity attacks, retreat/flee flow, and backstab conditions (requires hiding or surprise).

- Auto-combat AI improved: healing, hide/backstab sequencing, retreat/flee decisions, and safer missile use.

- Monster AI updated: undead relentless, animals flee when hurt, humanoids target exposed backline and can break, dragons focus strongest and may withdraw.

- Special ability automation (best-effort): breath weapon (recharge), gaze/petrify, paralysis touch, poison, and generic caster blasts based on monster Special text.

- Real monster spellcasting: caster monsters get prepared spells drawn from the full spell list and cast tactically (Fireball/Sleep/Hold Person/etc.).

- Spellcasting upgraded: AoE resolution for Fireball/Burning Hands/Lightning Bolt, plus Web/Fear/Confusion/Invisibility/Mirror Image/Silence/Dispel Magic, and status enforcement (Asleep/Entangled/Confused/Silenced).

- AoE targeting improved: PCs can choose frontline/backline/all; AI chooses intelligently. Added Cone of Cold, Cloudkill (ongoing), Hold Monster, Wall of Fire/Ice with ongoing tick effects.

## Campaign Layer
- Modernized XP: gold recovered (1 gp = 1 XP) + monster XP (HD*100 with bonuses).
- Unified XP table with smoother progression.
- Town phase: rest, train/level-up (small fee), buy equipment, sell loot.
- Party creation supports 1-4 PCs.

- Party size increased to 1-6.
- Added reaction rolls (2d6 + CHA mod) and a parley system (bribe/intimidate/withdraw/trade/recruit hireling).


## P4.9 Procedural Dungeon Generation (contract only)

This build includes the JSON Schema contract and starter table packs for the upcoming P4.9 procedural megadungeon generator:

- Schemas: `schemas/dungeon_gen/`
- Starter tables: `data/dungeon_gen/`

These artifacts are designed to support deterministic generation and save-game stability (blueprint+stocking immutable, instance state as deltas).

## Dev / tuning scripts

- `python -m scripts.run_fixtures` — deterministic replay fixtures.
- `python -m scripts.stress_test` — randomized stress sanity check.
- `python -m scripts.xp_economy_report` — prints XP thresholds by class.
- `python -m scripts.xp_pacing_sim` — simulates stocking + XP-based hoards to estimate XP/gp/magic per expedition.
