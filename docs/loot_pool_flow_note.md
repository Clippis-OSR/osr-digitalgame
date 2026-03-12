# Temporary loot pool flow (incremental)

Treasure generation is now split from ownership:

1. Encounter/room generation creates loot entries in a `LootPool`.
2. Coins from current room-treasure paths are still credited directly to treasury for compatibility.
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

Still using direct/legacy `party_items` write patterns (to migrate later):
- some wilderness/event reward append paths in `Game` that still call `self.party_items.append(...)` directly.

## TODOs

- Add richer player choice UI (batch assign, split stacks, leave multiple).
- Add coin splitting and town-vault/stash transfer commands.
- Remove legacy `party_items` once all reward paths use loot pool directly.
