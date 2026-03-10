from __future__ import annotations

from dataclasses import dataclass

from .grid_map import GridMap, has_line_of_sight, manhattan
from .grid_state import GridBattleState, UnitState
from .battle_events import BattleEvent, evt
from .commands import CmdAttack, CmdCastSpell, CmdDoorAction, CmdDungeonAction, CmdEndTurn, CmdHide, CmdMove, CmdTakeCover, CmdThrowItem, CmdParley, Command


@dataclass
class GridEntity:
    """A combatant on a grid.

    We keep a string id that is stable for the session (pc:0, foe:2).
    """

    entity_id: str
    actor: object  # PC or Monster (Actor)
    side: str  # "pc" | "foe"
    x: int
    y: int

    @property
    def pos(self) -> tuple[int, int]:
        return (int(self.x), int(self.y))


class GridBattle:
    """Minimal grid-based combat controller.

    This is a prototype layer: it delegates attacks/damage/effects to the
    existing engine (Game._resolve_attack) and only owns spatial rules.
    """

    def __init__(self, game, grid_map: GridMap):
        self.game = game
        self.map = grid_map
        self.entities: dict[str, GridEntity] = {}
        self.turn_order: list[str] = []  # entity_ids
        self.active_index: int = 0
        self.move_points_left: dict[str, int] = {}
        self.base_move_points: int = 6  # prototype default
        self.initiative_side: str = "pc"  # simple: PCs act, then foes

    def add_entity(self, entity: GridEntity) -> None:
        self.entities[entity.entity_id] = entity

    def setup_default_turn_order(self) -> None:
        pcs = [eid for eid, e in self.entities.items() if e.side == "pc" and getattr(e.actor, "hp", 0) > 0]
        foes = [eid for eid, e in self.entities.items() if e.side == "foe" and getattr(e.actor, "hp", 0) > 0]
        self.turn_order = pcs + foes
        self.active_index = 0
        for eid in self.turn_order:
            self.move_points_left[eid] = self.base_move_points

    def active_entity_id(self) -> str | None:
        if not self.turn_order:
            return None
        if self.active_index < 0 or self.active_index >= len(self.turn_order):
            return None
        return self.turn_order[self.active_index]

    def active_entity(self) -> GridEntity | None:
        eid = self.active_entity_id()
        return self.entities.get(eid) if eid else None

    def is_occupied(self, x: int, y: int) -> bool:
        for e in self.entities.values():
            if getattr(e.actor, "hp", 0) > 0 and (e.x, e.y) == (x, y):
                return True
        return False

    def can_move_to(self, eid: str, x: int, y: int) -> bool:
        if not self.map.in_bounds(x, y):
            return False
        if self.map.blocks_movement(x, y):
            return False
        if self.is_occupied(x, y):
            return False
        cur = self.entities.get(eid)
        if not cur:
            return False
        dist = manhattan(cur.pos, (x, y))
        return dist == 1 and self.move_points_left.get(eid, 0) > 0

    def move(self, eid: str, x: int, y: int) -> bool:
        if not self.can_move_to(eid, x, y):
            return False
        ent = self.entities[eid]
        ent.x, ent.y = x, y
        self.move_points_left[eid] = max(0, int(self.move_points_left.get(eid, 0)) - 1)
        # Moving breaks "take cover".
        st = getattr(ent.actor, "status", {}) or {}
        st.pop("cover", None)
        ent.actor.status = st
        return True

    def adjacent(self, a: GridEntity, b: GridEntity) -> bool:
        return manhattan(a.pos, b.pos) == 1

    def in_missile_range(self, a: GridEntity, b: GridEntity) -> bool:
        # Prototype: 12 tiles max (~60ft) if LOS.
        return manhattan(a.pos, b.pos) <= 12

    def cover_penalty_to_hit(self, target: GridEntity) -> int:
        # If actor used Take Cover we store cover in status as -2/-4.
        st = getattr(target.actor, "status", {}) or {}
        cov = int(st.get("cover", 0) or 0)
        return cov

    def take_cover(self, eid: str) -> bool:
        ent = self.entities.get(eid)
        if not ent:
            return False
        # Must be adjacent to a cover tile.
        x, y = ent.pos
        adjacent_cover = False
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if self.map.is_cover(x + dx, y + dy):
                adjacent_cover = True
                break
        if not adjacent_cover:
            return False
        st = getattr(ent.actor, "status", {}) or {}
        # Prototype: good cover if you take the action.
        st["cover"] = -4
        ent.actor.status = st
        return True

    def attack(self, attacker_id: str, target_id: str, mode: str) -> bool:
        a = self.entities.get(attacker_id)
        t = self.entities.get(target_id)
        if not a or not t:
            return False
        if getattr(a.actor, "hp", 0) <= 0 or getattr(t.actor, "hp", 0) <= 0:
            return False
        if a.side == t.side:
            return False

        kind = "melee" if mode == "melee" else "missile"
        if kind == "melee":
            if not self.adjacent(a, t):
                return False
        else:
            if not has_line_of_sight(self.map, a.pos, t.pos):
                return False
            if not self.in_missile_range(a, t):
                return False

        to_hit_mod = 0
        # Apply cover penalty to hit target.
        to_hit_mod += self.cover_penalty_to_hit(t)

        # Convert tile distance to feet and apply your existing range model.
        if kind == "missile":
            dist_ft = manhattan(a.pos, t.pos) * 5
            wname = getattr(a.actor, "weapon", None) or ""
            try:
                rng_mod, in_range = self.game._range_mod_for_weapon_at_distance(wname, dist_ft)
                if not in_range:
                    return False
                to_hit_mod += int(rng_mod)
            except Exception:
                pass

        # Delegate to engine for actual rolls/damage/specials.
        self.game._resolve_attack(a.actor, t.actor, to_hit_mod=to_hit_mod, round_no=1, kind_override=kind)
        return True

    def end_turn(self) -> None:
        eid = self.active_entity_id()
        if eid:
            self.move_points_left[eid] = self.base_move_points
        self.active_index = (self.active_index + 1) % max(1, len(self.turn_order))

    def remove_dead_from_order(self) -> None:
        self.turn_order = [eid for eid in self.turn_order if getattr(self.entities[eid].actor, "hp", 0) > 0]
        if self.active_index >= len(self.turn_order):
            self.active_index = 0

    def ai_step(self) -> None:
        """Very small AI: move towards nearest PC; attack if adjacent."""
        eid = self.active_entity_id()
        if not eid:
            return
        ent = self.entities.get(eid)
        if not ent or ent.side != "foe":
            return
        if getattr(ent.actor, "hp", 0) <= 0:
            self.end_turn()
            return

        pcs = [e for e in self.entities.values() if e.side == "pc" and getattr(e.actor, "hp", 0) > 0]
        if not pcs:
            return
        pcs.sort(key=lambda p: manhattan(ent.pos, p.pos))
        tgt = pcs[0]
        # Attack if adjacent.
        if self.adjacent(ent, tgt):
            self.attack(ent.entity_id, tgt.entity_id, "melee")
            self.remove_dead_from_order()
            self.end_turn()
            return
        # Move one step towards.
        x, y = ent.pos
        tx, ty = tgt.pos
        candidates = []
        if tx > x:
            candidates.append((x + 1, y))
        if tx < x:
            candidates.append((x - 1, y))
        if ty > y:
            candidates.append((x, y + 1))
        if ty < y:
            candidates.append((x, y - 1))
        # Choose first legal.
        for nx, ny in candidates:
            if self.can_move_to(eid, nx, ny):
                self.move(eid, nx, ny)
                break
        self.end_turn()


