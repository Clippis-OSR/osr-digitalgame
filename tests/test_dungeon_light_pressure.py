from sww.game import Game
from sww.ui_headless import HeadlessUI
from sww.save_load import game_to_dict, apply_game_dict


def _new_game(seed: int = 12000) -> Game:
    g = Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)
    g._check_wandering_monster = lambda: False
    return g


def test_dungeon_time_spend_consumes_light_and_warns_low_torch():
    g = _new_game()
    g.light_on = True
    g.torch_turns_left = 3
    g.magical_light_turns = 0

    g.spend_dungeon_time(1, reason="test")

    assert g.dungeon_turn == 1
    assert g.torch_turns_left == 2
    assert any("torchlight is running low (2 turns left)" in ln for ln in g.ui.lines)


def test_zero_light_adds_darkness_pressure_and_logs_consequence():
    g = _new_game(seed=12010)
    g.light_on = False
    g.torch_turns_left = 0
    g.magical_light_turns = 0
    g.noise_level = 0

    g.spend_dungeon_time(1, reason="test_dark")

    assert g.dungeon_turn == 1
    assert g.noise_level == 0  # +1 darkness pressure, then deterministic -1 decay in same turn
    assert any("fumble in darkness" in ln for ln in g.ui.lines)
    assert any((e.name == "dungeon_darkness_pressure") for e in g.events.events)


def test_darkness_blocks_precision_search_actions():
    g = _new_game(seed=12020)
    g.current_room_id = 1
    g.light_on = False
    g.torch_turns_left = 0
    g.magical_light_turns = 0

    res = g._cmd_dungeon_search_secret()

    assert res.status == "error"
    assert any("Too dark to search for secret doors" in ln for ln in g.ui.lines)


def test_save_load_preserves_dungeon_light_pressure_state():
    g = _new_game(seed=12030)
    g.dungeon_turn = 4
    g.torches = 1
    g.torch_turns_left = 2
    g.magical_light_turns = 0
    g.light_on = True

    payload = game_to_dict(g)

    g2 = _new_game(seed=12031)
    apply_game_dict(g2, payload)

    assert g2.dungeon_turn == 4
    assert g2.torches == 1
    assert g2.torch_turns_left == 2
    assert g2.light_on is True


def test_dungeon_pressure_summary_surfaces_lit_state_fields():
    g = _new_game(seed=12040)
    g.light_on = True
    g.torches = 4
    g.torch_turns_left = 2
    g.magical_light_turns = 0
    g.noise_level = 1
    g.wandering_chance = 1
    g.dungeon_alarm_by_level[g.dungeon_level] = 0

    line = g._dungeon_pressure_summary()

    assert "Pressure:" in line
    assert "Light torch 2t" in line
    assert "Spare torches 4" in line
    assert "Noise 1/5" in line
    assert "Wander 1/6" in line


def test_dungeon_pressure_summary_surfaces_dark_state_explicitly():
    g = _new_game(seed=12050)
    g.light_on = False
    g.torch_turns_left = 0
    g.magical_light_turns = 0
    g.torches = 0
    g.noise_level = 0

    line = g._dungeon_pressure_summary()

    assert "Light DARK" in line
    assert "Spare torches 0" in line


def test_dungeon_pressure_summary_after_save_load_continuity():
    g = _new_game(seed=12060)
    g.dungeon_turn = 4
    g.torches = 1
    g.torch_turns_left = 1
    g.magical_light_turns = 0
    g.light_on = True
    g.noise_level = 2

    payload = game_to_dict(g)

    g2 = _new_game(seed=12061)
    apply_game_dict(g2, payload)

    line = g2._dungeon_pressure_summary()
    assert "Light torch 1t" in line
    assert "Spare torches 1" in line
    assert "Noise 2/5" in line
