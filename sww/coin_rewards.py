from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final


class CoinDestination:
    """Explicit destinations for coin reward settlement.

    - TREASURY: shared campaign treasury (`game.gold`)
    - EXPEDITION_POOL: temporary expedition/loot pool (`loot_pool.coins_gp`)
    - IMMEDIATE_COMPATIBILITY_DEFAULT: transitional alias to TREASURY for
      legacy-stable behavior while call sites migrate.
    """

    TREASURY: Final[str] = "treasury"
    EXPEDITION_POOL: Final[str] = "expedition_pool"
    IMMEDIATE_COMPATIBILITY_DEFAULT: Final[str] = "immediate_compatibility_default"


VALID_COIN_DESTINATIONS: Final[set[str]] = {
    CoinDestination.TREASURY,
    CoinDestination.EXPEDITION_POOL,
    CoinDestination.IMMEDIATE_COMPATIBILITY_DEFAULT,
}


@dataclass(frozen=True)
class CoinRewardResult:
    amount_gp: int
    destination: str


def reward_coin_destination(*, source: str = "", explicit: str | None = None) -> str:
    """Resolve destination policy for a coin reward.

    Compatibility default intentionally resolves to shared treasury because save
    and economy semantics still treat party gold as canonical.
    """
    if explicit in VALID_COIN_DESTINATIONS:
        return str(explicit)

    # Backward-compatible alias accepted during migration from old callers.
    if explicit == "reward_object":
        return CoinDestination.IMMEDIATE_COMPATIBILITY_DEFAULT

    normalized = str(source or "").strip().lower()
    if normalized.startswith("loot_pool") or normalized.startswith("expedition_pool"):
        return CoinDestination.EXPEDITION_POOL
    return CoinDestination.IMMEDIATE_COMPATIBILITY_DEFAULT


def deposit_coins_to_treasury(game: Any, amount_gp: int) -> int:
    amount = int(amount_gp or 0)
    if amount <= 0:
        return 0
    game.gold = int(getattr(game, "gold", 0) or 0) + amount
    return amount


def add_coins_to_reward_pool(loot_pool: Any, amount_gp: int) -> int:
    amount = int(amount_gp or 0)
    if amount <= 0:
        return 0
    loot_pool.coins_gp = int(getattr(loot_pool, "coins_gp", 0) or 0) + amount
    return amount


def grant_coin_reward(
    game: Any,
    amount_gp: int,
    *,
    source: str = "",
    destination: str | None = None,
    policy: str | None = None,
    reward_obj: dict[str, Any] | None = None,
) -> CoinRewardResult:
    """Route coin rewards through an explicit destination policy.

    `destination` is the preferred explicit argument.
    `policy` is retained as a backward-compatible alias.
    """
    amount = int(amount_gp or 0)
    chosen = reward_coin_destination(source=source, explicit=(destination or policy))
    if amount <= 0:
        return CoinRewardResult(amount_gp=0, destination=chosen)

    # Compatibility-default intentionally maps to treasury.
    if chosen in {CoinDestination.TREASURY, CoinDestination.IMMEDIATE_COMPATIBILITY_DEFAULT}:
        deposit_coins_to_treasury(game, amount)
    elif chosen == CoinDestination.EXPEDITION_POOL:
        add_coins_to_reward_pool(getattr(game, "loot_pool"), amount)

    # Retained for callers that pass a reward object; coin handling still remains
    # centralized by default in this transition phase.
    if isinstance(reward_obj, dict):
        reward_obj.setdefault("coins_gp", int(reward_obj.get("coins_gp", 0) or 0))

    return CoinRewardResult(amount_gp=amount, destination=chosen)
