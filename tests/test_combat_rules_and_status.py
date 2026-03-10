import sww.grid_battle as gbmod
import sww.spellcasting as spmod
import sww.turn_controller as tcmod
import sww.grid_state as gsmod
from sww.models import Actor
from sww.combat_rules import is_melee_engaged, can_use_missile, shooting_into_melee_penalty, foe_frontage_limit, apply_forced_retreat, opportunity_attackers_on_leave
from sww.status_lifecycle import apply_status, tick_round_statuses, cleanup_actor_battle_status, action_block_reason, leave_combat
from sww.ai_capabilities import detect_capabilities, choose_attack_mode, morale_behavior
from sww.combat_legality import theater_target_is_valid, grid_target_is_attackable, grid_pair_is_attack_legal
from sww.grid_map import GridMap
from sww.grid_battle import GridBattle, GridEntity
from sww.models import Party
from sww.validation import validate_state
from sww.grid_state import GridBattleState, UnitState


class DummyGame:
    def _weapon_kind(self, actor):
        return getattr(actor, "weapon_kind", "melee")


def _actor(*, is_pc=False):
    return Actor(name="A", hp=5, hp_max=5, ac_desc=9, hd=1, save=14, is_pc=is_pc)


def test_combat_rules_core_thresholds():
    assert is_melee_engaged(10)
    assert not is_melee_engaged(15)
    assert can_use_missile(20)
    assert not can_use_missile(10)
    assert shooting_into_melee_penalty(0) == -4
    assert shooting_into_melee_penalty(10) == 0
    assert foe_frontage_limit(10, 0) == 1
    assert foe_frontage_limit(30, 3) is None


def test_forced_retreat_transition():
    foe = _actor(is_pc=False)
    state = apply_forced_retreat(foe)
    assert state == "flee"
    assert "fled" in foe.effects
    pc = _actor(is_pc=True)
    assert apply_forced_retreat(pc) == "cower"
    assert "fled" not in pc.effects


def test_status_lifecycle_tick_block_and_cleanup():
    a = _actor()
    apply_status(a, "asleep", 2, mode="max")
    apply_status(a, "parry", {"rounds": 1, "penalty": 2})
    apply_status(a, "casting", {"rounds": 1})
    tick_round_statuses([a], phase="start")
    assert a.status.get("asleep") == 1
    assert "parry" not in a.status
    assert "casting" in a.status
    assert action_block_reason(a) == "asleep"
    apply_status(a, "silence", 2, mode="max")
    assert action_block_reason(a, for_casting=True) in {"asleep", "silence"}
    cleanup_actor_battle_status(a)
    assert "casting" not in a.status
    assert "parry" not in a.status


def test_ai_capability_seam_includes_specials_and_morale_behavior():
    g = DummyGame()
    a = _actor()
    a.weapon_kind = "missile"
    a.spells_prepared = ["sleep"]
    a.specials = [{"type": "gaze_petrification"}]
    a.special_text = "Breath weapon"
    caps = detect_capabilities(a, g)
    assert {"ranged", "spellcasting", "gaze", "breath", "special", "morale", "melee"}.issubset(caps)
    assert choose_attack_mode(capabilities=caps, combat_distance_ft=30, prefer_ranged=True) == "missile"
    assert choose_attack_mode(capabilities=caps, combat_distance_ft=10, prefer_ranged=True) == "melee"
    assert morale_behavior("high") == "press"


def test_legality_seam_delegates_without_behavior_change():
    gm = GridMap.empty(5, 5, tile="floor")
    living = {
        "a": ("pc", (1, 1)),
        "b": ("foe", (1, 2)),
        "c": ("foe", (4, 4)),
    }
    assert grid_target_is_attackable(
        gm=gm, attacker_id="a", attacker_pos=(1, 1), attacker_side="pc",
        target_id="b", living=living, mode="melee"
    )
    assert not grid_target_is_attackable(
        gm=gm, attacker_id="a", attacker_pos=(1, 1), attacker_side="pc",
        target_id="c", living=living, mode="melee"
    )
    assert grid_pair_is_attack_legal(gm=gm, attacker_pos=(1, 1), target_pos=(1, 2), mode="melee")
    assert theater_target_is_valid(_actor(is_pc=False), []) is False


def test_action_block_reason_has_stable_priority():
    a = _actor()
    apply_status(a, "silence", 1, mode="max")
    apply_status(a, "asleep", 1, mode="max")
    assert action_block_reason(a, for_casting=True) == "asleep"


class _UI:
    def __init__(self):
        self.lines = []
    def log(self, msg):
        self.lines.append(str(msg))
    def choose(self, prompt, labels):
        return 0


class _GameStub:
    def __init__(self):
        self.ui = _UI()
        self.party = Party(members=[])


