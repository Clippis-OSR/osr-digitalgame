from unittest.mock import patch
from sww.models import Actor
from sww.combat_rules import is_melee_engaged, can_use_missile, shooting_into_melee_penalty, foe_frontage_limit, apply_forced_retreat
from sww.status_lifecycle import apply_status, tick_round_statuses, cleanup_actor_battle_status, clear_status, status_dict
from sww.ai_capabilities import detect_capabilities, choose_attack_mode
from sww.grid_map import GridMap
from sww.combat_legality import grid_target_is_attackable, grid_pair_is_attack_legal
from sww.game import Game
from sww.ui_headless import HeadlessUI


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


def test_grid_attack_legality_seam_matches_pair_checks():
    gm = GridMap.empty(5, 5)
    living = {
        "pc1": ("pc", (1, 1)),
        "foe_adj": ("foe", (2, 1)),
        "foe_far": ("foe", (4, 4)),
    }

    assert grid_target_is_attackable(
        gm=gm,
        attacker_id="pc1",
        attacker_pos=(1, 1),
        attacker_side="pc",
        target_id="foe_adj",
        living=living,
        mode="melee",
    )
    assert not grid_target_is_attackable(
        gm=gm,
        attacker_id="pc1",
        attacker_pos=(1, 1),
        attacker_side="pc",
        target_id="foe_far",
        living=living,
        mode="melee",
    )

    assert grid_target_is_attackable(
        gm=gm,
        attacker_id="pc1",
        attacker_pos=(1, 1),
        attacker_side="pc",
        target_id="foe_adj",
        living=living,
        mode="missile",
    )
    assert grid_target_is_attackable(
        gm=gm,
        attacker_id="pc1",
        attacker_pos=(1, 1),
        attacker_side="pc",
        target_id="foe_far",
        living=living,
        mode="missile",
    )

    assert grid_pair_is_attack_legal(gm=gm, attacker_pos=(1, 1), target_pos=(2, 1), mode="melee")
    assert not grid_pair_is_attack_legal(gm=gm, attacker_pos=(1, 1), target_pos=(4, 4), mode="melee")


def test_grid_attack_legality_rejects_unknown_modes_deterministically():
    gm = GridMap.empty(3, 3)
    living = {"pc1": ("pc", (1, 1)), "foe1": ("foe", (1, 2))}
    assert not grid_target_is_attackable(
        gm=gm,
        attacker_id="pc1",
        attacker_pos=(1, 1),
        attacker_side="pc",
        target_id="foe1",
        living=living,
        mode="",
    )
    assert not grid_pair_is_attack_legal(gm=gm, attacker_pos=(1, 1), target_pos=(1, 2), mode="")


def test_status_helpers_preserve_payload_shapes_and_clear_keys():
    a = _actor()
    assert status_dict(a) == {}

    payload = {"rounds": 1, "spell": "sleep", "disrupted": False}
    apply_status(a, "casting", payload)
    assert a.status.get("casting") == payload

    assert clear_status(a, "casting")
    assert "casting" not in a.status
    assert not clear_status(a, "casting")


def test_leave_combat_seam_marks_exit_and_cleans_transient_statuses():
    g = Game(HeadlessUI(), dice_seed=1, wilderness_seed=2)
    a = _actor(is_pc=False)
    apply_status(a, "casting", {"rounds": 1, "spell": "sleep", "disrupted": False})
    apply_status(a, "cover", -2)
    a.effects = ["flee_pending"]

    g._leave_combat_actor(a, marker="fled", remove_effects=("flee_pending",))

    assert "fled" in (a.effects or [])
    assert "flee_pending" not in (a.effects or [])
    assert "casting" not in (a.status or {})
    assert "cover" not in (a.status or {})

    g._leave_combat_actor(a, marker="fled")
    assert (a.effects or []).count("fled") == 1


def test_combat_retarget_uses_stable_enemy_order_before_rng_choice():
    g = Game(HeadlessUI(), dice_seed=1, wilderness_seed=2)
    g.dice_rng.choice = lambda xs: xs[0]

    actor = _actor(is_pc=True)
    stale = Actor(name="Stale", hp=0, hp_max=1, ac_desc=9, hd=1, save=14, is_pc=False)
    z = Actor(name="Zulu", hp=5, hp_max=5, ac_desc=9, hd=1, save=14, is_pc=False)
    a = Actor(name="Alpha", hp=5, hp_max=5, ac_desc=9, hd=1, save=14, is_pc=False)

    plan = {"type": "melee", "actor": actor, "target": stale}
    out = g._combat_retarget_or_clear(actor, plan, [z, a], reason="ordering")
    assert out.get("target") is a

    out_rev = g._combat_retarget_or_clear(actor, plan, [a, z], reason="ordering")
    assert out_rev.get("target") is a


def test_notify_death_uses_single_cleanup_owner_once_for_dead_actor():
    g = Game(HeadlessUI(), dice_seed=1, wilderness_seed=2)
    a = _actor(is_pc=False)
    a.hp = 0
    apply_status(a, "casting", {"rounds": 1, "spell": "sleep", "disrupted": False})

    target_calls = {"n": 0}
    orig = g._cleanup_combat_actor_state

    def _wrapped(actor):
        if actor is a:
            target_calls["n"] += 1
        return orig(actor)

    with patch.object(g.effects_mgr, "cleanup_on_death", return_value=None):
        with patch.object(g, "emit", return_value=None):
            with patch.object(g, "_cleanup_combat_actor_state", side_effect=_wrapped):
                g._notify_death(a, ctx={"combat_foes": []})

    assert target_calls["n"] == 1
    assert "casting" not in (a.status or {})


def test_notify_death_skips_transient_cleanup_for_living_actor():
    g = Game(HeadlessUI(), dice_seed=1, wilderness_seed=2)
    a = _actor(is_pc=False)
    a.hp = 1
    apply_status(a, "casting", {"rounds": 1, "spell": "sleep", "disrupted": False})

    with patch.object(g.effects_mgr, "cleanup_on_death", return_value=None):
        with patch.object(g, "emit", return_value=None):
            with patch.object(g, "_cleanup_combat_actor_state", wraps=g._cleanup_combat_actor_state) as cleanup_spy:
                g._notify_death(a, ctx={"combat_foes": []})

    assert cleanup_spy.call_count == 0
    assert "casting" in (a.status or {})
