# Wilderness loot write inventory (focused migration note)

Scope: wilderness POI reward item ingestion only. Coin/sell accounting intentionally excluded.

## Direct-write inventory and classification

### 1) `Game._handle_current_hex_poi` (wilderness POI reward branches)

- **Path**: `sww/game.py` (`ruins`, `shrine` relic recovery, `lair`, `abandoned_camp` branches).
- **Current write behavior**:
  - Reward items are built as ad-hoc dict rows (`{"name", "kind", "gp_value"}`) and then routed via `_ingest_reward_items_to_loot_pool(...)`.
  - No active direct `party_items.append/extend` writes in these branches.
- **Classification**: **safe to migrate now** (largely already migrated).
- **Proposed migration target**:
  - Keep ingestion via loot-pool bridge in next pass.
  - Tighten source-aware legacy mirror control so these sources can stop syncing to `party_items` once readers are migrated.

### 2) `Game._ingest_reward_items_to_loot_pool` (shared wilderness ingestion bridge)

- **Path**: `sww/game.py`.
- **Current write behavior**:
  - Writes items to `loot_pool` via `add_generated_treasure_to_pool(...)`.
  - Immediately calls `_sync_legacy_party_items_from_loot_pool()`, mirroring loot into legacy `party_items`.
- **Classification**: **needs compatibility wrapper**.
- **Proposed migration target**:
  - Keep this as compatibility bridge for now.
  - Next pass: source-aware sync policy (or caller-level policy) to disable legacy mirror for wilderness sources once legacy readers are removed.

### 3) `Game._sync_legacy_party_items_from_loot_pool` and callers

- **Path**: `sww/game.py`.
- **Current write behavior**:
  - Rebuilds `party_items` from loot pool entries (legacy mirror write).
  - Used by reward ingestion and other compatibility paths.
- **Classification**: **needs compatibility wrapper**.
- **Proposed migration target**:
  - Preserve during transitional period.
  - Restrict to legacy UI/readers only, then delete when `party_items` readers are gone.

### 4) Wilderness/combat recap fallback reading `party_items` deltas

- **Path**: `sww/game.py` (`combat_start_items_count` / fallback summary delta logic).
- **Current write behavior**:
  - Not a reward write path, but still depends on legacy `party_items` for fallback item delta reporting.
- **Classification**: **defer for later**.
- **Proposed migration target**:
  - Move recap entirely to loot-pool entry-id deltas/events.
  - Remove legacy fallback after wilderness + combat readers are fully migrated.

## Wilderness direct-write sites found (active code)

- No active wilderness branches directly appending to `party_items` were found in `sww/game.py`.
- Active wilderness reward item flows (`ruins`, `shrine` relic, `lair`, `abandoned_camp`) currently write to `loot_pool` and then mirror via compatibility sync.

## Next-pass safe targets

- Safe first pass:
  - Wilderness POI branches in `_handle_current_hex_poi` (`ruins`, `shrine` relic, `lair`, `abandoned_camp`) to stop legacy mirror side effects once readers are confirmed migrated.
- Needs wrapper first:
  - `_ingest_reward_items_to_loot_pool` and `_sync_legacy_party_items_from_loot_pool`.
- Defer:
  - Combat/encounter recap fallback logic that still references `party_items` deltas.
