from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict
from sww.travel_state import DUNGEON_ENTRANCE_HEX


def _new_game(seed: int = 9800) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def _sched(g: Game) -> tuple[int, int]:
    ts = g.travel_state
    return (
        int(getattr(ts, "encounter_clock_ticks", 0)),
        int(getattr(ts, "encounter_next_check_tick", 1)),
    )


def test_repeated_travel_schedules_checks_deterministically():
    g1 = _new_game(seed=9900)
    g2 = _new_game(seed=9900)

    c1 = {"n": 0}
    c2 = {"n": 0}

    g1._wilderness_encounter_check = lambda hx, encounter_mod=0: c1.__setitem__("n", c1["n"] + 1)
    g2._wilderness_encounter_check = lambda hx, encounter_mod=0: c2.__setitem__("n", c2["n"] + 1)

    for _ in range(3):
        assert g1._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok
        # bounce back to town-adjacent by step toward town to keep valid adjacency cycles
        assert g1._cmd_step_toward_town().ok

        assert g2._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok
        assert g2._cmd_step_toward_town().ok

    assert c1["n"] == c2["n"]
    assert _sched(g1) == _sched(g2)


def test_save_load_preserves_encounter_scheduler_state():
    g = _new_game(seed=9910)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    assert g._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok
    before = _sched(g)

    data = game_to_dict(g)
    g2 = _new_game(seed=9911)
    apply_game_dict(g2, data)

    assert _sched(g2) == before


def test_return_to_town_interacts_with_scheduler_coherently():
    g = _new_game(seed=9920)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    assert g._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok
    t1, n1 = _sched(g)
    assert n1 > t1

    assert g._cmd_return_to_town().ok
    t2, n2 = _sched(g)
    assert t2 > t1
    assert n2 > t2
    assert g._wilderness_encounter_due() is False


def test_dungeon_transitions_do_not_corrupt_scheduler_state():
    g = _new_game(seed=9930)
    before = _sched(g)

    assert g._cmd_enter_dungeon().ok
    mid = _sched(g)
    assert mid[0] >= before[0]
    assert mid[1] >= before[1]

    assert g._cmd_dungeon_leave().ok
    after = _sched(g)
    assert after[0] >= mid[0]
    assert after[1] >= mid[1]
