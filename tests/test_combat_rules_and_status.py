from sww.models import Actor
from sww.combat_rules import is_melee_engaged, can_use_missile, shooting_into_melee_penalty, foe_frontage_limit, apply_forced_retreat
from sww.status_lifecycle import apply_status, tick_round_statuses, cleanup_actor_battle_status, action_block_reason
from sww.ai_capabilities import detect_capabilities, choose_attack_mode, morale_behavior
from sww.combat_legality import theater_target_is_valid, grid_target_is_attackable, grid_pair_is_attack_legal
from sww.grid_map import GridMap


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
