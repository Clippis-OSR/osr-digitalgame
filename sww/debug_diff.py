from __future__ import annotations

from typing import Any

def snapshot_state(g) -> dict[str, Any]:
    # Keep this shallow and stable; it's for developer debugging.
    return {
        "campaign_day": g.campaign_day,
        "day_watch": g.day_watch,
        "gold": g.gold,
        "party_size": len(getattr(getattr(g, "party", None), "members", []) or []),
        "party_items": len(getattr(g, "party_items", [])),
        "retainers_roster": len(getattr(g, "retainer_roster", [])),
        "retainers_active": len(getattr(g, "active_retainers", [])),
        "dungeon_rooms": len(getattr(g, "dungeon_rooms", {})),

        "torches": g.torches,
        "rations": g.rations,
        "party_hex": tuple(getattr(g, "party_hex", (0, 0))),
        "in_dungeon": bool(getattr(g, "in_dungeon", False)),
        "dungeon_turn": g.dungeon_turn,
        "current_room_id": g.current_room_id,
        "noise_level": g.noise_level,
        "light_on": g.light_on,
        "torch_turns_left": g.torch_turns_left,
        "contracts_active": len(getattr(g, "active_contracts", [])),
        "contracts_board": len(getattr(g, "contract_board", [])),
        "clocks": len(getattr(g, "conflict_clocks", [])),
        "world_hexes": len(getattr(g, "world_hexes", {})),
        "factions": len(getattr(g, "factions", {})),
    }

def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    keys = sorted(set(before.keys()) | set(after.keys()))
    for k in keys:
        bv = before.get(k, "<missing>")
        av = after.get(k, "<missing>")
        if bv != av:
            changes.append({"field": k, "before": bv, "after": av})
    return changes
