from __future__ import annotations

from typing import Any

from .status_lifecycle import leave_combat


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
    leave_combat(actor, marker="fled")
    return "flee"


def is_active_hostile_target(target: Any, enemies: list[Any]) -> bool:
    """Shared theater target legality for hostile attack declarations/execution."""
    if target is None:
        return False
    try:
        if int(getattr(target, "hp", 0) or 0) <= 0:
            return False
    except Exception:
        return False
    try:
        if "fled" in (getattr(target, "effects", []) or []):
            return False
    except Exception:
        return False
    return any(e is target for e in (enemies or []))



def opportunity_attackers_on_leave(
    *,
    mover_side: str,
    old_pos: tuple[int, int],
    new_pos: tuple[int, int],
    candidates: list[Any],
) -> list[str]:
    """Return sorted threatening enemy unit_ids that lose adjacency when mover leaves.

    Shared disengage/opportunity determination primitive for grid execution-time use.
    """
    def _adj(a: tuple[int, int], b: tuple[int, int]) -> bool:
        return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1])) == 1

    old_adj: set[str] = set()
    new_adj: set[str] = set()
    for c in list(candidates or []):
        try:
            if not bool(getattr(c, "is_alive")()):
                continue
        except Exception:
            continue
        if str(getattr(c, "side", "")) == str(mover_side):
            continue
        uid = str(getattr(c, "unit_id", "") or "")
        if not uid:
            continue
        pos = getattr(c, "pos", None)
        if not isinstance(pos, tuple) or len(pos) != 2:
            continue
        if _adj(old_pos, pos):
            old_adj.add(uid)
        if _adj(new_pos, pos):
            new_adj.add(uid)
    return sorted(list(old_adj - new_adj))
