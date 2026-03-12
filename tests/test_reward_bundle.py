from types import SimpleNamespace

from sww.loot_pool import create_loot_pool
from sww.reward_bundle import (
    apply_reward_bundle,
    empty_reward_bundle,
    reward_bundle_add_coins,
    reward_bundle_add_item,
    reward_bundle_is_empty,
)


def test_reward_bundle_can_hold_coins_and_items_together():
    b = empty_reward_bundle(source="room_treasure")
    reward_bundle_add_coins(b, gp=3, sp=7, cp=9)
    reward_bundle_add_item(b, {"name": "Dagger", "kind": "weapon"})

    assert reward_bundle_is_empty(b) is False
    assert b.coins_gp == 3
    assert b.coins_sp == 7
    assert b.coins_cp == 9
    assert len(b.items) == 1


def test_apply_reward_bundle_preserves_values_into_treasury_and_loot_pool():
    game = SimpleNamespace(gold=5)
    pool = create_loot_pool()
    b = empty_reward_bundle(source="room_treasure")
    reward_bundle_add_coins(b, gp=4, sp=9, cp=99)  # 4 gp + 99 sp/cp -> 5 gp floor total
    reward_bundle_add_item(b, {"name": "Short bow", "kind": "weapon"})

    result = apply_reward_bundle(game, bundle=b, loot_pool=pool, coin_policy="treasury")

    assert result.coins_gp_awarded == 5
    assert game.gold == 10
    assert len(result.items_added) == 1
    assert len(pool.entries) == 1


def test_apply_reward_bundle_feeds_existing_item_and_coin_paths(monkeypatch):
    import sww.reward_bundle as reward_bundle_mod

    game = SimpleNamespace(gold=0)
    pool = create_loot_pool()
    b = empty_reward_bundle(source="boss_spoils")
    reward_bundle_add_coins(b, gp=12)
    reward_bundle_add_item(b, {"name": "Ruby Ring", "kind": "treasure", "gp_value": 75})

    calls = []
    real = reward_bundle_mod.grant_coin_reward

    def _spy(game_obj, amount_gp, **kwargs):
        calls.append((int(amount_gp), dict(kwargs)))
        return real(game_obj, amount_gp, **kwargs)

    monkeypatch.setattr(reward_bundle_mod, "grant_coin_reward", _spy)

    result = apply_reward_bundle(game, bundle=b, loot_pool=pool, coin_policy="treasury")

    assert result.coins_gp_awarded == 12
    assert len(calls) == 1
    assert calls[0][0] == 12
    assert calls[0][1].get("source") == "boss_spoils"
    assert game.gold == 12
    assert len(pool.entries) == 1
