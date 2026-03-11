from __future__ import annotations

from typing import Any


def first_time_room_resolution_key(room: dict[str, Any], feature_id: str, mode: str = "resolve") -> str:
    """Return a stable canonical key for one-time room feature resolution."""
    r = room if isinstance(room, dict) else {}
    rid = r.get("id")
    try:
        room_id = int(rid)
    except Exception:
        room_id = -1
    feature = str(feature_id or "feature").strip().lower() or "feature"
    m = str(mode or "resolve").strip().lower() or "resolve"
    return f"{m}:room:{room_id}:{feature}"


def resolve_room_interaction_context(game: Any, room: dict[str, Any]) -> dict[str, Any]:
    """Read-only normalization of room interaction context."""
    r = room if isinstance(room, dict) else {}
    rid = int(r.get("id", getattr(game, "current_room_id", 0)) or 0)
    depth = int(r.get("depth", getattr(game, "dungeon_depth", 1)) or 1)

    tags: list[str] = []
    if hasattr(game, "_dungeon_room_tags"):
        try:
            tags = [str(t).lower() for t in (game._dungeon_room_tags(rid) or [])]
        except Exception:
            tags = []
    if not tags:
        tags = [str(t).lower() for t in (r.get("tags") or []) if str(t).strip()]

    d = r.get("_delta") if isinstance(r.get("_delta"), dict) else {}
    resolved_keys = set(str(x) for x in (d.get("resolution_keys") or r.get("resolution_keys") or []) if str(x).strip())

    party = getattr(game, "party", None)
    members = list(getattr(party, "members", []) or [])
    living = [m for m in members if int(getattr(m, "hp", 0) or 0) > 0]
    injured = [m for m in living if int(getattr(m, "hp", 0) or 0) < int(getattr(m, "hp_max", 0) or 0)]

    low_light = int(getattr(game, "torch_turns_left", 0) or 0) <= 1 and int(getattr(game, "magical_light_turns", 0) or 0) <= 0

    return {
        "room_id": int(rid),
        "depth": int(depth),
        "room_tags": list(tags),
        "cleared": bool(r.get("cleared", False)),
        "secrets_found": bool(r.get("secret_found", False)),
        "hazards_resolved": bool(r.get("trap_disarmed", False) or r.get("hazard_resolved", False)),
        "party_ready": bool(living) and len(injured) <= max(1, len(living) // 2),
        "low_light": bool(low_light),
        "exits": sorted(str(k) for k in ((r.get("exits") or {}).keys())),
        "resolution_keys": sorted(resolved_keys),
    }