def test_grid_battle_status_writes_route_through_lifecycle(monkeypatch):
    calls = {"apply": 0, "clear": 0}

    def _apply(actor, key, value, mode="replace"):
        calls["apply"] += 1
        actor.status[key] = value
        return True

    def _clear(actor, key):
        calls["clear"] += 1
        actor.status.pop(key, None)
        return True

    monkeypatch.setattr(gbmod, "apply_status", _apply)
    monkeypatch.setattr(gbmod, "clear_status", _clear)

    gm = GridMap.empty(3, 3, tile="floor")
    gm.set(1, 0, "cover")
    g = GridBattle(game=_GameStub(), grid_map=gm)
    a = _actor(is_pc=True)
    g.add_entity(GridEntity("u", a, "pc", 1, 1))
    g.setup_default_turn_order()

    assert g.take_cover("u")
    assert calls["apply"] >= 1

    assert g.move("u", 1, 2)
    assert calls["clear"] >= 1


def test_spellcasting_status_writes_route_through_lifecycle(monkeypatch):
    calls = {"apply": 0, "clear": 0, "leave": 0}

    def _apply(actor, key, value, mode="replace"):
        calls["apply"] += 1
        actor.status[key] = value
        return True

    def _clear(actor, key):
        calls["clear"] += 1
        actor.status.pop(key, None)
        return True

    def _leave(actor, marker, remove_effects=(), clear_status_keys=()):
        calls["leave"] += 1
        fx = list(getattr(actor, "effects", []) or [])
        for e in remove_effects:
            fx = [x for x in fx if x != e]
        if marker not in fx:
            fx.append(marker)
        actor.effects = fx
        return True

    monkeypatch.setattr(spmod, "apply_status", _apply)
    monkeypatch.setattr(spmod, "clear_status", _clear)
    monkeypatch.setattr(spmod, "leave_combat", _leave)

    game = _GameStub()
    caster = _actor(is_pc=True)
    ally = _actor(is_pc=True)
    ally.name = "Ally"
    game.party.members = [caster, ally]

    spmod.apply_spell_out_of_combat(game=game, caster=caster, spell_name="bless", context="dungeon")
    assert calls["apply"] >= 1


def test_leave_combat_marks_and_prunes_pending_flags_without_duplicates():
    a = _actor()
    a.effects = ["flee_pending"]
    leave_combat(a, marker="fled", remove_effects=("flee_pending",))
    assert "flee_pending" not in a.effects
    assert "fled" in a.effects
    before = list(a.effects)
    leave_combat(a, marker="fled", remove_effects=("flee_pending",))
    assert a.effects == before


def test_leave_combat_turned_and_fled_combination_preserved():
    u = _actor(is_pc=False)
    leave_combat(u, marker="fled")
    u.effects.append("turned")
    assert "fled" in u.effects and "turned" in u.effects



class _UnitStub:
    def __init__(self, unit_id, side, pos, alive=True):
        self.unit_id = unit_id
        self.side = side
        self._pos = pos
        self._alive = alive

    @property
    def pos(self):
        return self._pos

    def is_alive(self):
        return bool(self._alive)


def test_opportunity_attackers_on_leave_single_adjacent():
    cands = [
        _UnitStub("e1", "foe", (1, 0), True),
        _UnitStub("a1", "pc", (0, 1), True),
    ]
    out = opportunity_attackers_on_leave(mover_side="pc", old_pos=(1, 1), new_pos=(2, 1), candidates=cands)
    assert out == ["e1"]


def test_opportunity_attackers_on_leave_multi_adjacent_sorted_and_filtered():
    cands = [
        _UnitStub("e2", "foe", (1, 0), True),
        _UnitStub("e1", "foe", (0, 1), True),
        _UnitStub("dead", "foe", (2, 1), False),
        _UnitStub("ally", "pc", (1, 2), True),
        _UnitStub("stick", "foe", (3, 1), True),  # remains adjacent after move
    ]
    out = opportunity_attackers_on_leave(mover_side="pc", old_pos=(1, 1), new_pos=(2, 1), candidates=cands)
    assert out == ["e1", "e2"]


class _ValidationGame:
    def __init__(self):
        self.party = Party(members=[])
        self.MAX_PCS = 8
        self.MAX_RETAINERS = 12
        self.factions = {}
        self.conflict_clocks = {}
        self.contract_offers = []
        self.active_contracts = []
        self.world_hexes = {}
        self.dungeon_rooms = {}
        self.campaign_day = 1
        self.day_watch = 0
        self.torches = 0
        self.rations = 0
        self.gold = 0


def _messages(issues):
    return [i.message for i in issues]


