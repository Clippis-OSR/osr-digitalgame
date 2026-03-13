from __future__ import annotations

from typing import Any


def _norm_tags(ctx: dict[str, Any]) -> set[str]:
    return {str(t).strip().lower() for t in (ctx.get("room_tags") or []) if str(t).strip()}


def choose_archetype(*, room: dict[str, Any], ctx: dict[str, Any], role: str, world_seed: int) -> str:
    """Deterministically choose a non-combat encounter archetype for this room."""
    tags = _norm_tags(ctx)
    kind = str((ctx.get("scene_kind") or "")).strip().lower()
    rid = int(ctx.get("room_id", room.get("id", 0)) or 0)
    depth = int(ctx.get("depth", room.get("depth", 1)) or 1)

    if "shrine" in tags or "clue_target:boss" in tags or kind == "lore":
        return "omen_echo"
    if role in {"guard", "lair"} or kind == "occupant":
        return "neutral_creature" if ((rid + depth + int(world_seed)) % 2 == 0) else "stranded_npc"
    if "role:transit" in tags or kind == "rest":
        return "environmental_scene"

    pool = ["stranded_npc", "neutral_creature", "omen_echo", "environmental_scene"]
    idx = (rid * 37 + depth * 13 + int(world_seed) * 3) % len(pool)
    return pool[idx]


def build_encounter(archetype: str, *, room: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    rid = int(ctx.get("room_id", room.get("id", 0)) or 0)
    depth = int(ctx.get("depth", room.get("depth", 1)) or 1)
    eid = f"nc:{archetype}:{rid}:{depth}"

    if archetype == "stranded_npc":
        return {
            "id": eid,
            "archetype": archetype,
            "status": "active",
            "state": {"aid_given": False},
            "prompt": "A trapped delver begs for help but seems alert enough to bargain.",
            "choices": [
                {"id": "aid", "label": "Share supplies and free them", "effects": ["ration:-1", "clue:stable_routes", "heal:2"], "resolve": True},
                {"id": "question", "label": "Question them from a distance", "effects": ["clue:hidden_route", "noise:+1"], "resolve": True},
                {"id": "leave", "label": "Leave them and move on", "effects": [], "resolve": True},
            ],
            "history": [],
        }

    if archetype == "neutral_creature":
        return {
            "id": eid,
            "archetype": archetype,
            "status": "active",
            "state": {"observed": False},
            "prompt": "A creature watches from the rubble, uncertain but not attacking.",
            "choices": [
                {"id": "observe", "label": "Observe quietly", "effects": ["mark:observed"], "resolve": False},
                {"id": "calm", "label": "Offer a calming gesture", "effects": ["check:wis", "clue:creature_path"], "resolve": True},
                {"id": "retreat", "label": "Back away slowly", "effects": ["noise:+0"], "resolve": True},
            ],
            "history": [],
        }

    if archetype == "omen_echo":
        return {
            "id": eid,
            "archetype": archetype,
            "status": "active",
            "state": {},
            "prompt": "A cold omen ripples through the room, carrying fragments of old warnings.",
            "choices": [
                {"id": "commune", "label": "Listen to the omen", "effects": ["clue:deeper_defense", "light:+2"], "resolve": True},
                {"id": "record", "label": "Record the omen as field notes", "effects": ["loot:item:Annotated omen notes"], "resolve": True},
                {"id": "ward", "label": "Trace protective signs and continue", "effects": ["resolve:hazard"], "resolve": True},
            ],
            "history": [],
        }

    return {
        "id": eid,
        "archetype": "environmental_scene",
        "status": "active",
        "state": {},
        "prompt": "A collapsed section blocks easy movement; the room can be worked safely with care.",
        "choices": [
            {"id": "clear", "label": "Clear a safer path", "effects": ["check:str", "noise:+1", "resolve:hazard"], "resolve": True},
            {"id": "scavenge", "label": "Scavenge useful scraps", "effects": ["loot:gp:8", "noise:+1"], "resolve": True},
            {"id": "bypass", "label": "Bypass and leave it", "effects": [], "resolve": True},
        ],
        "history": [],
    }
