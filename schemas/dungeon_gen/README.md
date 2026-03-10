# P4.9 Dungeon Generation Schemas

These JSON Schemas define the *artifact contract* for procedural dungeon generation.

- `dungeon_blueprint.schema.json` — immutable geometry + fixtures (rooms/corridors/doors/stairs/features) and a connectivity graph.
- `dungeon_stocking.schema.json` — immutable population of rooms (monsters/treasure/traps/specials) + wandering + restock policy.
- `dungeon_instance_state.schema.json` — mutable save-game delta layer applied on top of blueprint+stocking.
- `generation_report.schema.json` — audit log of rolls/choices + validation results.

## Suggested output locations

Generator outputs (immutable):
- `saves/dungeons/<campaign_id>/<level_id>/blueprint.json`
- `saves/dungeons/<campaign_id>/<level_id>/stocking.json`
- `saves/dungeons/<campaign_id>/<level_id>/generation_report.json`

Instance save (mutable):
- `saves/dungeons/<campaign_id>/<level_id>/instance_state.json`

## Table packs

Starter table packs live in:
- `data/dungeon_gen/`

These are intentionally minimal scaffolds to wire the pipeline.
