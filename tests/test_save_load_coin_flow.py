from sww.game import Game
from sww.save_load import apply_game_dict, game_to_dict, SAVE_VERSION
from sww.ui_headless import HeadlessUI


def test_coin_reward_abstractions_are_transient_not_serialized():
    g = Game(HeadlessUI(), dice_seed=30200, wilderness_seed=30201)
    g.gold = 17
    g.loot_pool.coins_gp = 4

    data = game_to_dict(g)

    assert int(data.get("save_version", 0)) == int(SAVE_VERSION)
    campaign = dict(data.get("campaign") or {})
    assert int(campaign.get("gold", 0)) == 17
    lp = dict(campaign.get("loot_pool") or {})
    assert int(lp.get("coins_gp", 0)) == 4
    # RewardBundle and coin-routing decision objects are runtime-only.
    assert "reward_bundle" not in campaign
    assert "coin_destination" not in campaign


def test_save_load_resume_does_not_duplicate_room_treasure_coin_credit():
    g = Game(HeadlessUI(), dice_seed=30210, wilderness_seed=30211)
    room = {
        "id": 1,
        "type": "treasure",
        "treasure_taken": False,
        "treasure_gp": 12,
        "treasure_items": [{"name": "Saved Dagger", "kind": "weapon"}],
        "_delta": {},
    }
    g.dungeon_rooms = {1: room}
    g.current_room_id = 1

    g._handle_room_treasure(room)
    assert g.gold == 12
    assert room.get("treasure_taken") is True

    payload = game_to_dict(g)

    g2 = Game(HeadlessUI(), dice_seed=30212, wilderness_seed=30213)
    apply_game_dict(g2, payload)
    room2 = dict((g2.dungeon_rooms or {}).get(1) or {})

    assert int(getattr(g2, "gold", 0) or 0) == 12
    assert bool(room2.get("treasure_taken")) is True

    # Re-running treasure handler after load should be a no-op for coin credit.
    g2._handle_room_treasure(room2)
    assert int(getattr(g2, "gold", 0) or 0) == 12