def test_validation_flags_dead_unit_still_in_occupancy():
    game = _ValidationGame()
    state = GridBattleState(gm=GridMap.empty(3, 3, tile="floor"))
    dead = _actor(is_pc=False)
    dead.hp = 0
    state.units["u_dead"] = UnitState("u_dead", dead, "foe", 1, 1)
    state.occupancy[(1, 1)] = "u_dead"
    game.grid_battle_state = state

    msgs = _messages(validate_state(game))
    assert any("dead unit occupies active tile" in m for m in msgs)


def test_validation_flags_fled_or_surrendered_unit_as_active_targetable_participant():
    game = _ValidationGame()
    state = GridBattleState(gm=GridMap.empty(3, 3, tile="floor"))
    fled = _actor(is_pc=False)
    fled.effects = ["fled"]
    state.units["u_fled"] = UnitState("u_fled", fled, "foe", 1, 1)
    state.occupancy[(1, 1)] = "u_fled"
    state.initiative = ["u_fled"]
    game.grid_battle_state = state

    msgs = _messages(validate_state(game))
    assert any("non-active unit remains in occupancy" in m for m in msgs)
    assert any("out-of-combat unit remains in active turn order" in m for m in msgs)


def test_validation_flags_removed_unit_with_stale_combat_markers():
    game = _ValidationGame()
    state = GridBattleState(gm=GridMap.empty(3, 3, tile="floor"))
    removed = _actor(is_pc=False)
    removed.effects = ["removed"]
    removed.status = {"casting": {"rounds": 1}, "cover": -4}
    state.units["u_removed"] = UnitState("u_removed", removed, "foe", 2, 2)
    game.grid_battle_state = state

    msgs = _messages(validate_state(game))
    assert any("retains transient combat statuses" in m for m in msgs)


def test_validation_flags_contradictory_flee_markers():
    game = _ValidationGame()
    state = GridBattleState(gm=GridMap.empty(3, 3, tile="floor"))
    actor = _actor(is_pc=False)
    actor.effects = ["fled", "flee_pending"]
    state.units["u_fx"] = UnitState("u_fx", actor, "foe", 0, 0)
    game.grid_battle_state = state

    msgs = _messages(validate_state(game))
    assert any("contradictory flee markers" in m for m in msgs)


def test_turn_controller_casting_marker_routes_through_lifecycle(monkeypatch):
    calls = {"apply": 0}

    def _apply(actor, key, value, mode="replace"):
        calls["apply"] += 1
        actor.status[key] = value
        return True

    monkeypatch.setattr(tcmod, "apply_status", _apply)

    state = GridBattleState(gm=GridMap.empty(3, 3, tile="floor"))
    caster = _actor(is_pc=True)
    caster.spells_prepared = ["sleep"]
    state.units["pc1"] = UnitState("pc1", caster, "pc", 1, 1, actions_remaining=1)
    state.phase = "DECLARE"
    state.declare_order = ["pc1"]
    state.declare_index = 0

    class _Dice:
        def randint(self, a, b):
            return a

    class _G:
        def __init__(self):
            self.dice_rng = _Dice()

    tc = tcmod.TurnController(_G(), state)
    tc.node = "DECLARE_SELECT_SPELL_TARGET"
    tc.sel.action_id = "CAST"
    tc.sel.selected_spell_name = "sleep"

    monkeypatch.setattr(tc, "_try_build_cast", lambda unit_id, sp, pos: object())
    monkeypatch.setattr(tc, "_declare", lambda unit_id, cmd, events: None)

    events = tc.handle_intent(tcmod.IntentSelectTile(1, 2))
    assert events is not None
    assert calls["apply"] >= 1


def test_grid_state_start_round_status_tick_routes_through_lifecycle(monkeypatch):
    calls = {"apply": 0, "clear": 0}

    def _apply(actor, key, value, mode="replace"):
        calls["apply"] += 1
        actor.status[key] = value
        return True

    def _clear(actor, key):
        calls["clear"] += 1
        actor.status.pop(key, None)
        return True

    monkeypatch.setattr(gsmod, "apply_status", _apply)
    monkeypatch.setattr(gsmod, "clear_status", _clear)

    state = GridBattleState(gm=GridMap.empty(3, 3, tile="floor"))
    a = _actor(is_pc=True)
    a.status = {"casting": {"round": 1}}
    state.add_unit(UnitState("u1", a, "pc", 1, 1))

    class _Dice:
        def __init__(self):
            self._n = 0

        def randint(self, a, b):
            self._n += 1
            return a if (self._n % 2) else min(b, a + 1)

    class _G:
        def __init__(self):
            self.dice_rng = _Dice()

    ev = state.start_new_round(_G())
    assert ev is not None
    assert calls["apply"] == 0
    assert calls["clear"] >= 1
