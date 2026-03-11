from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.travel_state import DUNGEON_ENTRANCE_HEX
from sww.save_load import game_to_dict, apply_game_dict
from sww.wilderness import first_time_poi_resolution_key


def _new_game(seed: int = 17000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def test_resolve_travel_context_normalizes_current_wilderness_state():
    g = _new_game()
    g.wilderness_travel_mode = "cautious"
    g.day_watch = 5
    g.rations = 0
    hx = g._ensure_current_hex()
    hx["terrain"] = "road"
    g.world_hexes[f"{g.party_hex[0]},{g.party_hex[1]}"] = hx

    ctx = g.resolve_travel_context(hx)

    assert ctx["terrain"] == "road"
    assert ctx["pace"] == "cautious"
    assert ctx["watch"] == "Night"
    assert ctx["on_road"] is True
    assert ctx["low_supplies"] is True
    assert isinstance(ctx["encounter_mod"], int)


def test_resolve_travel_context_used_for_travel_encounter_mod():
    g = _new_game(seed=17100)
    seen = {"mod": None}

    g.resolve_travel_context = lambda hx=None: {
        "terrain": "clear",
        "pace": "normal",
        "watch": "Morning",
        "on_road": False,
        "party_ready": True,
        "low_supplies": False,
        "encounter_mod": 2,
        "discovery_mod": 0,
    }
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: seen.__setitem__("mod", encounter_mod)

    assert g._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok
    assert seen["mod"] == 2


def test_first_time_poi_resolution_key_is_stable_across_paths():
    poi = {"id": "poi:canonical:dungeon_entrance", "type": "dungeon_entrance", "name": "Dungeon Entrance"}
    assert first_time_poi_resolution_key(poi, q=1, r=0, mode="explore") == first_time_poi_resolution_key(
        poi, q=4, r=-3, mode="explore"
    )


def test_poi_first_time_reward_or_event_does_not_duplicate():
    g = _new_game(seed=17200)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g._handle_current_hex_poi = lambda: None

    assert g._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok
    assert g._cmd_investigate_current_location().ok
    assert g._cmd_investigate_current_location().ok

    explored = [e for e in (g.event_history or []) if str(e.get("type") or "") == "poi.explored"]
    assert len(explored) == 1

    data = game_to_dict(g)
    g2 = _new_game(seed=17201)
    apply_game_dict(g2, data)
    g2._handle_current_hex_poi = lambda: None
    assert g2._cmd_investigate_current_location().ok

    explored2 = [e for e in (g2.event_history or []) if str(e.get("type") or "") == "poi.explored"]
    assert len(explored2) == 1
