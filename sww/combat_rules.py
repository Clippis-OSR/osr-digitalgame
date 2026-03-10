from __future__ import annotations

from typing import Any


def is_melee_engaged(combat_distance_ft: int | None) -> bool:
    """Central melee-engagement authority for theater-of-the-mind combat."""
    if combat_distance_ft is None:
        return False
    return int(combat_distance_ft) <= 10


def can_use_missile(combat_distance_ft: int | None) -> bool:
    """Missiles are disallowed when lines are fully closed into melee."""
    if combat_distance_ft is None:
        return True
    return int(combat_distance_ft) > 10


def shooting_into_melee_penalty(combat_distance_ft: int | None) -> int:
    """Penalty for firing into fully entangled melee."""
    if combat_distance_ft is None:
        return 0
    return -4 if int(combat_distance_ft) <= 0 else 0


def foe_frontage_limit(combat_distance_ft: int | None, party_living_count: int) -> int | None:
    """Maximum simultaneous foe melee attackers when lines are engaged."""
    if not is_melee_engaged(combat_distance_ft):
        return None
    return max(1, int(party_living_count))


def apply_forced_retreat(actor: Any) -> str:
    """Apply morale-driven flee transition and return resolved state.

    Returns one of: 'flee', 'cower'.
    """
    if bool(getattr(actor, "is_pc", False)):
        return "cower"
    effects = list(getattr(actor, "effects", []) or [])
    if "fled" not in effects:
        effects.append("fled")
    actor.effects = effects
    return "flee"
