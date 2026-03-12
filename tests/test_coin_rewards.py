from types import SimpleNamespace

from sww.coin_rewards import (
    CoinDestination,
    add_coins_to_reward_pool,
    deposit_coins_to_treasury,
    grant_coin_reward,
    reward_coin_destination,
)


def test_compatibility_default_destination_is_stable_treasury_path():
    assert reward_coin_destination(source="dungeon_feature") == CoinDestination.IMMEDIATE_COMPATIBILITY_DEFAULT


def test_treasury_policy_credits_treasury():
    game = SimpleNamespace(gold=12)
    result = grant_coin_reward(game, 8, destination=CoinDestination.TREASURY)
    assert result.destination == CoinDestination.TREASURY
    assert game.gold == 20


def test_deposit_coins_to_treasury_updates_gold():
    game = SimpleNamespace(gold=12)
    assert deposit_coins_to_treasury(game, 8) == 8
    assert game.gold == 20


def test_add_coins_to_reward_pool_updates_pool():
    pool = SimpleNamespace(coins_gp=5)
    assert add_coins_to_reward_pool(pool, 7) == 7
    assert pool.coins_gp == 12


def test_expedition_policy_credits_expedition_pool():
    game = SimpleNamespace(gold=1, loot_pool=SimpleNamespace(coins_gp=2))
    result = grant_coin_reward(game, 9, destination=CoinDestination.EXPEDITION_POOL)
    assert result.destination == CoinDestination.EXPEDITION_POOL
    assert game.gold == 1
    assert game.loot_pool.coins_gp == 11


def test_compatibility_default_credits_treasury():
    game = SimpleNamespace(gold=0, loot_pool=SimpleNamespace(coins_gp=0))
    result = grant_coin_reward(game, 6, destination=CoinDestination.IMMEDIATE_COMPATIBILITY_DEFAULT)
    assert result.destination == CoinDestination.IMMEDIATE_COMPATIBILITY_DEFAULT
    assert game.gold == 6
    assert game.loot_pool.coins_gp == 0
