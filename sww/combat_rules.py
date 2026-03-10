from dataclasses import dataclass
from .grid_map import has_line_of_sight, manhattan
from .grid_state import GridBattleState, UnitState


@dataclass
class AttackCheck:
    ok: bool
    kind: str = "melee"
    to_hit_mod: int = 0


@dataclass
class StepCheck:
    ok: bool
    step_cost: int = 0


def validate_attack(state: GridBattleState, attacker: UnitState, target: UnitState, mode: str) -> AttackCheck:
    """Central rule authority for attack validation."""

    if not target.is_alive():
        return AttackCheck(False)

    kind = "melee" if mode == "melee" else "missile"

    if kind == "melee":
        if manhattan(attacker.pos, target.pos) != 1:
            return AttackCheck(False)
    else:
        if not has_line_of_sight(state.gm, attacker.pos, target.pos):
            return AttackCheck(False)

    to_hit_mod = 0
    st = getattr(target.actor, "status", {}) or {}
    to_hit_mod += int(st.get("cover", 0) or 0)

    return AttackCheck(True, kind, to_hit_mod)


def validate_step(state: GridBattleState, unit: UnitState, dest: tuple[int, int], blocked: set):
    x, y = dest

    if not state.gm.in_bounds(x, y):
        return StepCheck(False)

    if state.gm.blocks_movement(x, y):
        return StepCheck(False)

    if dest in blocked:
        return StepCheck(False)

    if manhattan(unit.pos, dest) != 1:
        return StepCheck(False)

    cost = int(state.gm.move_cost(x, y))

    if cost > unit.move_remaining:
        return StepCheck(False)

    return StepCheck(True, cost)


def can_take_cover(state: GridBattleState, unit: UnitState):

    x, y = unit.pos

    for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
        if state.gm.is_cover(x+dx, y+dy):
            return True

    return False


def opportunity_attackers_for_move(state: GridBattleState, unit: UnitState, old_pos, new_pos):

    enemies = []

    for u in state.units.values():
        if not u.is_alive():
            continue

        if u.side == unit.side:
            continue

        old_adj = manhattan(old_pos, u.pos) == 1
        new_adj = manhattan(new_pos, u.pos) == 1

        if old_adj and not new_adj:
            enemies.append(u)

    enemies.sort(key=lambda u: u.unit_id)
    return enemies
