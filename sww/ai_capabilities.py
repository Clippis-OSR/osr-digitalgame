from __future__ import annotations

from typing import Any

from .combat_rules import can_use_missile


CAPABILITIES = {"melee", "ranged", "spellcasting", "special", "morale"}


def detect_capabilities(actor: Any, game: Any) -> set[str]:
    caps: set[str] = {"melee", "morale"}
    if str(getattr(game, "_weapon_kind", lambda _a: "melee")(actor)).lower() == "missile":
        caps.add("ranged")
    if list(getattr(actor, "spells_prepared", []) or []):
        caps.add("spellcasting")
    if list(getattr(actor, "specials", []) or []) or str(getattr(actor, "special_text", "") or "").strip():
        caps.add("special")
    return caps


def choose_attack_mode(*, capabilities: set[str], combat_distance_ft: int | None, prefer_ranged: bool) -> str:
    if prefer_ranged and "ranged" in capabilities and can_use_missile(combat_distance_ft):
        return "missile"
    return "melee"
