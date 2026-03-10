from __future__ import annotations

from .grid_map import has_line_of_sight, manhattan
from .targeting import tile_is_visible


def lit_tiles_for_side(state, side: str) -> set[tuple[int,int]]:
    lit: set[tuple[int,int]] = set()
    for u in state.living_units().values():
        if u.side != side:
            continue
        r = int(getattr(u, "light_radius", 0) or 0)
        if r <= 0:
            continue
        ux, uy = u.pos
        for x in range(state.gm.width):
            for y in range(state.gm.height):
                if abs(x - ux) + abs(y - uy) <= r:
                    lit.add((x, y))
    return lit


def unit_visible_to_any_enemy(game, state, u) -> bool:
    """Return True if any living enemy has LOS and can see u by light rules."""
    enemy_side = "foe" if u.side == "pc" else "pc"
    lit = lit_tiles_for_side(state, enemy_side)
    for e in state.living_units().values():
        if e.side != enemy_side:
            continue
        if manhattan(e.pos, u.pos) == 1:
            return True
        if not has_line_of_sight(state.gm, e.pos, u.pos):
            continue
        # visibility is evaluated from enemy perspective using enemy-side lighting.
        if tile_is_visible(lit, e.pos, u.pos):
            return True
    return False


def adjacent_to_cover(state, u) -> bool:
    x, y = u.pos
    for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
        if state.gm.is_cover(x+dx, y+dy):
            return True
    return False


def tile_in_darkness_against_enemies(state, u) -> bool:
    enemy_side = "foe" if u.side == "pc" else "pc"
    lit = lit_tiles_for_side(state, enemy_side)
    return u.pos not in lit


def can_attempt_hide(game, state, u) -> tuple[bool, str, bool]:
    """Returns (ok, reason, darkness_bonus)."""
    if unit_visible_to_any_enemy(game, state, u):
        # Still allow if adjacent to cover or in darkness (ducking behind something).
        if adjacent_to_cover(state, u):
            return True, "cover", False
        if tile_in_darkness_against_enemies(state, u):
            return True, "darkness", True
        return False, "in enemy sight", False
    # not visible: ok; darkness bonus if in darkness
    return True, "unseen", tile_in_darkness_against_enemies(state, u)


def hide_roll_success(u, roll_d6: int, darkness_bonus: bool = False) -> bool:
    """Simple OSR-facing hide/move-silent roll."""
    roll = int(roll_d6)
    # base success on 1-2
    thresh = 2
    # thieves better at stealth
    cls = str(getattr(u.actor, "cls", "") or getattr(u.actor, "class_name", "") or "").lower()
    if "thief" in cls:
        thresh = 4
    if darkness_bonus:
        thresh = min(5, thresh + 1)
    return roll <= thresh


def update_detection(game, state) -> list[str]:
    """Clear hidden flags for units that become detected. Returns list of unit_ids detected."""
    detected: list[str] = []
    for u in state.living_units().values():
        if not getattr(u, "hidden", False):
            continue
        if unit_visible_to_any_enemy(game, state, u):
            u.hidden = False
            detected.append(u.unit_id)
    return detected
