# Temporary loot pool flow (incremental)

Treasure generation is now split from ownership:

1. Encounter/room generation creates loot entries in a `LootPool`.
2. Coins are handled explicitly by coin-destination policy (treasury by default in most current flows).
3. Item entries remain pooled until one of:
   - assigned to an actor inventory,
   - moved to party stash,
   - sold (removed from pool for gp),
   - left behind (removed from pool with no gain).

## Why this shape

This keeps old-school logistics pressure (items are not instantly ownerless abstractions) while avoiding a big UI rewrite.

## Current migration status

Migrated to loot-pool helper path:
- `_handle_room_treasure`
- `_grant_room_loot`
- wilderness POI item rewards (`ruins`, `shrine`, `lair`, `abandoned_camp`) now ingest through `_ingest_reward_items_to_loot_pool(...)`

Legacy compatibility bridges still active:
- `Game._sync_legacy_party_items_from_loot_pool()` mirrors pending loot into `party_items` for older menus.
- `Game._ensure_loot_pool_hydrated_from_legacy()` backfills pool entries from old saves/paths that only had `party_items`.
- `sell_loot()` still sells from pending/shared pool entries (not directly from actor inventories yet).

## TODOs

- Add richer player choice UI (batch assign, split stacks, leave multiple).
- Add coin splitting and town-vault/stash transfer commands.
- Remove legacy `party_items` once all reward paths use loot pool directly.
