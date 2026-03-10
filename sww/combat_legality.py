from __future__ import annotations

from typing import Any

from .combat_rules import is_active_hostile_target
from .targeting import valid_melee_targets, valid_missile_targets, is_valid_melee_pair, is_valid_missile_pair


def theater_target_is_valid(target: Any, enemies: list[Any]) -> bool:
    """Read-only theater target legality seam.

    Delegates to existing combat_rules behavior to preserve semantics.
    """
    return is_active_hostile_target(target, enemies)


def grid_target_is_attackable(
    *,
    gm,
    attacker_id: str,
    attacker_pos: tuple[int, int],
    attacker_side: str,
    target_id: str,
    living: dict[str, tuple[str, tuple[int, int]]],
    mode: str,
    lit_tiles: set[tuple[int, int]] | None = None,
    max_tiles: int = 12,
) -> bool:
    """Read-only grid attack legality seam for planning/command building."""
    m = str(mode or "").strip().lower()
    if m == "melee":
        return str(target_id) in valid_melee_targets(attacker_id, attacker_pos, attacker_side, living)
    if m == "missile":
        return str(target_id) in valid_missile_targets(
            gm,
            attacker_id,
            attacker_pos,
            attacker_side,
            living,
            max_tiles=max_tiles,
            lit_tiles=lit_tiles,
        )
    return False


def grid_pair_is_attack_legal(*, gm, attacker_pos: tuple[int, int], target_pos: tuple[int, int], mode: str, lit_tiles=None, max_tiles: int = 12) -> bool:
    """Read-only grid legality seam for execution-time pair checks."""
    m = str(mode or "").strip().lower()
    if m == "melee":
        return is_valid_melee_pair(attacker_pos, target_pos)
    if m == "missile":
        return is_valid_missile_pair(gm, attacker_pos, target_pos, max_tiles=max_tiles, lit_tiles=lit_tiles)
    return False
