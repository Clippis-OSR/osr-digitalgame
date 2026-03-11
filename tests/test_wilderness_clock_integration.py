from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict
from sww.travel_state import DUNGEON_ENTRANCE_HEX, TOWN_HEX


def _new_game(seed: int = 8800) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def _clock(g: Game) -> tuple[int, int, int]:
    ts = g.travel_state
    return (
        int(getattr(ts, "travel_turns", 0)),
        int(getattr(ts, "wilderness_clock_turns", 0)),
        int(getattr(ts, "encounter_clock_ticks", 0)),
    )


def test_repeated_travel_advances_wilderness_clock_deterministically():
    g1 = _new_game(seed=9000)
    g2 = _new_game(seed=9000)
    g1._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g2._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    assert g1._cmd_enter_dungeon().ok and g2._cmd_enter_dungeon().ok
    assert g1._cmd_dungeon_leave().ok and g2._cmd_dungeon_leave().ok

    for _ in range(3):
        assert g1._cmd_step_toward_town().ok
        assert g2._cmd_step_toward_town().ok
        if tuple(g1.party_hex) == tuple(TOWN_HEX):
            break

    assert _clock(g1) == _clock(g2)


def test_save_load_preserves_wilderness_clock_state():
    g = _new_game(seed=9100)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    assert g._cmd_enter_dungeon().ok
    assert g._cmd_dungeon_leave().ok
    assert g._cmd_step_toward_town().ok

    before = _clock(g)
    data = game_to_dict(g)

    g2 = _new_game(seed=9101)
    apply_game_dict(g2, data)
    after = _clock(g2)

    assert before == after


def test_transitions_do_not_corrupt_clock_state():
    g = _new_game(seed=9200)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    start = _clock(g)
    assert g._cmd_enter_dungeon().ok
    assert g._cmd_dungeon_leave().ok
    mid = _clock(g)
    assert mid[0] >= start[0]
    assert mid[1] >= start[1]

    assert g._cmd_step_toward_town().ok
    end = _clock(g)
    assert end[0] >= mid[0]
    assert end[1] >= mid[1]
    assert tuple(g.party_hex) in (tuple(TOWN_HEX), tuple(DUNGEON_ENTRANCE_HEX))


def test_return_to_town_and_step_travel_advance_clock_as_intended():
    g = _new_game(seed=9300)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    assert g._cmd_travel_to_hex(DUNGEON_ENTRANCE_HEX[0], DUNGEON_ENTRANCE_HEX[1]).ok
    after_step = _clock(g)

    assert g._cmd_return_to_town().ok
    after_return = _clock(g)

    assert after_step[0] >= 1
    assert after_step[1] >= 1
    assert after_step[2] >= 1

    assert after_return[0] > after_step[0]
    assert after_return[1] >= after_step[1] + g.WATCHES_PER_DAY
    assert after_return[2] >= after_step[2] + 1
    assert tuple(g.party_hex) == tuple(TOWN_HEX)
