from __future__ import annotations

from dataclasses import dataclass

from .grid_state import GridBattleState
from .pathing import bfs_movement_range, bfs_shortest_path
from .targeting import spell_aoe_preview_tiles
from .targeting_service import TargetingService


@dataclass
class UIModel:
    selected_unit_id: str | None
    hovered_tile: tuple[int, int] | None
    move_tiles: set[tuple[int, int]]
    move_costs: dict[tuple[int, int], int]
    preview_path: list[tuple[int, int]]
    valid_targets: set[str]
    spell_preview_tiles: set[tuple[int, int]]
    lit_tiles: set[tuple[int, int]]


def compute_ui_model(
    state: GridBattleState,
    selected_unit_id: str | None,
    hovered_tile: tuple[int, int] | None,
    action_id: str | None,
) -> UIModel:
    active = state.active_unit()
    sel = state.units.get(selected_unit_id) if selected_unit_id else None

    move_costs: dict[tuple[int, int], int] = {}
    move_tiles: set[tuple[int, int]] = set()
    preview_path: list[tuple[int, int]] = []
    valid_targets: set[str] = set()
    spell_preview_tiles: set[tuple[int, int]] = set()

    lit_tiles: set[tuple[int, int]] = set()
    # Light/visibility: union of all unit light radii.
    for u in state.living_units().values():
        r = int(getattr(u, "light_radius", 0) or 0)
        if r <= 0:
            continue
        ux, uy = u.pos
        for x in range(state.gm.width):
            for y in range(state.gm.height):
                if abs(x - ux) + abs(y - uy) <= r:
                    lit_tiles.add((x, y))

    if sel and active and sel.unit_id == active.unit_id and sel.is_alive():
        blocked = set(state.occupancy.keys())
        blocked.discard(sel.pos)

        if action_id in ("MOVE", "MOVE_MELEE"):
            move_costs = bfs_movement_range(state.gm, sel.pos, sel.move_remaining, blocked)
            move_tiles = set(move_costs.keys())
            if hovered_tile and hovered_tile in move_tiles and hovered_tile != sel.pos:
                preview_path = bfs_shortest_path(state.gm, sel.pos, hovered_tile, blocked)

        living = {uid: (u.side, u.pos) for uid, u in state.living_units().items()}
        if action_id == "MELEE":
            valid_targets = {tid for tid in TargetingService.melee_target_ids(attacker_id=sel.unit_id, attacker_pos=sel.pos, attacker_side=sel.side, living=living)
                             if not (getattr(state.units.get(tid), 'hidden', False) and state.units.get(tid).side != sel.side)}
        elif action_id == "MISSILE":
            valid_targets = {tid for tid in TargetingService.missile_target_ids(gm=state.gm, attacker_id=sel.unit_id, attacker_pos=sel.pos, attacker_side=sel.side, living=living, lit_tiles=lit_tiles)
                             if not (getattr(state.units.get(tid), 'hidden', False) and state.units.get(tid).side != sel.side)}


        elif action_id and action_id.startswith("CAST:"):
            # CAST:<spell_name>
            spell_name = action_id.split(":", 1)[1]
            if hovered_tile:
                spell_preview_tiles = spell_aoe_preview_tiles(state.gm, sel.pos, spell_name, hovered_tile)

    return UIModel(
        selected_unit_id=selected_unit_id,
        hovered_tile=hovered_tile,
        move_tiles=move_tiles,
        move_costs=move_costs,
        preview_path=preview_path,
        valid_targets=valid_targets,
        spell_preview_tiles=spell_preview_tiles,
        lit_tiles=lit_tiles,
    )
