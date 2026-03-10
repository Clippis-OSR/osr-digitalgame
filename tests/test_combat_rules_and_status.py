import sww.grid_battle as gbmod
import sww.spellcasting as spmod
from sww.models import Actor
from sww.combat_rules import is_melee_engaged, can_use_missile, shooting_into_melee_penalty, foe_frontage_limit, apply_forced_retreat, opportunity_attackers_on_leave
from sww.status_lifecycle import apply_status, tick_round_statuses, cleanup_actor_battle_status, action_block_reason, leave_combat
from sww.ai_capabilities import detect_capabilities, choose_attack_mode, morale_behavior
from sww.combat_legality import theater_target_is_valid, grid_target_is_attackable, grid_pair_is_attack_legal
from sww.grid_map import GridMap
from sww.grid_battle import GridBattle, GridEntity
from sww.models import Party


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
