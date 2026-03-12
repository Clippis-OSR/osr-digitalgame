from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

CoinRewardPolicy = Literal["treasury", "expedition_pool", "reward_object"]


@dataclass(frozen=True)
class CoinRewardResult:
    amount_gp: int
    policy: CoinRewardPolicy


def reward_coin_policy(*, source: str = "", explicit: str | None = None) -> CoinRewardPolicy:
    """Resolve where a coin reward should be routed.

    Transitional policy notes:
    - We still treat party gold as the canonical treasury for save compatibility.
    - Loot-pool coin routing is available for expedition ownership-aware flows.
    - Reward-object routing is available for deferred claim surfaces.
    """
    if explicit in {"treasury", "expedition_pool", "reward_object"}:
        return explicit  # type: ignore[return-value]

    normalized = str(source or "").strip().lower()
    # Keep legacy behavior stable by default. Call sites can opt in explicitly.
    if normalized.startswith("loot_pool") or normalized.startswith("expedition_pool"):
        return "expedition_pool"
    if normalized.startswith("reward_object"):
        return "reward_object"
    return "treasury"


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
    policy: str | None = None,
    reward_obj: dict[str, Any] | None = None,
) -> CoinRewardResult:
    """Route a coin reward through one explicit policy decision.

    We intentionally do not split coin rewards to per-actor purses yet.
    The campaign economy and save format still model a shared treasury, while
    ownership-aware item flows continue to migrate through the loot pool.
    """
    amount = int(amount_gp or 0)
    chosen = reward_coin_policy(source=source, explicit=policy)
    if amount <= 0:
        return CoinRewardResult(amount_gp=0, policy=chosen)

    if chosen == "treasury":
        deposit_coins_to_treasury(game, amount)
    elif chosen == "expedition_pool":
        add_coins_to_reward_pool(getattr(game, "loot_pool"), amount)
    elif chosen == "reward_object":
        target = reward_obj if isinstance(reward_obj, dict) else {}
        target["coins_gp"] = int(target.get("coins_gp", 0) or 0) + amount
    return CoinRewardResult(amount_gp=amount, policy=chosen)
