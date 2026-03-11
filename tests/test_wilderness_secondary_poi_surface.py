from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict

SECONDARY_POIS = {
    (1, 0): "poi:secondary:ruins:1,0",
    (0, -1): "poi:secondary:shrine:0,-1",
}


def _new_game(seed: int = 14000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def _poi_at(g: Game, q: int, r: int) -> dict:
    return ((g.world_hexes.get(f"{q},{r}") or {}).get("poi") or {})


def test_secondary_pois_stable_across_save_load():
    g = _new_game()
    for (q, r), pid in SECONDARY_POIS.items():
        poi = _poi_at(g, q, r)
        assert str(poi.get("id") or "") == pid

    data = game_to_dict(g)
    g2 = _new_game(seed=14001)
    apply_game_dict(g2, data)

    for (q, r), pid in SECONDARY_POIS.items():
        poi2 = _poi_at(g2, q, r)
        assert str(poi2.get("id") or "") == pid


def test_secondary_discovery_persists_correctly():
    g = _new_game(seed=14010)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    assert g._cmd_travel_to_hex(1, 0).ok
    p = _poi_at(g, 1, 0)
    assert bool(p.get("discovered", False)) is True

    data = game_to_dict(g)
    g2 = _new_game(seed=14011)
    apply_game_dict(g2, data)

    p2 = _poi_at(g2, 1, 0)
    assert bool(p2.get("discovered", False)) is True


def test_secondary_rumor_linkage_is_deterministic_when_surfaced():
    g = _new_game(seed=14020)

    rumors = [r for r in (g.rumors or []) if str(r.get("source") or "") == "secondary"]
    rumor_ids = sorted(str(r.get("poi_id") or "") for r in rumors)
    expected = sorted(SECONDARY_POIS.values())
    assert rumor_ids == expected


def test_repeated_transitions_do_not_duplicate_or_corrupt_secondary_pois():
    g = _new_game(seed=14030)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None

    for _ in range(4):
        assert g._cmd_enter_dungeon().ok
        assert g._cmd_dungeon_leave().ok
        assert g._cmd_step_toward_town().ok
        g._ensure_minimal_rumor_surface()

    for (q, r), pid in SECONDARY_POIS.items():
        poi = _poi_at(g, q, r)
        assert str(poi.get("id") or "") == pid

    # Rumors are deduped by poi_id.
    for pid in SECONDARY_POIS.values():
        rows = [r for r in (g.rumors or []) if str(r.get("poi_id") or "") == pid]
        assert len(rows) == 1
