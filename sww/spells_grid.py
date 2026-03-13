from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .grid_map import GridMap, bresenham_line, has_line_of_sight, manhattan
from .status_service import StatusService


@dataclass(frozen=True)
class SpellTemplate:
    kind: str  # "unit" | "burst" | "line"
    range_tiles: int
    radius: int = 0
    length: int = 0  # for line


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def template_for_spell(spell_name: str) -> SpellTemplate:
    """Heuristic templates for grid casting.

    This is intentionally small and safe; extend as needed.
    """
    n = _norm(spell_name)

    if n == "magic missile":
        return SpellTemplate(kind="unit", range_tiles=12)
    if n == "sleep":
        return SpellTemplate(kind="burst", range_tiles=18, radius=3)
    if n == "fireball":
        return SpellTemplate(kind="burst", range_tiles=24, radius=3)
    if n == "lightning bolt":
        return SpellTemplate(kind="line", range_tiles=24, length=12)
    if n == "web":
        return SpellTemplate(kind="burst", range_tiles=18, radius=3)
    # Default: treat as single-target at missile range.
    return SpellTemplate(kind="unit", range_tiles=12)


def aoe_tiles(gm: GridMap, caster_pos: tuple[int, int], target_pos: tuple[int, int], tmpl: SpellTemplate) -> set[tuple[int, int]]:
    if tmpl.kind == "unit":
        return {target_pos}
    if tmpl.kind == "burst":
        out: set[tuple[int, int]] = set()
        tx, ty = target_pos
        for y in range(ty - tmpl.radius, ty + tmpl.radius + 1):
            for x in range(tx - tmpl.radius, tx + tmpl.radius + 1):
                if not gm.in_bounds(x, y):
                    continue
                if manhattan((x, y), (tx, ty)) <= tmpl.radius:
                    out.add((x, y))
        return out
    if tmpl.kind == "line":
        pts = bresenham_line(caster_pos, target_pos)
        # Exclude caster tile.
        pts = pts[1:]
        return set(pts[: tmpl.length])
    return {target_pos}


def can_target(gm: GridMap, caster_pos: tuple[int, int], target_pos: tuple[int, int], tmpl: SpellTemplate) -> bool:
    # Range check.
    if manhattan(caster_pos, target_pos) > tmpl.range_tiles:
        return False
    # For line and unit, require LOS to target tile.
    if tmpl.kind in ("unit", "line"):
        if not has_line_of_sight(gm, caster_pos, target_pos):
            return False
    return True


def apply_spell(game: Any, gm: GridMap, caster: Any, spell_name: str, targets: list[Any]) -> dict[str, object]:
    """Very small grid spell resolver.

    Returns a dict with keys:
      - success: bool
      - damage: dict[target_id->amount]
      - slept: list[target_id]
      - info: str
    """
    n = _norm(spell_name)
    dice = getattr(game, "dice", None)

    out: dict[str, object] = {"success": True, "damage": {}, "slept": [], "info": ""}

    # Helper
    def deal(t, amt: int):
        if amt <= 0:
            return
        before = int(getattr(t, "hp", 0) or 0)
        setattr(t, "hp", max(0, before - int(amt)))
        out["damage"][getattr(t, "name", "?")] = int(out["damage"].get(getattr(t, "name", "?"), 0)) + int(before - getattr(t, "hp", 0))

    lvl = int(getattr(caster, "level", 1) or 1)

    if n == "magic missile":
        # S&W: 1d6+1, auto-hit.
        dmg = (dice.d(6) + 1) if dice else 4
        if targets:
            deal(targets[0], dmg)
        out["info"] = "Magic Missile."
        return out

    if n == "sleep":
        # Affect 2d6 HD worth of foes, lowest HD first. No save.
        hd_budget = (dice.d(6) + dice.d(6)) if dice else 7
        affected = 0
        for t in sorted(targets, key=lambda a: int(getattr(a, "hd", getattr(a, "hit_dice", 1)) or 1)):
            hd = int(getattr(t, "hd", getattr(t, "hit_dice", 1)) or 1)
            if hd <= 0:
                continue
            if hd > hd_budget:
                continue
            hd_budget -= hd
            StatusService.apply_status(t, "asleep", 6, mode="max")
            out["slept"].append(getattr(t, "name", "?"))
            affected += 1
            if hd_budget <= 0:
                break
        out["info"] = f"Sleep affects {affected} foe(s)."
        return out

    if n == "fireball":
        # Simplified: 1d6 per level (cap 10) to all targets.
        dice_count = min(10, max(1, lvl))
        dmg = sum((dice.d(6) if dice else 3) for _ in range(dice_count))
        for t in targets:
            deal(t, dmg)
        out["info"] = "Fireball explodes!"
        return out

    if n == "lightning bolt":
        # Simplified: 1d6 per level (cap 10) to all targets in line.
        dice_count = min(10, max(1, lvl))
        dmg = sum((dice.d(6) if dice else 3) for _ in range(dice_count))
        for t in targets:
            deal(t, dmg)
        out["info"] = "Lightning Bolt!"
        return out

    out["success"] = False
    out["info"] = f"{spell_name} has no grid implementation yet."
    return out
