from __future__ import annotations

from typing import Any


Effect = dict[str, Any]


def _norm_tags(ctx: dict[str, Any]) -> set[str]:
    return {str(t).strip().lower() for t in (ctx.get("room_tags") or []) if str(t).strip()}


def _eff(etype: str, **params: Any) -> Effect:
    rec: Effect = {"type": str(etype)}
    rec.update(params)
    return rec


def _parse_legacy_effect(token: str) -> Effect:
    tok = str(token or "").strip().lower()
    if tok == "ration:-1":
        return _eff("ration", delta=-1)
    if tok.startswith("heal:"):
        return _eff("heal", amount=int(tok.split(":", 1)[1] or 0))
    if tok.startswith("clue:"):
        return _eff("clue", clue_id=tok.split(":", 1)[1])
    if tok.startswith("loot:item:"):
        return _eff("loot_item", item_name=str(token).split(":", 2)[2])
    if tok.startswith("loot:gp:"):
        return _eff("loot_gp", gp=int(tok.split(":", 2)[2] or 0))
    if tok.startswith("light:+"):
        return _eff("light", turns=int(tok.split("+", 1)[1] or 0))
    if tok.startswith("noise:"):
        return _eff("noise", delta=int(tok.split(":", 1)[1] or 0))
    if tok.startswith("mark:"):
        return _eff("mark", key=tok.split(":", 1)[1], value=True)
    if tok.startswith("check:"):
        return _eff("check", stat=tok.split(":", 1)[1])
    if tok == "resolve:hazard":
        return _eff("resolve_hazard")
    return _eff("noop", legacy_token=str(token))


def normalize_effect(effect: Any) -> Effect:
    if isinstance(effect, dict):
        etype = str(effect.get("type") or "").strip().lower()
        if etype:
            return dict(effect)
        return _eff("noop")
    return _parse_legacy_effect(str(effect or ""))


def normalize_encounter(encounter: dict[str, Any] | None) -> dict[str, Any]:
    enc = dict(encounter or {})
    choices = []
    for ch in (enc.get("choices") or []):
        if not isinstance(ch, dict):
            continue
        ch2 = dict(ch)
        ch2["effects"] = [normalize_effect(e) for e in (ch.get("effects") or [])]
        choices.append(ch2)
    enc["choices"] = choices
    if not isinstance(enc.get("history"), list):
        enc["history"] = []
    if not isinstance(enc.get("state"), dict):
        enc["state"] = {}
    enc["status"] = str(enc.get("status") or "active")
    return enc


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
        return normalize_encounter({
            "id": eid,
            "archetype": archetype,
            "status": "active",
            "state": {"aid_given": False},
            "prompt": "A trapped delver begs for help but seems alert enough to bargain.",
            "choices": [
                {"id": "aid", "label": "Share supplies and free them", "effects": [_eff("ration", delta=-1), _eff("clue", clue_id="stable_routes"), _eff("heal", amount=2)], "resolve": True},
                {"id": "question", "label": "Question them from a distance", "effects": [_eff("clue", clue_id="hidden_route"), _eff("noise", delta=1)], "resolve": True},
                {"id": "leave", "label": "Leave them and move on", "effects": [], "resolve": True},
            ],
            "history": [],
        })

    if archetype == "neutral_creature":
        return normalize_encounter({
            "id": eid,
            "archetype": archetype,
            "status": "active",
            "state": {"observed": False},
            "prompt": "A creature watches from the rubble, uncertain but not attacking.",
            "choices": [
                {"id": "observe", "label": "Observe quietly", "effects": [_eff("mark", key="observed", value=True)], "resolve": False},
                {"id": "calm", "label": "Offer a calming gesture", "effects": [_eff("check", stat="wis"), _eff("clue", clue_id="creature_path")], "resolve": True},
                {"id": "retreat", "label": "Back away slowly", "effects": [_eff("noise", delta=0)], "resolve": True},
            ],
            "history": [],
        })

    if archetype == "omen_echo":
        return normalize_encounter({
            "id": eid,
            "archetype": archetype,
            "status": "active",
            "state": {},
            "prompt": "A cold omen ripples through the room, carrying fragments of old warnings.",
            "choices": [
                {"id": "commune", "label": "Listen to the omen", "effects": [_eff("clue", clue_id="deeper_defense"), _eff("light", turns=2)], "resolve": True},
                {"id": "record", "label": "Record the omen as field notes", "effects": [_eff("loot_item", item_name="Annotated omen notes")], "resolve": True},
                {"id": "ward", "label": "Trace protective signs and continue", "effects": [_eff("resolve_hazard")], "resolve": True},
            ],
            "history": [],
        })

    return normalize_encounter({
        "id": eid,
        "archetype": "environmental_scene",
        "status": "active",
        "state": {},
        "prompt": "A collapsed section blocks easy movement; the room can be worked safely with care.",
        "choices": [
            {"id": "clear", "label": "Clear a safer path", "effects": [_eff("check", stat="str"), _eff("noise", delta=1), _eff("resolve_hazard")], "resolve": True},
            {"id": "scavenge", "label": "Scavenge useful scraps", "effects": [_eff("loot_gp", gp=8), _eff("noise", delta=1)], "resolve": True},
            {"id": "bypass", "label": "Bypass and leave it", "effects": [], "resolve": True},
        ],
        "history": [],
    })
