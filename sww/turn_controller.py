from __future__ import annotations

from dataclasses import dataclass

from .battle_events import BattleEvent, evt
from .commands import CmdAttack, CmdCastSpell, CmdDoorAction, CmdDungeonAction, CmdHide, CmdMove, CmdMoveThenMelee, CmdTakeCover, CmdThrowItem, CmdParley, Command
from .grid_battle import resolve_command
from .grid_state import GridBattleState, UnitState
from .intents import (
    Intent,
    IntentCancel,
    IntentChooseAction,
    IntentChooseSpell,
    IntentChooseItem,
    IntentEndTurn,
    IntentHoverTile,
    IntentSelectTile,
    IntentSelectUnit,
)
from .pathing import bfs_movement_range, dijkstra_shortest_path
from .targeting import spell_aoe_preview_tiles
from .combat_legality import grid_target_is_attackable

from .rules import morale_check

from .stealth import update_detection, unit_visible_to_any_enemy


@dataclass
class SelectionState:
    selected_unit_id: str | None = None
    hovered_tile: tuple[int, int] | None = None
    action_id: str | None = "MOVE"  # MOVE | MELEE | MISSILE | COVER | CAST | MOVE_MELEE | HIDE
    selected_spell_name: str | None = None
    selected_item_id: str | None = None
    pending_move_path: list[tuple[int, int]] | None = None
    pending_move_dest: tuple[int, int] | None = None


