from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict
from sww.travel_state import DUNGEON_ENTRANCE_HEX, TOWN_HEX


def _new_game(seed: int = 15000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def _cond(g: Game):
    ts = g.travel_state
    return (
        str(getattr(ts, "travel_condition", "clear") or "clear"),
        int(getattr(ts, "condition_started_clock", 0) or 0),
        int(getattr(ts, "wilderness_clock_turns", 0) or 0),
    )


def test_condition_state_stable_across_save_load():
    g = _new_game()
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    assert g._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok

    before = _cond(g)
    data = game_to_dict(g)

    g2 = _new_game(seed=15001)
    apply_game_dict(g2, data)
    after = _cond(g2)

    assert before == after


def test_repeated_travel_updates_conditions_deterministically():
    g1 = _new_game(seed=15100)
    g2 = _new_game(seed=15100)
    g1._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g2._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    for _ in range(8):
        assert g1._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok
        assert g1._cmd_step_toward_town().ok
        assert g2._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok
        assert g2._cmd_step_toward_town().ok

    assert _cond(g1) == _cond(g2)


def test_transitions_do_not_corrupt_condition_state():
    g = _new_game(seed=15200)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    c0 = _cond(g)
    assert g._cmd_enter_dungeon().ok
    assert g._cmd_dungeon_leave().ok
    c1 = _cond(g)
    assert c1[2] >= c0[2]

    assert g._cmd_step_toward_town().ok
    c2 = _cond(g)
    assert c2[2] >= c1[2]
    assert tuple(g.party_hex) in (tuple(TOWN_HEX), tuple(DUNGEON_ENTRANCE_HEX))


def test_condition_does_not_reset_on_repeated_reentry():
    g = _new_game(seed=15300)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    for _ in range(5):
        assert g._cmd_enter_dungeon().ok
        assert g._cmd_dungeon_leave().ok
        assert g._cmd_step_toward_town().ok

    cond, started, clock = _cond(g)
    assert cond in {"clear", "wind", "rain", "fog"}
    assert started <= clock
    # Re-run the condition hook; should not regress/reset.
    before = _cond(g)
    g._update_wilderness_condition()
    after = _cond(g)
    assert after[2] == before[2]
    assert after[1] <= after[2]
