from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict
from sww.travel_state import DUNGEON_ENTRANCE_HEX, TOWN_HEX

CANONICAL_POI_ID = "poi:canonical:dungeon_entrance"


def _new_game(seed: int = 12000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def _rumors_for_poi(g: Game, poi_id: str):
    return [r for r in (g.rumors or []) if str(r.get("poi_id") or "") == str(poi_id)]


def test_rumor_data_stable_across_save_load():
    g = _new_game()
    before = [dict(x) for x in (g.rumors or [])]
    assert any(str(r.get("poi_id") or "") == CANONICAL_POI_ID for r in before)

    data = game_to_dict(g)
    g2 = _new_game(seed=12001)
    apply_game_dict(g2, data)

    after = [dict(x) for x in (g2.rumors or [])]
    assert before == after


def test_canonical_entrance_surfaced_via_rumor_layer():
    g = _new_game()
    rows = _rumors_for_poi(g, CANONICAL_POI_ID)
    assert len(rows) == 1
    r = rows[0]
    assert str(r.get("kind") or "") == "dungeon_entrance"
    assert (int(r.get("q", 0)), int(r.get("r", 0))) == tuple(DUNGEON_ENTRANCE_HEX)


def test_transitions_do_not_corrupt_rumor_discovery_linkage():
    g = _new_game(seed=12010)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    assert g._cmd_enter_dungeon().ok
    assert g._cmd_dungeon_leave().ok
    assert g._cmd_step_toward_town().ok
    assert tuple(g.party_hex) == tuple(TOWN_HEX)

    rows = _rumors_for_poi(g, CANONICAL_POI_ID)
    assert len(rows) == 1
    assert bool(rows[0].get("seen", False)) is True


def test_discovered_poi_and_surface_rumors_do_not_duplicate():
    g = _new_game(seed=12020)
    # Force repeated surfacing attempts; dedupe should keep one canonical rumor row.
    g._ensure_minimal_rumor_surface()
    g._ensure_minimal_rumor_surface()

    rows = _rumors_for_poi(g, CANONICAL_POI_ID)
    assert len(rows) == 1

    # Discover entrance and re-surface; still exactly one linked rumor.
    assert g._cmd_enter_dungeon().ok
    assert g._cmd_dungeon_leave().ok
    g._ensure_minimal_rumor_surface()

    rows2 = _rumors_for_poi(g, CANONICAL_POI_ID)
    assert len(rows2) == 1
    assert bool(rows2[0].get("seen", False)) is True
