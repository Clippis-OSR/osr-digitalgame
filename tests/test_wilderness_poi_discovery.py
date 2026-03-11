from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict
from sww.travel_state import DUNGEON_ENTRANCE_HEX, TOWN_HEX


CANONICAL_POI_ID = "poi:canonical:dungeon_entrance"


def _new_game(seed: int = 6000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def _entrance_hex(game: Game) -> dict:
    key = f"{DUNGEON_ENTRANCE_HEX[0]},{DUNGEON_ENTRANCE_HEX[1]}"
    return (game.world_hexes.get(key) or {})


def _entrance_poi(game: Game) -> dict:
    return (_entrance_hex(game).get("poi") or {})


def test_canonical_dungeon_entrance_poi_stable_across_save_load():
    g = _new_game()
    p = _entrance_poi(g)
    assert p.get("id") == CANONICAL_POI_ID
    assert p.get("type") == "dungeon_entrance"

    data = game_to_dict(g)
    g2 = _new_game(seed=7000)
    apply_game_dict(g2, data)

    p2 = _entrance_poi(g2)
    assert p2.get("id") == CANONICAL_POI_ID
    assert p2.get("type") == "dungeon_entrance"


def test_entrance_discovery_state_persists_once_discovered():
    g = _new_game()
    assert g._cmd_enter_dungeon().ok
    assert g._cmd_dungeon_leave().ok

    p = _entrance_poi(g)
    assert bool(p.get("discovered", False)) is True

    data = game_to_dict(g)
    g2 = _new_game(seed=7100)
    apply_game_dict(g2, data)

    p2 = _entrance_poi(g2)
    assert bool(p2.get("discovered", False)) is True


def test_repeated_town_wilderness_dungeon_transitions_do_not_duplicate_or_corrupt_poi():
    g = _new_game(seed=6200)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    baseline_id = _entrance_poi(g).get("id")
    baseline_type = _entrance_poi(g).get("type")

    for _ in range(5):
        assert g._cmd_enter_dungeon().ok
        assert g._cmd_dungeon_leave().ok
        assert tuple(g.party_hex) == tuple(DUNGEON_ENTRANCE_HEX)
        assert g._cmd_step_toward_town().ok
        assert tuple(g.party_hex) == tuple(TOWN_HEX)

    p = _entrance_poi(g)
    assert p.get("id") == baseline_id == CANONICAL_POI_ID
    assert p.get("type") == baseline_type == "dungeon_entrance"



def test_travel_to_entrance_remains_deterministic():
    g1 = _new_game(seed=6300)
    g2 = _new_game(seed=6300)

    assert g1._cmd_enter_dungeon().ok
    assert g2._cmd_enter_dungeon().ok
    assert g1._cmd_dungeon_leave().ok
    assert g2._cmd_dungeon_leave().ok

    assert tuple(g1.party_hex) == tuple(DUNGEON_ENTRANCE_HEX)
    assert tuple(g2.party_hex) == tuple(DUNGEON_ENTRANCE_HEX)
    assert _entrance_poi(g1).get("id") == _entrance_poi(g2).get("id") == CANONICAL_POI_ID
