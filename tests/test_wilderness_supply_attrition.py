from sww.game import Game, PC
from sww.ui_headless import HeadlessUI
from sww.models import Stats
from sww.travel_state import DUNGEON_ENTRANCE_HEX


def _new_game(seed: int = 9400) -> Game:
    g = Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)
    g._wilderness_encounter_check = lambda hx, encounter_mod=0: None
    g.party.members = [
        PC(name="Scout", hp=8, hp_max=8, ac_desc=8, hd=1, save=15, is_pc=True, stats=Stats(10, 10, 10, 10, 10, 10)),
        PC(name="Porter", hp=7, hp_max=7, ac_desc=9, hd=1, save=15, is_pc=True, stats=Stats(9, 10, 10, 10, 10, 10)),
    ]
    return g


def test_travel_consumes_rations_and_night_torch_light():
    g = _new_game(9410)
    party_size = len(g.party.members or [])
    g.rations = 99
    g.torches = 3
    g.day_watch = 4  # evening

    assert g._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok

    assert g.rations == 99 - party_size
    assert g.torches == 2


def test_supply_attrition_is_deterministic_for_same_seed_and_actions():
    g1 = _new_game(9420)
    g2 = _new_game(9420)
    for g in (g1, g2):
        g.rations = 50
        g.torches = 4
        g.day_watch = 4

    assert g1._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok
    assert g2._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok

    hp1 = [(m.name, int(m.hp)) for m in g1.party.members]
    hp2 = [(m.name, int(m.hp)) for m in g2.party.members]
    assert hp1 == hp2
    assert (g1.rations, g1.torches, g1.day_watch) == (g2.rations, g2.torches, g2.day_watch)


def test_running_out_of_rations_causes_minor_hp_attrition():
    g = _new_game(9430)
    before_total_hp = sum(int(m.hp) for m in g.party.living())
    g.rations = 0
    g.torches = 2
    g.day_watch = 0

    assert g._cmd_travel_to_hex(*DUNGEON_ENTRANCE_HEX).ok

    after_total_hp = sum(int(m.hp) for m in g.party.living())
    assert after_total_hp < before_total_hp
