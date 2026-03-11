from sww.game import Game, PC, Stats
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict


def _new_game(seed: int = 30000) -> Game:
    g = Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    if not g.party.pcs():
        g.party.members = [
            PC(
                name="Hero",
                race="Human",
                hp=6,
                hp_max=8,
                ac_desc=9,
                hd=1,
                save=15,
                morale=9,
                alignment="Neutrality",
                is_pc=True,
                cls="Fighter",
                level=1,
                xp=0,
                stats=Stats(10, 10, 10, 10, 10, 10),
            )
        ]
    return g


def _append(g: Game, etype: str, payload: dict | None = None, title: str = "") -> dict:
    return g._append_player_event(
        etype,
        category="expedition",
        title=title or etype,
        payload=dict(payload or {}),
        refs={},
    )


def test_expedition_recap_generated_on_return_to_town():
    g = _new_game()
    pc = g.party.pcs()[0]
    pc.xp += 25
    g.gold += 12

    assert g._cmd_enter_dungeon().ok
    _append(g, "dungeon.depth_reached", {"depth": 2}, "Reached dungeon depth 2")
    _append(g, "poi.explored", {"poi_name": "Ruined Shrine"}, "Explored Ruined Shrine")

    assert g._cmd_return_to_town().ok

    assert any("Expedition Recap" in ln for ln in g.ui.lines)
    assert any("Depth reached: 2" in ln for ln in g.ui.lines)
    assert any("POIs explored: Ruined Shrine" in ln for ln in g.ui.lines)


def test_expedition_recap_uses_event_history_window():
    g = _new_game(seed=30010)

    d1 = _append(g, "expedition.departed", {"gold_start": 10, "party_xp_total": 100}, "Expedition departed")
    _append(g, "poi.explored", {"poi_name": "Old Ruins"}, "Explored Old Ruins")
    _append(g, "expedition.returned", {"day_cost": 1}, "Returned to Town")

    d2 = _append(g, "expedition.departed", {"gold_start": 30, "party_xp_total": 200}, "Expedition departed")
    _append(g, "poi.explored", {"poi_name": "Sunken Hall"}, "Explored Sunken Hall")
    r2 = _append(g, "expedition.returned", {"day_cost": 1}, "Returned to Town")

    assert int(d2.get("eid", 0)) > int(d1.get("eid", 0))
    lines = g._build_expedition_recap_lines(returned_eid=int(r2.get("eid", 0)))

    assert any("POIs explored: Sunken Hall" in ln for ln in lines)
    assert all("Old Ruins" not in ln for ln in lines)


def test_expedition_recap_after_save_load():
    g = _new_game(seed=30020)
    _append(g, "expedition.departed", {"gold_start": 100, "party_xp_total": 0}, "Expedition departed")
    _append(g, "contract.completed", {"title": "Missing Caravan", "reward_gp": 25}, "Completed contract: Missing Caravan")
    g.gold = 125
    ret = _append(g, "expedition.returned", {"day_cost": 1}, "Returned to Town")

    data = game_to_dict(g)
    g2 = _new_game(seed=30021)
    apply_game_dict(g2, data)

    lines = g2._build_expedition_recap_lines(returned_eid=int(ret.get("eid", 0)))
    assert any("Contracts completed: Missing Caravan" in ln for ln in lines)
    assert any("Gold gained: 25" in ln for ln in lines)
