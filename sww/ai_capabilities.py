from __future__ import annotations

from typing import Any

from .combat_rules import can_use_missile


CAPABILITIES = {"melee", "ranged", "spellcasting", "breath", "gaze", "special", "morale"}


MORALE_BEHAVIOR_BY_AGGRESSION = {
    "high": "press",
    "pack": "opportunistic",
    "cautious": "cautious",
    "fearful": "cautious",
    "normal": "steady",
}


def _special_types(actor: Any) -> set[str]:
    out: set[str] = set()
    for spec in list(getattr(actor, "specials", []) or []):
        if not isinstance(spec, dict):
            continue
        out.add(str(spec.get("type") or "").strip().lower())
    txt = str(getattr(actor, "special_text", "") or "").strip().lower()
    if txt:
        if "breath" in txt:
            out.add("breath")
        if "gaze" in txt:
            out.add("gaze")
    return out


def detect_capabilities(actor: Any, game: Any) -> set[str]:
    caps: set[str] = {"melee", "morale"}
    if str(getattr(game, "_weapon_kind", lambda _a: "melee")(actor)).lower() == "missile":
        caps.add("ranged")
    if list(getattr(actor, "spells_prepared", []) or []):
        caps.add("spellcasting")

    spec_types = _special_types(actor)
    if "breath" in spec_types:
        caps.add("breath")
    if "gaze" in spec_types or "gaze_petrification" in spec_types:
        caps.add("gaze")
    if spec_types:
        caps.add("special")
    return caps


def morale_behavior(aggression: str) -> str:
    return MORALE_BEHAVIOR_BY_AGGRESSION.get(str(aggression or "normal").strip().lower(), "steady")


def choose_attack_mode(*, capabilities: set[str], combat_distance_ft: int | None, prefer_ranged: bool) -> str:
    if prefer_ranged and "ranged" in capabilities and can_use_missile(combat_distance_ft):
        return "missile"
    return "melee"