class TurnController:
    """OSR grid controller with declared actions and phase resolution.

    Round flow:
      - Side initiative (1d12) determines `side_first`
      - DECLARE: each unit declares an action (targets stored now)
      - RESOLVE: MISSILES -> SPELLS -> MELEE (side_first acts first each phase)
    """

    def __init__(self, game, state: GridBattleState):
        self.game = game
        self.state = state
        self.sel = SelectionState()
        self.node: str = "DECLARE_SELECT_ACTION"

        if not self.state.declare_order:
            self.state.setup_default_initiative()
            self.state.events.extend(self.state.start_new_round(self.game))
            self.state.events.extend(self._maybe_wandering())

    # ---------- convenience ----------
    def active(self) -> UnitState | None:
        return self.state.declaring_unit() if self.state.phase == "DECLARE" else None

    def _living_map(self) -> dict[str, tuple[str, tuple[int, int]]]:
        return {uid: (u.side, u.pos) for uid, u in self.state.living_units().items()}

    def _living_map_visible_to(self, side: str) -> dict[str, tuple[str, tuple[int, int]]]:
        """Units visible/targetable to `side` (hidden enemies omitted)."""
        out: dict[str, tuple[str, tuple[int,int]]] = {}
        for uid, u in self.state.living_units().items():
            if getattr(u, 'hidden', False) and u.side != side:
                continue
            out[uid] = (u.side, u.pos)
        return out

    def _set_selected_to_active(self) -> None:
        a = self.active()
        self.sel.selected_unit_id = a.unit_id if a else None

    @staticmethod
    def _ordered_units(units: list[UnitState]) -> list[UnitState]:
        """Stable deterministic unit ordering for state-affecting decisions."""
        return sorted(list(units or []), key=lambda u: str(getattr(u, "unit_id", "")))

    # ---------- intents (player) ----------
    def handle_intent(self, intent: Intent) -> list[BattleEvent]:
        events: list[BattleEvent] = []

        if self.state.phase != "DECLARE":
            return events

        active = self.active()
        if not active or not active.is_alive():
            return events

        self._set_selected_to_active()

        # Only PCs accept interactive intents during DECLARE.
        if active.side != "pc":
            return events

        if isinstance(intent, IntentHoverTile):
            self.sel.hovered_tile = (int(intent.x), int(intent.y))
            return events

        if isinstance(intent, IntentCancel):
            self.state.declared.pop(active.unit_id, None)
            self.node = "DECLARE_SELECT_ACTION"
            self.sel.action_id = "MOVE"
            self.sel.selected_spell_name = None
            self.sel.selected_item_id = None
            self.sel.selected_item_id = None
            self.sel.pending_move_path = None
            self.sel.pending_move_dest = None
            events.append(evt("INFO", text=f"{active.unit_id}: declaration cleared."))
            self.state.events.extend(events)
            return events

        if isinstance(intent, IntentSelectUnit):
            # During DECLARE, only current unit is active.
            return events

        if isinstance(intent, IntentChooseAction):
            aid = str(intent.action_id)
            self.sel.action_id = aid
            self.sel.selected_spell_name = None
            self.sel.selected_item_id = None
            self.sel.selected_item_id = None
            self.sel.pending_move_path = None
            self.sel.pending_move_dest = None

            # P4.8.1: Social actions (only meaningful when foes are currently non-hostile).
            if aid in ("PARLEY", "BRIBE", "THREATEN"):
                stance = getattr(self.state, "foe_reaction", "unseen")
                if stance != "neutral":
                    events.append(evt("INFO", text="Parley/Bribe/Threaten are available only when foes are neutral."))
                    self.state.events.extend(events)
                    return events
                kind = "parley" if aid == "PARLEY" else ("bribe" if aid == "BRIBE" else "threaten")
                self._declare(active.unit_id, CmdParley(unit_id=active.unit_id, kind=kind), events)
                return events

            if aid == "MOVE":
                self.node = "DECLARE_SELECT_DEST"
                return events
            if aid in ("MELEE", "MISSILE"):
                self.node = "DECLARE_SELECT_TARGET"
                return events
            if aid == "MOVE_MELEE":
                self.node = "DECLARE_SELECT_DEST_MOVE_MELEE"
                return events

            if aid in ("OPEN_DOOR", "FORCE_DOOR", "SPIKE_DOOR"):
                self.node = "DECLARE_SELECT_DOOR_TILE"
                return events
            if aid in ("LISTEN", "SEARCH"):
                self.node = "DECLARE_SELECT_DUNGEON_TILE"
                return events
            if aid == "THROW_ITEM":
                self.node = "DECLARE_SELECT_ITEM"
                return events
            if aid == "HIDE":
                self._declare(active.unit_id, CmdHide(unit_id=active.unit_id), events)
                return events
            if aid == "COVER":
                self._declare(active.unit_id, CmdTakeCover(unit_id=active.unit_id), events)
                return events
            if aid == "CAST":
                prepared = list(getattr(active.actor, "spells_prepared", []) or [])
                if not prepared:
                    events.append(evt("INFO", text="No spells prepared."))
                    self.state.events.extend(events)
                    return events
                self.node = "DECLARE_SELECT_SPELL"
                return events
            return events

        if isinstance(intent, IntentChooseSpell):
            if self.node == "DECLARE_SELECT_SPELL":
                self.sel.selected_spell_name = str(intent.spell_name)
                self.sel.action_id = f"CAST:{self.sel.selected_spell_name}"
                self.node = "DECLARE_SELECT_SPELL_TARGET"
            return events

        if isinstance(intent, IntentEndTurn):
            self._declare(active.unit_id, None, events)
            return events

        
        if isinstance(intent, IntentChooseItem):
            if self.node != "DECLARE_SELECT_ITEM":
                return events
            self.sel.selected_item_id = str(intent.item_id)
            self.node = "DECLARE_SELECT_THROW_TILE"
            events.append(evt("INFO", text=f"{active.unit_id}: throwing {self.sel.selected_item_id}, select target tile."))
            self.state.events.extend(events)
            return events

        if isinstance(intent, IntentSelectTile):
            tx, ty = int(intent.x), int(intent.y)
            self.sel.hovered_tile = (tx, ty)

            if self.node == "DECLARE_SELECT_DEST" and self.sel.action_id == "MOVE":
                cmd = self._try_build_move(active.unit_id, (tx, ty))
                if cmd:
                    self._declare(active.unit_id, cmd, events)
                return events


            if self.node == "DECLARE_SELECT_DOOR_TILE" and self.sel.action_id in ("OPEN_DOOR", "FORCE_DOOR", "SPIKE_DOOR"):
                # must be adjacent to a door tile
                if self.sel.action_id == "OPEN_DOOR":
                    kind = "open"
                elif self.sel.action_id == "FORCE_DOOR":
                    kind = "force"
                else:
                    kind = "spike"
                cmd = CmdDoorAction(unit_id=active.unit_id, kind=kind, door_pos=(tx, ty))
                self._declare(active.unit_id, cmd, events)
                return events

            
            if self.node == "DECLARE_SELECT_DUNGEON_TILE" and self.sel.action_id in ("LISTEN","SEARCH"):
                kind = "listen" if self.sel.action_id == "LISTEN" else "search"
                cmd = CmdDungeonAction(unit_id=active.unit_id, kind=kind, target_pos=(tx, ty))
                self._declare(active.unit_id, cmd, events)
                return events

            if self.node == "DECLARE_SELECT_THROW_TILE" and self.sel.action_id == "THROW_ITEM":
                if not self.sel.selected_item_id:
                    events.append(evt("INFO", text="Select an item first."))
                    self.state.events.extend(events)
                    return events
                cmd = CmdThrowItem(unit_id=active.unit_id, item_id=self.sel.selected_item_id, target_pos=(tx, ty))
                self._declare(active.unit_id, cmd, events)
                return events


            if self.node == "DECLARE_SELECT_DEST_MOVE_MELEE" and self.sel.action_id == "MOVE_MELEE":
                cmd_move = self._try_build_move(active.unit_id, (tx, ty))
                if cmd_move:
                    self.sel.pending_move_path = list(cmd_move.path)
                    self.sel.pending_move_dest = (tx, ty)
                    self.node = "DECLARE_SELECT_TARGET_MOVE_MELEE"
                    events.append(evt("INFO", text="Select a melee target for after the move."))
                return events

            if self.node == "DECLARE_SELECT_TARGET_MOVE_MELEE" and self.sel.action_id == "MOVE_MELEE":
                tid = self.state.occupancy.get((tx, ty))
                if tid and self.sel.pending_move_dest and self.sel.pending_move_path:
                    living = self._living_map_visible_to(side)
                    if grid_target_is_attackable(
                        gm=self.state.gm,
                        attacker_id=active.unit_id,
                        attacker_pos=self.sel.pending_move_dest,
                        attacker_side=active.side,
                        target_id=tid,
                        living=living,
                        mode="melee",
                    ):
                        cmd = CmdMoveThenMelee(unit_id=active.unit_id, path=list(self.sel.pending_move_path), target_id=tid)
                        self._declare(active.unit_id, cmd, events)
                return events

            if self.node == "DECLARE_SELECT_TARGET" and self.sel.action_id in ("MELEE", "MISSILE"):
                tid = self.state.occupancy.get((tx, ty))
                if tid:
                    cmd = self._try_build_attack(active.unit_id, tid, self.sel.action_id)
                    if cmd:
                        self._declare(active.unit_id, cmd, events)
                return events

            if self.node == "DECLARE_SELECT_SPELL_TARGET" and (self.sel.selected_spell_name or "").strip():
                sp = str(self.sel.selected_spell_name)
                cmd = self._try_build_cast(active.unit_id, sp, (tx, ty))
                if cmd:
                    # Mark caster as casting so missile damage can disrupt.
                    apply_status(active.actor, "casting", {"spell": sp, "disrupted": False, "round": int(self.state.round_no)})
                    self._declare(active.unit_id, cmd, events)
                return events

        return events

    # ---------- AI declaration (foes) ----------
    def step_ai_if_needed(self) -> list[BattleEvent]:
        events: list[BattleEvent] = []
        if self.state.phase != "DECLARE":
            return events

        a = self.active()
        if not a or not a.is_alive() or a.side != "foe":
            return events

        # Morale states override normal AI.
        if a.morale_state in ("fallback", "rout"):
            cmd = self._ai_retreat_cmd(a)
            self._declare(a.unit_id, cmd, events)
            return events
        if a.morale_state == "surrender":
            self._declare(a.unit_id, None, events)
            return events

        # If foes are non-hostile (reaction), they hold position and do not attack.
        if getattr(self.state, 'foe_reaction', 'hostile') != 'hostile':
            self._declare(a.unit_id, None, events)
            return events

        living = self._living_map_visible_to(u.side)
        pcs = [(uid, pos) for uid, (side, pos) in living.items() if side == "pc" and not getattr(self.state.units.get(uid), 'hidden', False)]
        if not pcs:
            self._declare(a.unit_id, None, events)
            return events

        pcs.sort(key=lambda it: abs(it[1][0] - a.x) + abs(it[1][1] - a.y))
        tgt_id, tgt_pos = pcs[0]

        if a.actions_remaining > 0 and grid_target_is_attackable(
            gm=self.state.gm,
            attacker_id=a.unit_id,
            attacker_pos=a.pos,
            attacker_side=a.side,
            target_id=tgt_id,
            living=living,
            mode="melee",
        ):
            self._declare(a.unit_id, CmdAttack(unit_id=a.unit_id, target_id=tgt_id, mode="melee"), events)
            return events

        if a.actions_remaining > 0 and grid_target_is_attackable(
            gm=self.state.gm,
            attacker_id=a.unit_id,
            attacker_pos=a.pos,
            attacker_side=a.side,
            target_id=tgt_id,
            living=living,
            mode="missile",
        ):
            self._declare(a.unit_id, CmdAttack(unit_id=a.unit_id, target_id=tgt_id, mode="missile"), events)
            return events

        self.state.rebuild_occupancy()
        blocked = set(self.state.occupancy.keys())
        blocked.discard(a.pos)
        path = dijkstra_shortest_path(self.state.gm, a.pos, tgt_pos, blocked)
        if path and a.move_remaining > 0:
            self._declare(a.unit_id, CmdMove(unit_id=a.unit_id, path=path), events)
            return events

        self._declare(a.unit_id, None, events)
        return events

    def _ai_retreat_cmd(self, u: UnitState) -> Command | None:
        """Pick a deterministic retreat move away from nearest PC."""
        living = self._living_map()
        enemies = [pos for uid, (side, pos) in living.items() if side != u.side]
        if not enemies:
            return None

        self.state.rebuild_occupancy()
        blocked = set(self.state.occupancy.keys())
        blocked.discard(u.pos)

        reachable = bfs_movement_range(self.state.gm, u.pos, u.move_remaining, blocked)
        if not reachable:
            u.morale_state = "surrender"
            self.state.events.append(evt("UNIT_SURRENDERED", unit_id=u.unit_id))
            return None

        def nearest_dist(tile: tuple[int, int]) -> int:
            x, y = tile
            return min(abs(x - ex) + abs(y - ey) for ex, ey in enemies)

        cur_d = nearest_dist(u.pos)
        # Choose the tile with maximum nearest-enemy distance.
        best_tiles = sorted(reachable.keys())
        best_tile = u.pos
        best_d = cur_d
        best_cost = 10**9
        for t in best_tiles:
            d = nearest_dist(t)
            c = int(reachable.get(t, 10**9))
            if d > best_d or (d == best_d and c < best_cost):
                best_tile = t
                best_d = d
                best_cost = c

        if best_tile == u.pos:
            # Can't increase distance; if routed, surrender; if fallback, hold.
            if u.morale_state == "rout":
                u.morale_state = "surrender"
                self.state.events.append(evt("UNIT_SURRENDERED", unit_id=u.unit_id))
                return None
            return None

        path = dijkstra_shortest_path(self.state.gm, u.pos, best_tile, blocked)
        if not path:
            if u.morale_state == "rout":
                u.morale_state = "surrender"
                self.state.events.append(evt("UNIT_SURRENDERED", unit_id=u.unit_id))
            return None
        return CmdMove(unit_id=u.unit_id, path=path)

    # ---------- declaration + resolution ----------
    def _declare(self, unit_id: str, cmd: Command | None, events: list[BattleEvent]) -> None:
        self.state.declared[unit_id] = cmd
        events.append(evt("ACTION_DECLARED", unit_id=unit_id, action=self._cmd_label(cmd)))

        self.state.advance_declare()

        self.node = "DECLARE_SELECT_ACTION"
        self.sel.action_id = "MOVE"
        self.sel.selected_spell_name = None
        self.sel.pending_move_path = None
        self.sel.pending_move_dest = None
        self._set_selected_to_active()

        if self.state.is_declare_complete():
            events.extend(self._resolve_round())
            # Post-combat resolution before starting next round.
            if self._combat_over():
                events.extend(self._post_combat_resolution())
            else:
                self.state.round_no += 1
                events.extend(self.state.start_new_round(self.game))

        self.state.events.extend(events)


    
    def _maybe_wandering(self) -> list[BattleEvent]:
        """Dungeon-turn wandering monster check.

        We approximate 1 dungeon turn = 10 combat rounds. At the start of each dungeon turn,
        roll 1d6; on 1, spawn a small encounter at a map edge and roll surprise.
        """
        ev: list[BattleEvent] = []
        # Detection pass: hidden units that become visible are revealed.
        for uid in update_detection(self.game, self.state):
            ev.append(evt('DETECTED', unit_id=uid))
        # Reaction check on first LOS contact.
        ev.extend(self._check_first_contact_los())

        if (int(self.state.round_no) % int(self.state.dungeon_rounds_per_turn or 10)) != 0:
            return ev
        # Increment dungeon turn and check.
        self.state.dungeon_turn += 1
        roll = int(self.game.dice_rng.randint(1, 6))
        ev.append(evt("WANDERING_MONSTER_CHECK", roll=roll, dungeon_turn=int(self.state.dungeon_turn)))
        if roll != 1:
            return ev

        monster_id = "orc"
        count = int(self.game.dice_rng.randint(1, 4))
        # Find spawn tiles: any floor/door_open on right edge, else any edge.
        candidates: list[tuple[int,int]] = []
        for y in range(self.state.gm.height):
            x = self.state.gm.width - 1
            if self.state.gm.get(x,y) in ("floor","door_open") and (x,y) not in self.state.occupancy:
                candidates.append((x,y))
        if not candidates:
            for x in range(self.state.gm.width):
                for y in (0, self.state.gm.height-1):
                    if self.state.gm.get(x,y) in ("floor","door_open") and (x,y) not in self.state.occupancy:
                        candidates.append((x,y))
        if not candidates:
            return ev

        spawn_pos = candidates[int(self.game.dice_rng.randint(0, len(candidates)-1))]
        # Create monsters via encounter factory.
        m = self.game.content.get_monster(monster_id)
        for i in range(count):
            actor = self.game.encounters._mk_monster(m)
            uid = f"foe:w{self.state.dungeon_turn}:{i}"
            # Spread spawns near edge if possible.
            sx, sy = spawn_pos
            py = min(self.state.gm.height-1, max(0, sy + i))
            if (sx, py) in self.state.occupancy:
                py = sy
            self.state.add_unit(UnitState(unit_id=uid, actor=actor, side="foe", x=sx, y=py, move_remaining=6))
        self.state.rebuild_occupancy()
        ev.append(evt("WANDERING_MONSTER_ARRIVES", monster_id=monster_id, count=count, x=int(spawn_pos[0]), y=int(spawn_pos[1])))

        # Surprise: each side d6, 1-2 surprised.
        pc_roll = int(self.game.dice_rng.randint(1,6))
        foe_roll = int(self.game.dice_rng.randint(1,6))
        pc_surprised = pc_roll <= 2
        foe_surprised = foe_roll <= 2
        result = "none"
        if pc_surprised and not foe_surprised:
            self.state.set_surprise("pc", 1)
            result = "pc surprised"
        elif foe_surprised and not pc_surprised:
            self.state.set_surprise("foe", 1)
            result = "foe surprised"
        elif pc_surprised and foe_surprised:
            result = "both surprised"
        ev.append(evt("SURPRISE_ROLL", pc_roll=pc_roll, foe_roll=foe_roll, result=result))
        return ev


    # ---------- reaction (P4.8.0) ----------
    def _reaction_roll(self, context: str) -> list[BattleEvent]:
        """Roll 2d6 reaction for foes on first contact (deterministic)."""
        if getattr(self.state, "foe_reaction", "unseen") != "unseen":
            return []
        roll = int(self.game.dice_rng.randint(1,6)) + int(self.game.dice_rng.randint(1,6))
        # Classic OSR bands; we simplify to hostile vs neutral for tactical mode.
        stance = "hostile" if roll <= 6 else "neutral"
        self.state.foe_reaction = stance
        return [evt("REACTION_ROLL", roll=roll, stance=stance, context=context)]

    def _check_first_contact_los(self) -> list[BattleEvent]:
        """If any PC can see any foe by LOS+light, trigger reaction once."""
        if getattr(self.state, "foe_reaction", "unseen") != "unseen":
            return []
        # Any visible contact counts.
        pcs = self._ordered_units([u for u in self.state.living_units().values() if u.side == "pc"])
        foes = self._ordered_units([u for u in self.state.living_units().values() if u.side == "foe" and u.morale_state != "surrender"])
        if not pcs or not foes:
            return []
        from .stealth import lit_tiles_for_side
        from .grid_map import has_line_of_sight, manhattan
        from .targeting import tile_is_visible
        pc_lit = lit_tiles_for_side(self.state, "pc")
        for pc in pcs:
            for fo in foes:
                if manhattan(pc.pos, fo.pos) == 1:
                    return self._reaction_roll("contact")
                if not has_line_of_sight(self.state.gm, pc.pos, fo.pos):
                    continue
                if not tile_is_visible(pc_lit, pc.pos, fo.pos):
                    continue
                return self._reaction_roll("contact")
        return []

    def _combat_over(self) -> bool:
        pcs_alive = [u for u in self.state.units.values() if u.side == "pc" and u.is_alive()]
        foes_fighting = [u for u in self.state.units.values() if u.side == "foe" and u.is_alive() and u.morale_state != "surrender"]
        return (len(pcs_alive) == 0) or (len(foes_fighting) == 0)

    def _post_combat_resolution(self) -> list[BattleEvent]:
        ev: list[BattleEvent] = []
        # Detection pass: hidden units that become visible are revealed.
        for uid in update_detection(self.game, self.state):
            ev.append(evt('DETECTED', unit_id=uid))
        # Reaction check on first LOS contact.
        ev.extend(self._check_first_contact_los())

        pcs = self._ordered_units([u for u in self.state.units.values() if u.side == "pc" and u.is_alive()])
        foes = self._ordered_units([u for u in self.state.units.values() if u.side == "foe"])

        # If combat ended due to friendly parley, do not award monster XP/loot.
        if getattr(self.state, "foe_reaction", "") == "friendly":
            ev.append(evt("COMBAT_ENDED", round_no=int(self.state.round_no), reason="parley"))
            self.state.phase = "COMBAT_END"
            self.node = "COMBAT_END"
            return ev
        # XP from defeated/surrendered foes
        xp_total = 0
        for fu in foes:
            if not fu.is_alive() or fu.morale_state == "surrender":
                mid = getattr(fu.actor, "monster_id", None)
                if mid and hasattr(self.game, "content"):
                    try:
                        xp_total += int(self.game.content.monsters.get(mid, {}).get("xp", 0) or 0)
                    except Exception:
                        pass
        if pcs and xp_total > 0:
            # Add to expedition XP pool; distributed on settlement.
            if hasattr(self.game, 'add_expedition_xp'):
                self.game.add_expedition_xp(int(xp_total), source='monsters')
            # Keep a battle event for trace/debug purposes.
            ev.append(evt('XP_EARNED', xp_total=int(xp_total)))

        # Deterministic loot from compiled treasure tables.
        defeated = [fu for fu in foes if (not fu.is_alive()) or fu.morale_state == "surrender"]
        if defeated and hasattr(self.game, "gold"):
            try:
                from .treasure_runtime import choose_hoard_table_for_encounter, roll_hoard, roll_xp_tradeout_hoard

                # Collect defeated monster metadata.
                max_cl = 1
                xp_total = 0
                formula_ids: list[str] = []
                for fu in defeated:
                    mid = getattr(fu.actor, "monster_id", None)
                    m = self.game.content.get_monster(mid) if (mid and hasattr(self.game, "content")) else None
                    if isinstance(m, dict):
                        try:
                            max_cl = max(max_cl, int(str(m.get("challenge_level", "1")).replace("+", "")))
                        except Exception:
                            pass
                        try:
                            xp_total += int(m.get("xp", 0) or 0)
                        except Exception:
                            pass
                        fid = str(m.get("treasure_formula_id") or "").strip()
                        if fid:
                            formula_ids.append(fid)

                table_id = choose_hoard_table_for_encounter(max_cl=max_cl)

                # XP-based hoard sizing (S&W-style): hoard_gp = total_xp * (1d3+1)
                # If monsters specify a treasure_formula_id we honor it (currently only xp_hoard_1d3p1).
                budget_gp = None
                mult = None
                if formula_ids:
                    fid = formula_ids[0]
                    if fid == "xp_hoard_1d3p1":
                        mult = int(self.game.dice_rng.randint(1, 3)) + 1
                        budget_gp = int(xp_total) * int(mult)

                # If the encounter uses XP-based hoards, apply Table 78 trade-outs.
                if budget_gp is not None:
                    instance_tag = f"dl{int(getattr(self.game,'dungeon_level',1) or 1)}_r{int(getattr(self.game,'current_room_id',0) or 0)}_t{int(getattr(self.game,'dungeon_turn',0) or 0)}"
                    loot = roll_xp_tradeout_hoard(game=self.game, budget_gp=int(budget_gp), instance_tag=instance_tag)
                    table_used = loot.table_id
                else:
                    loot = roll_hoard(game=self.game, table_id=table_id, budget_gp=None)
                    table_used = str(table_id)
                gp_total = int(loot.gp_total)

                # Materialized magic items (Table 78 trade-outs).
                try:
                    mi = list(getattr(loot, "magic_items", None) or [])
                except Exception:
                    mi = []

                take_loot = True
                try:
                    if hasattr(self.game, "confirm_take_loot"):
                        take_loot = bool(self.game.confirm_take_loot(gp=int(gp_total), items=list(mi or []), source="combat_loot"))
                except Exception:
                    take_loot = True

                if take_loot:
                    try:
                        if mi:
                            cur = list(getattr(self.game, "party_items", []) or [])
                            cur.extend(mi)
                            setattr(self.game, "party_items", cur)
                    except Exception:
                        pass
                    if gp_total > 0:
                        self.game.gold = int(getattr(self.game, "gold", 0) or 0) + gp_total
                else:
                    # Loot left behind: persist it into the current room so it can be collected later.
                    try:
                        if (bool(getattr(self.game, 'in_dungeon', False)) and (int(gp_total) > 0 or (mi and len(mi) > 0))):
                            room = None
                            try:
                                room = self.game._ensure_room(int(getattr(self.game, 'current_room_id', 0) or 0))
                            except Exception:
                                room = None
                            if isinstance(room, dict):
                                gl = room.setdefault('ground_loot', {'gp': 0, 'items': []})
                                try:
                                    gl['gp'] = int(gl.get('gp', 0) or 0) + int(gp_total)
                                except Exception:
                                    gl['gp'] = int(gp_total)
                                try:
                                    cur_items = list(gl.get('items') or [])
                                    cur_items.extend(list(mi or []))
                                    gl['items'] = cur_items
                                except Exception:
                                    gl['items'] = list(mi or [])
                                room['ground_loot_taken'] = False
                                try:
                                    self.game.emit('combat_loot_dropped', room_id=int(room.get('id', -1)), gp=int(gp_total), items=int(len(mi or [])))
                                except Exception:
                                    pass
                    except Exception:
                        pass

                    gp_total = 0
                    mi = []
                ev.append(
                    evt(
                        "LOOT_GAINED",
                        gp=int(gp_total),
                        foes=int(len(defeated)),
                        table_id=str(table_used),
                        xp_total=int(xp_total),
                        hoard_mult=int(mult) if (mult is not None) else 0,
                        hoard_budget_gp=int(budget_gp) if (budget_gp is not None) else 0,
                        gp_coins=int(loot.gp_from_coins),
                        gp_gems=int(loot.gp_from_gems),
                        gp_jewelry=int(loot.gp_from_jewelry),
                        gems=int(loot.gems_count),
                        jewelry=int(loot.jewelry_count),
                        magic=int(loot.magic_count),
                        magic_items=[str(x.get("name")) for x in (mi or [])],
                        tradeouts_100=int(getattr(loot, "tradeouts_100", 0) or 0),
                        tradeouts_1000=int(getattr(loot, "tradeouts_1000", 0) or 0),
                        tradeouts_5000=int(getattr(loot, "tradeouts_5000", 0) or 0),
                        major_treasure=bool(getattr(loot, "major_treasure", False)),
                    )
                )
            except Exception:
                # Fallback: no loot rather than breaking combat end.
                ev.append(evt("LOOT_GAINED", gp=0, foes=int(len(defeated)), table_id="NONE"))

        ev.append(evt("COMBAT_ENDED", round_no=int(self.state.round_no)))
        self.state.phase = "COMBAT_END"
        self.node = "COMBAT_END"
        return ev

    def _cmd_label(self, cmd: Command | None) -> str:
        if cmd is None:
            return "PASS"
        if isinstance(cmd, CmdMove):
            return "MOVE"
        if isinstance(cmd, CmdMoveThenMelee):
            return "MOVE+MELEE"
        if isinstance(cmd, CmdAttack):
            return "MISSILE" if cmd.mode == "missile" else "MELEE"
        if isinstance(cmd, CmdTakeCover):
            return "COVER"
        if isinstance(cmd, CmdCastSpell):
            return f"CAST:{cmd.spell_name}"
        if isinstance(cmd, CmdParley):
            return cmd.kind.upper()
        return type(cmd).__name__

    def _resolve_round(self) -> list[BattleEvent]:
        out: list[BattleEvent] = []
        for ph in ("SOCIAL", "MISSILES", "SPELLS", "MELEE"):
            self.state.phase = ph
            out.append(evt("PHASE_STARTED", phase=ph, round_no=int(self.state.round_no), side_first=str(self.state.side_first)))
            out.extend(self._resolve_phase(ph))
            self.state.remove_dead()
            # Social success may end combat without XP/loot.
            if getattr(self.state, "foe_reaction", "") == "friendly":
                out.extend(self._post_combat_resolution())
                break
        return out

    def _resolve_phase(self, phase: str) -> list[BattleEvent]:
        ev: list[BattleEvent] = []
        # Detection pass: hidden units that become visible are revealed.
        for uid in update_detection(self.game, self.state):
            ev.append(evt('DETECTED', unit_id=uid))
        # Reaction check on first LOS contact.
        ev.extend(self._check_first_contact_los())

        side_first = self.state.side_first
        side_second = "foe" if side_first == "pc" else "pc"

        def cmds_for_side(side: str) -> list[Command]:
            if self.state.is_surprised(side):
                return []
            ordered: list[Command] = []
            for uid in self.state.declare_order:
                u = self.state.units.get(uid)
                if not u or not u.is_alive() or u.side != side:
                    continue
                cmd = self.state.declared.get(uid)
                if not isinstance(cmd, Command):
                    continue
                if self._cmd_phase(cmd) != phase:
                    continue
                ordered.append(cmd)
            return ordered

        for side in (side_first, side_second):
            ordered = cmds_for_side(side)

            if phase != "MELEE":
                for cmd in ordered:
                    u = self.state.units.get(getattr(cmd, "unit_id", ""))
                    if not u or not u.is_alive():
                        continue
                    new_events = resolve_command(self.game, self.state, cmd)
                    ev.extend(new_events)
                    self._apply_morale_triggers(new_events, ev)
                    self.state.remove_dead()
                continue

            # MELEE sequencing: move first, then non-move, then MOVE+MELEE follow-ups.
            followups: list[tuple[str, str]] = []

            for cmd in ordered:
                u = self.state.units.get(getattr(cmd, "unit_id", ""))
                if not u or not u.is_alive():
                    continue
                if isinstance(cmd, CmdMove):
                    new_events = resolve_command(self.game, self.state, cmd)
                    ev.extend(new_events)
                    self._apply_morale_triggers(new_events, ev)
                    self.state.remove_dead()
                elif isinstance(cmd, CmdMoveThenMelee):
                    new_events = resolve_command(self.game, self.state, CmdMove(unit_id=cmd.unit_id, path=list(cmd.path)))
                    ev.extend(new_events)
                    self._apply_morale_triggers(new_events, ev)
                    self.state.remove_dead()
                    followups.append((cmd.unit_id, cmd.target_id))

            for cmd in ordered:
                u = self.state.units.get(getattr(cmd, "unit_id", ""))
                if not u or not u.is_alive():
                    continue
                if isinstance(cmd, CmdTakeCover):
                    new_events = resolve_command(self.game, self.state, cmd)
                    ev.extend(new_events)
                    self._apply_morale_triggers(new_events, ev)
                    self.state.remove_dead()
                elif isinstance(cmd, CmdDoorAction):
                    new_events = resolve_command(self.game, self.state, cmd)
                    ev.extend(new_events)
                    self._apply_morale_triggers(new_events, ev)
                    self.state.remove_dead()
                elif isinstance(cmd, CmdAttack) and cmd.mode == "melee":
                    new_events = resolve_command(self.game, self.state, cmd)
                    ev.extend(new_events)
                    self._apply_morale_triggers(new_events, ev)
                    self.state.remove_dead()

            living = self._living_map_visible_to(side)
            for attacker_id, target_id in followups:
                a = self.state.units.get(attacker_id)
                t = self.state.units.get(target_id)
                if not a or not t or not a.is_alive() or not t.is_alive():
                    continue
                if not grid_target_is_attackable(
                    gm=self.state.gm,
                    attacker_id=attacker_id,
                    attacker_pos=a.pos,
                    attacker_side=a.side,
                    target_id=target_id,
                    living=living,
                    mode="melee",
                ):
                    ev.append(evt("INFO", text=f"{attacker_id}: follow-up melee no longer valid."))
                    continue
                new_events = resolve_command(self.game, self.state, CmdAttack(unit_id=attacker_id, target_id=target_id, mode="melee"))
                ev.extend(new_events)
                self._apply_morale_triggers(new_events, ev)
                self.state.remove_dead()

        return ev

    def _cmd_phase(self, cmd: Command) -> str:
        if isinstance(cmd, CmdParley):
            return "SOCIAL"
        if isinstance(cmd, CmdAttack):
            return "MISSILES" if cmd.mode == "missile" else "MELEE"
        if isinstance(cmd, CmdCastSpell):
            return "SPELLS"
        if isinstance(cmd, CmdThrowItem):
            return "MISSILES"
        if isinstance(cmd, CmdDoorAction):
            return "MELEE"
        if isinstance(cmd, (CmdMove, CmdMoveThenMelee, CmdTakeCover)):
            return "MELEE"
        return "MELEE"

    # ---------- morale (P4.6.0) ----------
    def _apply_morale_triggers(self, new_events: list[BattleEvent], out: list[BattleEvent]) -> None:
        """Run foe morale checks on key triggers and apply fallback/rout."""
        # Only apply morale AI to foes for now (PC retainers can be added later).
        # Trigger 1: first casualty on foe side.
        dead_foes = [e for e in new_events if e.kind == "UNIT_DIED" and self._unit_side(str(e.data.get("unit_id",""))) == "foe"]
        if dead_foes and not self.state.morale_first_casualty_checked.get("foe", False):
            self.state.morale_first_casualty_checked["foe"] = True
            out.extend(self._morale_check_side("foe", reason="first_casualty"))

        # Trigger 2: half casualties (living <= initial/2)
        init = int((self.state.initial_counts or {}).get("foe", 0) or 0)
        if init > 0 and not self.state.morale_half_checked.get("foe", False):
            living_foes = [u for u in self.state.units.values() if u.side == "foe" and u.is_alive()]
            if len(living_foes) <= max(1, init // 2):
                self.state.morale_half_checked["foe"] = True
                out.extend(self._morale_check_side("foe", reason="half_down"))

    def _unit_side(self, unit_id: str) -> str | None:
        u = self.state.units.get(unit_id)
        return u.side if u else None

    def _morale_check_side(self, side: str, reason: str) -> list[BattleEvent]:
        ev: list[BattleEvent] = []
        # Detection pass: hidden units that become visible are revealed.
        for uid in update_detection(self.game, self.state):
            ev.append(evt('DETECTED', unit_id=uid))
        # Reaction check on first LOS contact.
        ev.extend(self._check_first_contact_los())

        living = self._ordered_units([u for u in self.state.units.values() if u.side == side and u.is_alive()])
        if not living:
            return ev
        # Fearless units (morale None) ignore checks.
        sample = next((u for u in living if getattr(u.actor, "morale", None) is not None), None)
        if sample is None:
            return ev

        mod = self._morale_mod(sample)
        # Roll 2d6 deterministically, compute margin of failure for fallback vs rout.
        r = int(self.game.dice_rng.randint(1, 6)) + int(self.game.dice_rng.randint(1, 6))
        target = int(getattr(sample.actor, "morale", 7) or 7) + int(mod)
        ev.append(evt("MORALE_CHECK", side=side, reason=reason, roll=r, target=target))

        if r <= target:
            ev.append(evt("MORALE_PASSED", side=side))
            return ev

        margin = r - target
        state = "fallback" if margin <= 2 else "rout"
        ev.append(evt("MORALE_FAILED", side=side, result=state, margin=margin))

        for u in living:
            if getattr(u.actor, "morale", None) is None:
                continue
            if u.morale_state in ("rout", "surrender"):
                continue
            u.morale_state = state
            if state == "fallback":
                ev.append(evt("UNIT_FALLBACK", unit_id=u.unit_id))
            else:
                ev.append(evt("UNIT_ROUTED", unit_id=u.unit_id))

        return ev

    def _morale_mod(self, u: UnitState) -> int:
        try:
            mid = getattr(u.actor, "monster_id", None)
            prof = (getattr(self.game.content, "monster_ai_profiles", {}) or {}).get(mid, {}) if mid else {}
            return int(prof.get("morale_mod", 0) or 0)
        except Exception:
            return 0

    # ---------- command builders ----------
    def _try_build_move(self, unit_id: str, dest: tuple[int, int]) -> CmdMove | None:
        u = self.state.units.get(unit_id)
        if not u or not u.is_alive() or u.move_remaining <= 0:
            return None
        st = getattr(u.actor, "status", {}) or {}
        if int(st.get("entangled", 0) or 0) > 0:
            return None
        self.state.rebuild_occupancy()
        blocked = set(self.state.occupancy.keys())
        blocked.discard(u.pos)
        path = dijkstra_shortest_path(self.state.gm, u.pos, dest, blocked)
        if not path:
            return None
        return CmdMove(unit_id=unit_id, path=path)

    def _try_build_attack(self, unit_id: str, target_id: str, action_id: str) -> CmdAttack | None:
        u = self.state.units.get(unit_id)
        t = self.state.units.get(target_id)
        if not u or not t or not u.is_alive() or not t.is_alive():
            return None
        if u.actions_remaining <= 0:
            return None
        living = self._living_map()
        if action_id == "MELEE":
            if not grid_target_is_attackable(
                gm=self.state.gm,
                attacker_id=u.unit_id,
                attacker_pos=u.pos,
                attacker_side=u.side,
                target_id=target_id,
                living=living,
                mode="melee",
            ):
                return None
            return CmdAttack(unit_id=unit_id, target_id=target_id, mode="melee")
        if action_id == "MISSILE":
            if not grid_target_is_attackable(
                gm=self.state.gm,
                attacker_id=u.unit_id,
                attacker_pos=u.pos,
                attacker_side=u.side,
                target_id=target_id,
                living=living,
                mode="missile",
            ):
                return None
            return CmdAttack(unit_id=unit_id, target_id=target_id, mode="missile")
        return None

    def _try_build_cast(self, unit_id: str, spell_name: str, target_pos: tuple[int, int]) -> CmdCastSpell | None:
        u = self.state.units.get(unit_id)
        if not u or not u.is_alive() or u.actions_remaining <= 0:
            return None
        tiles = spell_aoe_preview_tiles(self.state.gm, u.pos, spell_name, target_pos)
        if not tiles:
            return None
        from .spells_grid import template_for_spell
        tmpl = template_for_spell(spell_name)
        affected: list[str] = []
        self.state.rebuild_occupancy()
        if tmpl.kind == "unit":
            uid = self.state.occupancy.get(tuple(target_pos))
            if uid and uid != unit_id:
                affected = [uid]
        return CmdCastSpell(unit_id=unit_id, spell_name=spell_name, target_pos=target_pos, target_unit_ids=tuple(affected))
