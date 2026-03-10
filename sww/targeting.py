from __future__ import annotations

from .grid_map import GridMap, has_line_of_sight, manhattan


def tile_is_visible(lit_tiles: set[tuple[int, int]] | None, caster_pos: tuple[int,int], target_pos: tuple[int,int]) -> bool:
    """Visibility rule: if lit_tiles provided, missiles/spells require illumination unless adjacent."""
    if lit_tiles is None:
        return True
    if manhattan(caster_pos, target_pos) == 1:
        return True
    return target_pos in lit_tiles


def adjacent(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return manhattan(a, b) == 1


def in_missile_range(a: tuple[int, int], b: tuple[int, int], max_tiles: int = 12) -> bool:
    return manhattan(a, b) <= max_tiles


def valid_melee_targets(
    unit_id: str,
    unit_pos: tuple[int, int],
    unit_side: str,
    living: dict[str, tuple[str, tuple[int, int]]],
) -> set[str]:
    """living maps unit_id -> (side, pos)."""
    out: set[str] = set()
    for tid, (side, pos) in living.items():
        if tid == unit_id or side == unit_side:
            continue
        if adjacent(unit_pos, pos):
            out.add(tid)
    return out


def valid_missile_targets(
    gm: GridMap,
    unit_id: str,
    unit_pos: tuple[int, int],
    unit_side: str,
    living: dict[str, tuple[str, tuple[int, int]]],
    max_tiles: int = 12,
    lit_tiles: set[tuple[int, int]] | None = None,
) -> set[str]:
    out: set[str] = set()
    for tid, (side, pos) in living.items():
        if tid == unit_id or side == unit_side:
            continue
        if not in_missile_range(unit_pos, pos, max_tiles=max_tiles):
            continue
        if not has_line_of_sight(gm, unit_pos, pos):
            continue
        if not tile_is_visible(lit_tiles, unit_pos, pos):
            continue
        out.add(tid)
    return out


from .spells_grid import SpellTemplate, template_for_spell, can_target, aoe_tiles


def valid_spell_target_tiles(
    gm: GridMap,
    caster_pos: tuple[int, int],
    spell_name: str,
    lit_tiles: set[tuple[int, int]] | None = None,
) -> set[tuple[int, int]]:
    """Return all tiles that are valid targets for a spell, ignoring occupancy.

    Used for overlays / validation.
    """
    tmpl = template_for_spell(spell_name)
    out: set[tuple[int, int]] = set()
    for y in range(gm.height):
        for x in range(gm.width):
            if can_target(gm, caster_pos, (x, y), tmpl):
                out.add((x, y))
    return out


def spell_aoe_preview_tiles(
    gm: GridMap,
    caster_pos: tuple[int, int],
    spell_name: str,
    target_pos: tuple[int, int],
) -> set[tuple[int, int]]:
    tmpl = template_for_spell(spell_name)
    if not can_target(gm, caster_pos, target_pos, tmpl):
        return set()
    return aoe_tiles(gm, caster_pos, target_pos, tmpl)
