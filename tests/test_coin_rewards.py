from types import SimpleNamespace

from sww.coin_rewards import (
    add_coins_to_reward_pool,
    deposit_coins_to_treasury,
    grant_coin_reward,
    reward_coin_policy,
)


def test_reward_coin_policy_defaults_to_treasury_for_stability():
    assert reward_coin_policy(source="dungeon_feature") == "treasury"


def test_deposit_coins_to_treasury_updates_gold():
    game = SimpleNamespace(gold=12)
    assert deposit_coins_to_treasury(game, 8) == 8
    assert game.gold == 20


def test_add_coins_to_reward_pool_updates_pool():
    pool = SimpleNamespace(coins_gp=5)
    assert add_coins_to_reward_pool(pool, 7) == 7
    assert pool.coins_gp == 12


def test_grant_coin_reward_supports_reward_object_policy():
    game = SimpleNamespace(gold=0, loot_pool=SimpleNamespace(coins_gp=0))
    reward = {"coins_gp": 4}
    result = grant_coin_reward(game, 6, source="reward_object:test", reward_obj=reward)
    assert result.policy == "reward_object"
    assert reward["coins_gp"] == 10
    assert game.gold == 0


def test_grant_coin_reward_can_route_to_expedition_pool():
    game = SimpleNamespace(gold=1, loot_pool=SimpleNamespace(coins_gp=2))
    result = grant_coin_reward(game, 9, source="loot_pool:encounter")
    assert result.policy == "expedition_pool"
    assert game.gold == 1
    assert game.loot_pool.coins_gp == 11
