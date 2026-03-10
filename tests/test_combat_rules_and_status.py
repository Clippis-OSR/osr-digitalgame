from sww.models import Actor
from sww.combat_rules import is_melee_engaged, can_use_missile, shooting_into_melee_penalty, foe_frontage_limit, apply_forced_retreat
from sww.status_lifecycle import apply_status, tick_round_statuses, cleanup_actor_battle_status
from sww.ai_capabilities import detect_capabilities, choose_attack_mode


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


def test_status_lifecycle_tick_and_cleanup():
    a = _actor()
    apply_status(a, "asleep", 2, mode="max")
    apply_status(a, "parry", {"rounds": 1, "penalty": 2})
    apply_status(a, "casting", {"rounds": 1})
    tick_round_statuses([a], phase="start")
    assert a.status.get("asleep") == 1
    assert "parry" not in a.status
    assert "casting" in a.status
    cleanup_actor_battle_status(a)
    assert "casting" not in a.status


def test_ai_capability_seam():
    g = DummyGame()
    a = _actor()
    a.weapon_kind = "missile"
    a.spells_prepared = ["sleep"]
    caps = detect_capabilities(a, g)
    assert "ranged" in caps and "spellcasting" in caps
    assert choose_attack_mode(capabilities=caps, combat_distance_ft=30, prefer_ranged=True) == "missile"
    assert choose_attack_mode(capabilities=caps, combat_distance_ft=10, prefer_ranged=True) == "melee"
