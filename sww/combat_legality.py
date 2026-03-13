from __future__ import annotations

from typing import Any

from .targeting_service import TargetingService


def theater_target_is_valid(target: Any, enemies: list[Any]) -> bool:
    """Read-only theater target legality seam.

    Delegates to existing combat_rules behavior to preserve semantics.
    """
    return TargetingService.theater_target_is_valid(target, enemies)


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
        return str(target_id) in TargetingService.melee_target_ids(attacker_id=attacker_id, attacker_pos=attacker_pos, attacker_side=attacker_side, living=living)
    if m == "missile":
        return str(target_id) in TargetingService.missile_target_ids(
            gm=gm,
            attacker_id=attacker_id,
            attacker_pos=attacker_pos,
            attacker_side=attacker_side,
            living=living,
            max_tiles=max_tiles,
            lit_tiles=lit_tiles,
        )
    return False


def grid_pair_is_attack_legal(*, gm, attacker_pos: tuple[int, int], target_pos: tuple[int, int], mode: str, lit_tiles=None, max_tiles: int = 12) -> bool:
    """Read-only grid legality seam for execution-time pair checks."""
    m = str(mode or "").strip().lower()
    if m == "melee":
        return TargetingService.melee_pair_is_legal(attacker_pos=attacker_pos, target_pos=target_pos)
    if m == "missile":
        return TargetingService.missile_pair_is_legal(gm=gm, attacker_pos=attacker_pos, target_pos=target_pos, max_tiles=max_tiles, lit_tiles=lit_tiles)
    return False
