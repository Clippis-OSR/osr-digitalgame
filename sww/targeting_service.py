from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .combat_rules import is_active_hostile_target
from .spells_grid import can_target, template_for_spell
from .targeting import (
    is_valid_melee_pair,
    is_valid_missile_pair,
    valid_melee_targets,
    valid_missile_targets,
)


@dataclass(frozen=True)
class TargetingService:
    """Read-only targeting legality service.

    This class centralizes targeting checks used by theater combat, grid combat,
    and spell targeting without changing existing rule behavior.
    """

    @staticmethod
    def legal_target_exists(*, enemies: list[Any]) -> bool:
        return any(TargetingService.theater_target_is_valid(e, enemies) for e in list(enemies or []))

    @staticmethod
    def theater_target_is_valid(target: Any, enemies: list[Any]) -> bool:
        return is_active_hostile_target(target, enemies)

    @staticmethod
    def is_enemy_target(*, attacker_side: str, target_side: str, attacker_id: str | None = None, target_id: str | None = None) -> bool:
        if attacker_id is not None and target_id is not None and str(attacker_id) == str(target_id):
            return False
        return str(attacker_side) != str(target_side)

    @staticmethod
    def is_ally_target(*, attacker_side: str, target_side: str, attacker_id: str | None = None, target_id: str | None = None) -> bool:
        if attacker_id is not None and target_id is not None and str(attacker_id) == str(target_id):
            return False
        return str(attacker_side) == str(target_side)

    @staticmethod
    def melee_pair_is_legal(*, attacker_pos: tuple[int, int], target_pos: tuple[int, int]) -> bool:
        return is_valid_melee_pair(attacker_pos, target_pos)

    @staticmethod
    def melee_target_ids(*, attacker_id: str, attacker_pos: tuple[int, int], attacker_side: str, living: dict[str, tuple[str, tuple[int, int]]]) -> set[str]:
        return valid_melee_targets(attacker_id, attacker_pos, attacker_side, living)

    @staticmethod
    def missile_pair_is_legal(
        *,
        gm,
        attacker_pos: tuple[int, int],
        target_pos: tuple[int, int],
        lit_tiles: set[tuple[int, int]] | None = None,
        max_tiles: int = 12,
    ) -> bool:
        return is_valid_missile_pair(gm, attacker_pos, target_pos, max_tiles=max_tiles, lit_tiles=lit_tiles)

    @staticmethod
    def missile_target_ids(
        *,
        gm,
        attacker_id: str,
        attacker_pos: tuple[int, int],
        attacker_side: str,
        living: dict[str, tuple[str, tuple[int, int]]],
        lit_tiles: set[tuple[int, int]] | None = None,
        max_tiles: int = 12,
    ) -> set[str]:
        return valid_missile_targets(
            gm,
            attacker_id,
            attacker_pos,
            attacker_side,
            living,
            max_tiles=max_tiles,
            lit_tiles=lit_tiles,
        )

    @staticmethod
    def spell_target_is_legal(*, gm, caster_pos: tuple[int, int], target_pos: tuple[int, int], spell_name: str) -> bool:
        tmpl = template_for_spell(spell_name)
        return can_target(gm, caster_pos, target_pos, tmpl)
