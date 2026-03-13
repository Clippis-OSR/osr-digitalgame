# Canonical Dungeon Generation Pipeline

This package now defines one canonical generation flow:

1. **Schema**
   - Input parameters (`level_id`, `seed`, `width`, `height`, `depth`) and schema versioning.
2. **Blueprint**
   - Geometry/layout + connectivity + doors/stairs via `run_canonical_blueprint_stage`.
3. **Generator**
   - Deterministic stocking/content pass via `run_canonical_level_pipeline`.
4. **Instance**
   - Deterministic mutable-instance bootstrap (`instance_state`) included in canonical level artifacts.
5. **Delta persistence**
   - Runtime mutations persist separately as dungeon deltas in game/runtime systems.

## Entrypoints

- `sww.dungeon_gen.pipeline.run_canonical_blueprint_stage`
- `sww.dungeon_gen.pipeline.run_canonical_level_pipeline`
- `sww.dungeon_gen.pipeline.validate_canonical_blueprint`

Scripts now call these canonical entrypoints (`scripts/gen_dungeon.py`, `scripts/validate_dungeon.py`).