# ---------------------------
# P4.4 command resolution API
# ---------------------------


def resolve_command(game, state: GridBattleState, cmd: Command) -> list[BattleEvent]:
    """Apply a validated command to the GridBattleState and call into the engine.

    Returns a list of typed BattleEvents.
    """

    events: list[BattleEvent] = []

    # --- P4.7.1: door contact + surprise on door open ---
    def _maybe_door_contact(opener: UnitState, door_pos: tuple[int,int], listened_bonus: bool) -> None:
        """Trigger a surprise roll when a door opening newly reveals foes.

        listened_bonus: True if PCs successfully listened at this door recently.
        """
        dx = dy = 0  # unused for now (kept for future 'directional contact')
        x, y = door_pos
        # If no foes alive, nothing to do.
        foes = [foe for foe in state.units.values() if foe.side == "foe" and foe.is_alive() and foe.morale_state != "surrender"]
        if not foes:
            return

        # Determine whether the open door newly creates LOS to at least one foe.
        # We approximate by comparing LOS with the door treated as closed vs open.
        original_tile = state.gm.get(x, y)

        # LOS with closed door (treat any door as closed blocker).
        state.gm.set(x, y, "door_closed")
        newly_revealed: list[str] = []
        for foe in foes:
            before = has_line_of_sight(state.gm, opener.pos, foe.pos)
            newly_revealed.append((foe.unit_id, before))
        # Restore to open (caller already set to open, but be safe).
        state.gm.set(x, y, "door_open")
        revealed_any = False
        for foe_id, before in newly_revealed:
            foe = state.units.get(foe_id)
            if not foe or not foe.is_alive():
                continue
            after = has_line_of_sight(state.gm, opener.pos, foe.pos)
            if after and not before and manhattan(opener.pos, foe.pos) <= 12:
                revealed_any = True
                break

        # Restore tile to the actual current door tile if needed.
        # (In practice it's door_open here.)
        if state.gm.get(x, y) != "door_open":
            state.gm.set(x, y, original_tile)

        if not revealed_any:
            return

        events.append(evt("DOOR_CONTACT", unit=opener.unit_id, x=int(x), y=int(y), listened_bonus=bool(listened_bonus)))

        # Surprise roll: 1-2 surprised normally. If listened_bonus, PCs cannot be surprised and foes are easier to surprise (1-3).
        pc_roll = int(game.dice_rng.randint(1, 6))
        foe_roll = int(game.dice_rng.randint(1, 6))
        pc_surprised = (pc_roll <= 2)
        foe_threshold = 3 if listened_bonus else 2
        foe_surprised = (foe_roll <= foe_threshold)

        if listened_bonus:
            pc_surprised = False  # PCs are ready.

        result = "none"
        if pc_surprised and not foe_surprised:
            state.set_surprise("pc", 1)
            result = "pc surprised"
        elif foe_surprised and not pc_surprised:
            state.set_surprise("foe", 1)
            result = "foe surprised"
        elif pc_surprised and foe_surprised:
            result = "both surprised"
        events.append(evt("SURPRISE_ROLL", pc_roll=pc_roll, foe_roll=foe_roll, result=result, context="door"))
    u = state.units.get(getattr(cmd, "unit_id", ""))
    if not u or not u.is_alive():
        return events

    # --- P4.8.1: Parley / Bribe / Threaten (SOCIAL phase) ---
    if isinstance(cmd, CmdParley):
        # Only meaningful if foes are neutral; controller should enforce.
        if getattr(state, "foe_reaction", "unseen") != "neutral":
            events.append(evt("INFO", text="No parley possible: foes are not neutral."))
            return events

        kind = str(cmd.kind)

        # Determine encounter alignment from first living, non-surrendered foe.
        foe_alignment = "neutral"
        for fu in state.units.values():
            if fu.side != "foe" or not fu.is_alive() or getattr(fu, "morale_state", "steady") == "surrender":
                continue
            al = str(getattr(getattr(fu, "actor", None), "alignment", "") or "").strip().lower()
            if al in ("law", "lawful"):
                foe_alignment = "law"
            elif al in ("chaos", "chaotic"):
                foe_alignment = "chaos"
            else:
                foe_alignment = "neutral"
            break

        # Alignment overrides:
        # - Chaos: always hostile (no roll)
        # - Law: always friendly (no roll)
        # - Neutral: use the 2d6 reaction bands
        if foe_alignment == "chaos":
            state.foe_reaction = "hostile"
            events.append(evt("SOCIAL_AUTO_HOSTILE", kind=kind, alignment="chaos"))
            events.append(evt("REACTION_BROKEN", reason=kind))
            return events

        if foe_alignment == "law":
            state.foe_reaction = "friendly"
            events.append(evt("SOCIAL_AUTO_FRIENDLY", kind=kind, alignment="law"))
            return events

        # --- Neutrality: roll 2d6 + bonuses ---
        # Charisma modifier (PCs only).
        cha_mod = 0
        try:
            st = getattr(u.actor, "stats", None)
            if st is not None:
                from .models import mod_ability
                cha_mod = int(mod_ability(int(getattr(st, "CHA", 10) or 10)))
        except Exception:
            cha_mod = 0

        bonus = int(cha_mod)
        spent_gp = 0

        if kind == "bribe":
            # Flat 50 gp bribe to keep UI minimal.
            amt = 50
            if not hasattr(game, "gold") or int(getattr(game, "gold", 0) or 0) < amt:
                events.append(evt("INFO", text="Not enough gold to bribe (need 50 gp)."))
                return events
            game.gold = int(getattr(game, "gold", 0) or 0) - amt
            spent_gp = amt
            bonus += 1  # +1 for a meaningful bribe

        if kind == "threaten":
            # +1 if PCs currently outnumber visible foes.
            pcs = [uu for uu in state.units.values() if uu.side == "pc" and uu.is_alive()]
            foes = [uu for uu in state.units.values() if uu.side == "foe" and uu.is_alive() and uu.morale_state != "surrender"]
            if len(pcs) > len(foes):
                bonus += 1

        roll = int(game.dice_rng.randint(1, 6)) + int(game.dice_rng.randint(1, 6))
        total = roll + bonus

        # total >= 10: friendly
        # total 7-9: remain neutral
        # total <= 6: hostile
        if total >= 10:
            state.foe_reaction = "friendly"
            result = "friendly"
        elif total >= 7:
            state.foe_reaction = "neutral"
            result = "neutral"
        else:
            state.foe_reaction = "hostile"
            result = "hostile"

        events.append(evt("SOCIAL_ROLL", unit=u.unit_id, kind=kind, roll=roll, bonus=bonus, total=total, result=result, spent_gp=spent_gp, alignment="neutral"))
        if result == "hostile":
            events.append(evt("REACTION_BROKEN", reason=kind))
        return events

    if isinstance(cmd, CmdMove):
        # Consume as much of the path as fits the remaining movement (weighted costs).
        steps = list(cmd.path)
        if not steps:
            return events
        state.rebuild_occupancy()
        blocked = set(state.occupancy.keys())
        blocked.discard(u.pos)

        moved_points = 0
        moved_tiles = 0

        def adj_enemies(pos: tuple[int, int]) -> list[UnitState]:
            outl: list[UnitState] = []
            for ou in state.units.values():
                if not ou.is_alive() or ou.side == u.side:
                    continue
                if manhattan(pos, ou.pos) == 1:
                    outl.append(ou)
            # stable order
            outl.sort(key=lambda uu: uu.unit_id)
            return outl

        for (nx, ny) in steps:
            if u.move_remaining <= 0:
                break
            nx, ny = int(nx), int(ny)
            if not state.gm.in_bounds(nx, ny) or state.gm.blocks_movement(nx, ny):
                break
            if (nx, ny) in blocked:
                break
            if manhattan(u.pos, (nx, ny)) != 1:
                break

            step_cost = int(state.gm.move_cost(nx, ny))
            if step_cost > int(u.move_remaining):
                break

            old_pos = u.pos
            old_adj = {e.unit_id for e in adj_enemies(old_pos)}

            # Apply movement.
            state.occupancy.pop(u.pos, None)
            u.x, u.y = nx, ny
            state.occupancy[u.pos] = u.unit_id
            u.move_remaining = max(0, int(u.move_remaining) - step_cost)
            moved_points += step_cost
            moved_tiles += 1
            # Moving breaks "take cover".
            st = getattr(u.actor, "status", {}) or {}
            st.pop("cover", None)
            u.actor.status = st

            # Opportunity attacks: enemies that were adjacent to old_pos but are no longer adjacent now.
            new_adj = {e.unit_id for e in adj_enemies(u.pos)}
            leavers = sorted(list(old_adj - new_adj))
            for attacker_id in leavers:
                atk = state.units.get(attacker_id)
                if not atk or not atk.is_alive():
                    continue
                # Once per round per attacker.
                last = int(state.opp_used_round.get(attacker_id, 0) or 0)
                if last == int(state.round_no):
                    continue
                state.opp_used_round[attacker_id] = int(state.round_no)
                events.append(evt("OPPORTUNITY_ATTACK_TRIGGERED", attacker_id=attacker_id, target_id=u.unit_id))
                # Resolve a free melee attack.
                t_hp_before = int(getattr(u.actor, "hp", 0) or 0)
                try:
                    game._resolve_attack(atk.actor, u.actor, to_hit_mod=0, round_no=state.round_no, kind_override="melee")
                    t_hp_after = int(getattr(u.actor, "hp", 0) or 0)
                    if t_hp_after < t_hp_before:
                        events.append(evt("DAMAGE", target_id=u.unit_id, amount=int(t_hp_before - t_hp_after)))
                    if t_hp_after <= 0:
                        events.append(evt("UNIT_DIED", unit_id=u.unit_id))
                        state.remove_dead_from_initiative()
                        return events
                except Exception:
                    # Keep deterministic flow: ignore engine errors but log.
                    events.append(evt("INFO", text="Opportunity attack failed to resolve."))

        if moved_tiles:
            events.append(evt("MOVED", unit_id=u.unit_id, tiles=int(moved_tiles), points=int(moved_points), to=u.pos))
        return events

    if isinstance(cmd, CmdTakeCover):
        # Must be adjacent to a cover tile.
        x, y = u.pos
        ok = False
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if state.gm.is_cover(x + dx, y + dy):
                ok = True
                break
        if not ok:
            return events
        st = getattr(u.actor, "status", {}) or {}
        st["cover"] = -4
        u.actor.status = st
        u.actions_remaining = max(0, int(u.actions_remaining) - 1)
        events.append(evt("TAKE_COVER", unit_id=u.unit_id))
        return events

    if isinstance(cmd, CmdHide):
        # Attempt to hide/move silently (simple OSR-facing mechanic).
        # Conditions: not currently visible to any enemy (LOS + enemy light), OR adjacent cover, OR in darkness.
        # Resolution: d6 success 1-2 normally; thief 1-4; +1 pip if in darkness (capped at 5).
        # Hidden units are not targetable until detected.
        from .stealth import can_attempt_hide, hide_roll_success

        if u.actions_remaining <= 0:
            return events
        ok, reason, darkness_bonus = can_attempt_hide(game, state, u)
        if not ok:
            events.append(evt("HIDE_FAILED", unit_id=u.unit_id, reason=reason))
            u.actions_remaining = max(0, int(u.actions_remaining) - 1)
            return events

        roll = int(game.dice_rng.randint(1, 6))
        success = hide_roll_success(u, roll, darkness_bonus=darkness_bonus)
        if success:
            u.hidden = True
            events.append(evt("HIDDEN", unit_id=u.unit_id, roll=roll))
        else:
            events.append(evt("HIDE_FAILED", unit_id=u.unit_id, roll=roll))
        u.actions_remaining = max(0, int(u.actions_remaining) - 1)
        return events


    if isinstance(cmd, CmdDoorAction):
        x, y = cmd.door_pos
        if manhattan(u.pos, (x, y)) != 1:
            return events
        t = state.gm.get(x, y)
        if cmd.kind == "open":
            if t in ("door_closed",):
                state.gm.set(x, y, "door_open")
                events.append(evt("DOOR_OPENED", unit=u.unit_id, x=x, y=y))
                listened_bonus = (u.side == "pc" and int(state.listened_doors.get((x,y), -9999)) == int(state.dungeon_turn))
                state.listened_doors.pop((x,y), None)
                _maybe_door_contact(u, (x,y), listened_bonus)
            elif t == "door_spiked":
                events.append(evt("INFO", msg="Door is spiked shut."))
            else:
                events.append(evt("INFO", msg="No closed door to open."))
        elif cmd.kind == "force":
            if t in ("door_closed", "door_locked", "door_stuck", "door_spiked"):
                # STR check: d20 + STR mod vs DC (closed 8, locked/stuck 12, spiked 14 + strength)
                if t == "door_closed":
                    dc = 8
                elif t == "door_spiked":
                    dc = 14 + int(state.door_spike_strength.get((x,y), 2) or 2)
                else:
                    dc = 12
                str_score = int(getattr(getattr(u.actor, "stats", None), "STR", 10) or 10)
                # fallback if no stats: 10
                from .models import mod_ability
                roll = int(game.dice_rng.randint(1, 20))
                total = roll + int(mod_ability(str_score))
                events.append(evt("DOOR_FORCE_ATTEMPT", unit=u.unit_id, x=x, y=y, roll=roll, total=total, dc=dc))
                if total >= dc:
                    was_spiked = (t == "door_spiked")
                    state.gm.set(x, y, "door_open")
                    state.door_spike_strength.pop((x,y), None)
                    events.append(evt("DOOR_FORCED_OPEN", unit=u.unit_id, x=x, y=y))
                    listened_bonus = (u.side == "pc" and int(state.listened_doors.get((x,y), -9999)) == int(state.dungeon_turn))
                    state.listened_doors.pop((x,y), None)
                    _maybe_door_contact(u, (x,y), listened_bonus)
                    if was_spiked:
                        events.append(evt("DOOR_BASHED", unit=u.unit_id, x=x, y=y))
                else:
                    events.append(evt("INFO", msg="Door holds."))
            else:
                events.append(evt("INFO", msg="No door to force."))
        elif cmd.kind == "spike":
            if t in ("door_closed", "door_locked", "door_stuck"):
                
                state.gm.set(x, y, "door_spiked")
                state.door_spike_strength[(x, y)] = max(2, int(state.door_spike_strength.get((x,y), 0) or 0))
                events.append(evt("DOOR_SPIKED", unit=u.unit_id, x=x, y=y, strength=int(state.door_spike_strength[(x,y)])))
            else:
                events.append(evt("INFO", msg="No door to spike."))
        return events


    if isinstance(cmd, CmdDungeonAction):
        tx, ty = cmd.target_pos
        kind = cmd.kind
        if kind == "listen":
            # Must be adjacent to a closed/locked/stuck/spiked door.
            ok = False
            for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                ax, ay = u.x + dx, u.y + dy
                if (ax, ay) == (tx, ty) and state.gm.get(ax, ay) in ("door_closed","door_locked","door_stuck","door_spiked"):
                    ok = True
                    break
            if not ok:
                events.append(evt("LISTEN", unit=u.unit_id, result="Not adjacent to a closed door."))
                return events
            # OSR listen: 1-2 on d6; thieves 1-4 on d6.
            roll = int(game.dice_rng.randint(1,6))
            cls = str(getattr(u.actor, "cls", getattr(u.actor, "char_class", "")) or "").lower()
            need = 4 if "thief" in cls else 2
            heard = roll <= need
            # Simple signal: if any foe is within 6 tiles beyond the door line, we say "noises".
            noises = False
            for foe in state.units.values():
                if foe.side == "foe" and foe.is_alive():
                    if manhattan((foe.x, foe.y), (tx, ty)) <= 6:
                        noises = True
                        break
            if heard and noises:
                res = f"Heard noises beyond the door (d6={roll})."
            elif heard and not noises:
                res = f"You listen carefully but hear nothing (d6={roll})."
            else:
                res = f"You hear nothing useful (d6={roll})."
            events.append(evt("LISTEN", unit=u.unit_id, x=tx, y=ty, roll=roll, result=res))
            return events

        if kind == "search":
            # Must be adjacent to a wall/secret wall or door frame; searching can reveal a secret door.
            ok = False
            t = state.gm.get(tx, ty)
            for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                if (u.x + dx, u.y + dy) == (tx, ty):
                    ok = True
                    break
            if not ok:
                events.append(evt("SEARCH", unit=u.unit_id, result="Not adjacent to the searched tile."))
                return events
            roll = int(game.dice_rng.randint(1,6))
            # S&W: humans 1-in-6; elves/half-elves 4-in-6 when searching.
            race = str(getattr(u.actor, "race", "") or "").lower()
            need = 4 if "elf" in race else 1
            found = (roll <= need) and (t == "wall_secret")
            if found:
                state.gm.set(tx, ty, "door_closed")
                events.append(evt("SEARCH", unit=u.unit_id, x=tx, y=ty, roll=roll, result="Secret door found!"))
            else:
                events.append(evt("SEARCH", unit=u.unit_id, x=tx, y=ty, roll=roll, result="Nothing found."))
            return events

        events.append(evt("INFO", msg=f"Unknown dungeon action: {kind}"))
        return events

    if isinstance(cmd, CmdThrowItem):
        state.rebuild_occupancy()
        tx, ty = cmd.target_pos
        # simple thrown range: 6 tiles
        if manhattan(u.pos, (tx, ty)) > 6:
            events.append(evt("INFO", msg="Target out of throwing range."))
            return events
        if not has_line_of_sight(state.gm, u.pos, (tx, ty)):
            events.append(evt("INFO", msg="No line of sight."))
            return events

        # Consume party inventory counts (shared pools in Game).
        attr = {
            "oil": "oil_flasks",
            "holy_water": "holy_water",
            "net": "nets",
            "caltrops": "caltrops",
        }.get(cmd.item_id)
        if not attr or getattr(game, attr, 0) <= 0:
            events.append(evt("INFO", msg="Out of that item."))
            return events
        setattr(game, attr, int(getattr(game, attr)) - 1)
        events.append(evt("ITEM_THROWN", unit=u.unit_id, item=cmd.item_id, x=tx, y=ty))

        # Apply effects.
        affected_units: list[UnitState] = []
        if cmd.item_id in ("oil", "holy_water", "net"):
            # burst radius 1 around target tile
            tiles = [(tx, ty)] + [(tx+1, ty), (tx-1, ty), (tx, ty+1), (tx, ty-1)]
            for (x, y) in tiles:
                uid = state.occupancy.get((x, y))
                if not uid:
                    continue
                tu = state.units.get(uid)
                if tu and tu.is_alive() and uid != u.unit_id:
                    affected_units.append(tu)
            # stable order
            affected_units.sort(key=lambda uu: uu.unit_id)

        if cmd.item_id == "oil":
            # Immediate 1d3 damage and 2 rounds of burning (1 dmg/round) via status marker.
            for tu in affected_units:
                dmg = int(game.dice_rng.randint(1, 3))
                before = int(getattr(tu.actor, "hp", 0))
                tu.actor.hp = before - dmg
                events.append(evt("DAMAGE", src=u.unit_id, tgt=tu.unit_id, amount=dmg, kind="fire"))
                # mark burning
                tu.actor.status.setdefault("burning", 0)
                tu.actor.status["burning"] = max(int(tu.actor.status["burning"]), 2)
                events.append(evt("EFFECT_APPLIED", tgt=tu.unit_id, kind="burning", rounds=2))
        elif cmd.item_id == "holy_water":
            for tu in affected_units:
                tags = set(getattr(tu.actor, "tags", []) or [])
                if "undead" in tags:
                    dmg = sum(int(game.dice_rng.randint(1, 6)) for _ in range(2))
                else:
                    dmg = 1
                before = int(getattr(tu.actor, "hp", 0))
                tu.actor.hp = before - dmg
                events.append(evt("DAMAGE", src=u.unit_id, tgt=tu.unit_id, amount=dmg, kind="holy"))
        elif cmd.item_id == "net":
            for tu in affected_units:
                # Simple: set 'entangled' rounds=2 (movement halved / no move) handled by controller.
                tu.actor.status.setdefault("entangled", 0)
                tu.actor.status["entangled"] = max(int(tu.actor.status["entangled"]), 2)
                events.append(evt("EFFECT_APPLIED", tgt=tu.unit_id, kind="entangled", rounds=2))
        elif cmd.item_id == "caltrops":
            # Create rubble on target tile (difficult terrain). If unit on tile, deal 1 dmg.
            if state.gm.get(tx, ty) == "floor":
                state.gm.set(tx, ty, "rubble")
                events.append(evt("TERRAIN_CHANGED", x=tx, y=ty, tile="rubble"))
            uid = state.occupancy.get((tx, ty))
            if uid and uid != u.unit_id:
                tu = state.units.get(uid)
                if tu and tu.is_alive():
                    before = int(getattr(tu.actor, "hp", 0))
                    tu.actor.hp = before - 1
                    events.append(evt("DAMAGE", src=u.unit_id, tgt=tu.unit_id, amount=1, kind="piercing"))
        # death checks
        for tu in affected_units:
            if int(getattr(tu.actor, "hp", 0)) <= 0:
                events.append(evt("UNIT_DIED", unit=tu.unit_id))
        return events


    if isinstance(cmd, CmdAttack):
        t = state.units.get(cmd.target_id)
        if not t or not t.is_alive() or t.side == u.side:
            return events

        # If foes were non-hostile due to reaction, attacking breaks parley.
        if u.side == "pc" and t.side == "foe" and getattr(state, "foe_reaction", "hostile") != "hostile":
            state.foe_reaction = "hostile"
            events.append(evt("REACTION_BROKEN", reason="attacked"))

        t_hp_before = int(getattr(t.actor, "hp", 0) or 0)

        kind = "melee" if cmd.mode == "melee" else "missile"
        if kind == "melee":
            if manhattan(u.pos, t.pos) != 1:
                return events
        else:
            if not has_line_of_sight(state.gm, u.pos, t.pos):
                return events
            if manhattan(u.pos, t.pos) > 12:
                return events

        to_hit_mod = 0
        # Apply cover penalty to hit target.
        stt = getattr(t.actor, "status", {}) or {}
        to_hit_mod += int(stt.get("cover", 0) or 0)

        if kind == "missile":
            dist_ft = manhattan(u.pos, t.pos) * 5
            wname = getattr(u.actor, "weapon", None) or ""
            try:
                rng_mod, in_range = game._range_mod_for_weapon_at_distance(wname, dist_ft)
                if not in_range:
                    return events
                to_hit_mod += int(rng_mod)
            except Exception:
                pass

        # Ambush bonus: if attacker is hidden at time of attack, grant +4 to-hit and reveal.
        if getattr(u, "hidden", False):
            to_hit_mod += 4
            events.append(evt("AMBUSH", attacker_id=u.unit_id, target_id=t.unit_id, bonus=4))
            u.hidden = False

        # Delegate to engine.
        game._resolve_attack(u.actor, t.actor, to_hit_mod=to_hit_mod, round_no=state.round_no, kind_override=kind)
        u.actions_remaining = max(0, int(u.actions_remaining) - 1)
        events.append(evt("ATTACK", attacker_id=u.unit_id, target_id=t.unit_id, mode=kind))

        t_hp_after = int(getattr(t.actor, "hp", 0) or 0)
        if t_hp_after < t_hp_before:
            events.append(evt("DAMAGE", target_id=t.unit_id, amount=int(t_hp_before - t_hp_after)))
        if t_hp_after <= 0:
            events.append(evt("UNIT_DIED", unit_id=t.unit_id))

        # Clean initiative if target died.
        state.remove_dead_from_initiative()
        return events


    if isinstance(cmd, CmdCastSpell):
        spell_ident = str(cmd.spell_name)

        # Resolve display name for templates + spell logic.
        sp = None
        try:
            sp = game.content.get_spell(spell_ident)
        except Exception:
            sp = None
        spell_name = getattr(sp, "name", spell_ident)

        # Must be prepared to cast.
        prepared = list(getattr(u.actor, "spells_prepared", []) or [])
        if spell_ident not in prepared and spell_name not in prepared:
            return events

        # Casting at foes breaks non-hostile reaction stance.
        if u.side == "pc" and getattr(state, "foe_reaction", "hostile") != "hostile":
            # if any target unit ids include foes, break.
            if any((state.units.get(tid) and state.units[tid].side == "foe") for tid in (cmd.target_unit_ids or ())):
                state.foe_reaction = "hostile"
                events.append(evt("REACTION_BROKEN", reason="spell"))

        # If spell was disrupted before the SPELLS phase, it is lost.
        st_now = getattr(u.actor, "status", {}) or {}
        casting = st_now.get("casting")
        if isinstance(casting, dict) and int(casting.get("round", 0) or 0) == int(state.round_no) and bool(casting.get("disrupted", False)):
            events.append(evt("SPELL_DISRUPTED", caster_id=u.unit_id, spell=str(spell_name)))
            # Consume the prepared spell even if disrupted (OSR timing).
            try:
                u.actor.spells_prepared.remove(spell_ident)
            except Exception:
                try:
                    u.actor.spells_prepared.remove(spell_name)
                except Exception:
                    pass
            st_now.pop("casting", None)
            u.actor.status = st_now
            u.actions_remaining = max(0, int(u.actions_remaining) - 1)
            return events

        from .spells_grid import template_for_spell, can_target, aoe_tiles

        tmpl = template_for_spell(spell_name)
        if not can_target(state.gm, u.pos, cmd.target_pos, tmpl):
            return events

        # Determine affected units.
        # OSR-default: burst/line effects hit *everyone* in the area (including allies),
        # excluding the caster unless explicitly targeted.
        tiles = aoe_tiles(state.gm, u.pos, cmd.target_pos, tmpl)
        affected_units: list[UnitState] = []
        for pos in sorted(list(tiles)):
            tid = state.occupancy.get(pos)
            if not tid:
                continue
            tu = state.units.get(tid)
            if not tu or not tu.is_alive():
                continue
            # Exclude caster from incidental AoE.
            if tu.unit_id == u.unit_id and tmpl.kind in ("burst", "line"):
                continue
            affected_units.append(tu)

        events.append(evt("SPELL_CAST", caster_id=u.unit_id, spell=str(spell_name), target=cmd.target_pos, template=tmpl.kind))
        if tiles:
            events.append(evt("AOE_TILES", spell=spell_name, count=len(tiles)))

        # Snapshot HP for damage events.
        hp_before: dict[str, int] = {tu.unit_id: int(getattr(tu.actor, "hp", 0) or 0) for tu in affected_units}

        # Route spell resolution through the existing engine spell system.
        # For single-target spells, supply a forced target to avoid UI prompts.
        from .spellcasting import apply_spell_in_combat

        # Capture engine UI logs as typed INFO events for the tactical log.
        old_log = getattr(getattr(game, "ui", None), "log", None)
        def _grid_log(msg: str):
            try:
                if callable(old_log):
                    old_log(msg)
            except Exception:
                pass
            try:
                events.append(evt("INFO", text=str(msg)))
            except Exception:
                pass

        forced_foe = None
        forced_ally = None
        if tmpl.kind == "unit":
            # Use declared explicit target if present (declare/resolve timing).
            tgt = None
            if getattr(cmd, "target_unit_ids", ()) and len(getattr(cmd, "target_unit_ids", ())) > 0:
                tid = list(getattr(cmd, "target_unit_ids", ()))[0]
                tu = state.units.get(str(tid))
                if tu and tu.is_alive():
                    tgt = tu
            elif affected_units:
                tgt = affected_units[0]
            if tgt is not None:
                if tgt.side == u.side:
                    forced_ally = tgt.actor
                else:
                    forced_foe = tgt.actor

        # For AoE spells, pass the affected actors as the "foes" list.
        # The underlying spell logic applies saves/MR/immunities/damage packets.
        try:
            if getattr(game, "ui", None) is not None:
                game.ui.log = _grid_log  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            ok = bool(
                apply_spell_in_combat(
                    game=game,
                    caster=u.actor,
                    spell_name=str(spell_name),
                    foes=[tu.actor for tu in affected_units],
                    forced_foe=forced_foe,
                    forced_ally=forced_ally,
                )
            )
        finally:
            try:
                if getattr(game, "ui", None) is not None and old_log is not None:
                    game.ui.log = old_log  # type: ignore[attr-defined]
            except Exception:
                pass

        if not ok:
            events.append(evt("INFO", text=f"{spell_name} fizzles (not usable right now)."))
            return events

                # Clear casting marker (spell is now resolved).
        try:
            st2 = getattr(u.actor, "status", {}) or {}
            st2.pop("casting", None)
            u.actor.status = st2
        except Exception:
            pass

        # Consume only after successfully applying.
        try:
            u.actor.spells_prepared.remove(spell_ident)
        except Exception:
            try:
                u.actor.spells_prepared.remove(spell_name)
            except Exception:
                pass

        # Emit damage/death deltas.
        for tu in affected_units:
            before = int(hp_before.get(tu.unit_id, 0))
            after = int(getattr(tu.actor, "hp", 0) or 0)
            if after < before:
                events.append(evt("DAMAGE", target_id=tu.unit_id, amount=int(before - after)))
            if after <= 0:
                events.append(evt("UNIT_DIED", unit_id=tu.unit_id))

        u.actions_remaining = max(0, int(u.actions_remaining) - 1)
        state.remove_dead_from_initiative()
        return events

    if isinstance(cmd, CmdEndTurn):
        events.append(evt("TURN_ENDED", unit_id=u.unit_id))
        state.advance_turn()
        a = state.active_unit_id()
        if a:
            events.append(evt("TURN_STARTED", unit_id=a))
        return events

    return events
