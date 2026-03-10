from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict
from sww.travel_state import DUNGEON_ENTRANCE_HEX, TOWN_HEX


def _new_game(seed: int = 7777) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def test_travel_state_persists_across_save_load():
    g = _new_game()
    g._cmd_enter_dungeon()
    g._cmd_dungeon_leave()
    g._cmd_step_toward_town()

    data = game_to_dict(g)
    g2 = _new_game()
    apply_game_dict(g2, data)

    assert g2.travel_state.to_dict() == g.travel_state.to_dict()
    assert tuple(g2.party_hex) == tuple(g.party_hex)


def test_travel_turns_advance_deterministically():
    g1 = _new_game(seed=8100)
    g2 = _new_game(seed=8100)

    assert g1._cmd_enter_dungeon().ok
    assert g2._cmd_enter_dungeon().ok
    assert g1._cmd_dungeon_leave().ok
    assert g2._cmd_dungeon_leave().ok
    assert g1._cmd_step_toward_town().ok
    assert g2._cmd_step_toward_town().ok

    assert g1.travel_state.travel_turns == g2.travel_state.travel_turns
    assert g1.travel_state.route_progress == g2.travel_state.route_progress
    assert g1.travel_state.location == g2.travel_state.location


def test_town_to_dungeon_entrance_and_back_is_stable():
    g = _new_game()

    assert tuple(g.party_hex) == tuple(TOWN_HEX)
    assert g._cmd_enter_dungeon().ok
    assert g.travel_state.location == "dungeon"

    assert g._cmd_dungeon_leave().ok
    assert tuple(g.party_hex) == tuple(DUNGEON_ENTRANCE_HEX)
    assert g.travel_state.location == "wilderness"

    assert g._cmd_step_toward_town().ok
    assert tuple(g.party_hex) == tuple(TOWN_HEX)
    assert g.travel_state.location == "town"


def test_repeated_transitions_keep_town_and_dungeon_state_stable():
    g = _new_game(seed=9200)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    assert g._cmd_enter_dungeon().ok
    baseline_instance_id = (g.dungeon_instance.dungeon_id, int(g.dungeon_instance.seed))
    baseline_room = int(g.current_room_id)
    baseline_expeditions = int(g.expeditions)

    for _ in range(3):
        assert g._cmd_dungeon_leave().ok
        assert tuple(g.party_hex) == tuple(DUNGEON_ENTRANCE_HEX)
        assert g._cmd_step_toward_town().ok
        assert tuple(g.party_hex) == tuple(TOWN_HEX)
        assert g._cmd_enter_dungeon().ok

    assert (g.dungeon_instance.dungeon_id, int(g.dungeon_instance.seed)) == baseline_instance_id
    assert int(g.current_room_id) >= 1
    assert int(g.expeditions) == baseline_expeditions + 3
    assert int(g.current_room_id) == baseline_room
