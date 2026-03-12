from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .coin_rewards import grant_coin_reward
from .loot_pool import LootPool, LootEntry, add_generated_treasure_to_pool


@dataclass
class RewardBundle:
    """Transitional, JSON-friendly mixed reward container.

    Keeps coin and item rewards together until final routing:
    - coins route through centralized coin policy helpers
    - items route through loot-pool ownership flow
    """

    coins_gp: int = 0
    coins_sp: int = 0
    coins_cp: int = 0
    items: list[Any] = field(default_factory=list)
    source: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "coins_gp": int(self.coins_gp or 0),
            "coins_sp": int(self.coins_sp or 0),
            "coins_cp": int(self.coins_cp or 0),
            "items": list(self.items or []),
            "source": str(self.source or ""),
            "context": dict(self.context or {}),
        }


@dataclass(frozen=True)
class RewardBundleApplyResult:
    coins_gp_awarded: int
    items_added: list[LootEntry]


def empty_reward_bundle(*, source: str = "", context: dict[str, Any] | None = None) -> RewardBundle:
    return RewardBundle(source=str(source or ""), context=dict(context or {}))


def reward_bundle_add_coins(bundle: RewardBundle, *, gp: int = 0, sp: int = 0, cp: int = 0) -> RewardBundle:
    bundle.coins_gp = int(bundle.coins_gp or 0) + int(gp or 0)
    bundle.coins_sp = int(bundle.coins_sp or 0) + int(sp or 0)
    bundle.coins_cp = int(bundle.coins_cp or 0) + int(cp or 0)
    return bundle


def reward_bundle_add_item(bundle: RewardBundle, item: Any) -> RewardBundle:
    bundle.items.append(item)
    return bundle


def reward_bundle_is_empty(bundle: RewardBundle) -> bool:
    return (
        int(bundle.coins_gp or 0) <= 0
        and int(bundle.coins_sp or 0) <= 0
        and int(bundle.coins_cp or 0) <= 0
        and len(list(bundle.items or [])) == 0
    )


def _bundle_coins_to_gp_floor(bundle: RewardBundle) -> int:
    total_cp = int(bundle.coins_gp or 0) * 100 + int(bundle.coins_sp or 0) * 10 + int(bundle.coins_cp or 0)
    return max(0, int(total_cp // 100))


def apply_reward_bundle(
    game: Any,
    *,
    bundle: RewardBundle,
    loot_pool: LootPool,
    coin_policy: str = "treasury",
    identify_magic: bool = False,
) -> RewardBundleApplyResult:
    """Apply a mixed reward through centralized coin + loot-pool item flows.

    Currency is normalized to whole GP for current economy compatibility.
    """
    if reward_bundle_is_empty(bundle):
        return RewardBundleApplyResult(coins_gp_awarded=0, items_added=[])

    coins_gp = _bundle_coins_to_gp_floor(bundle)
    if coins_gp > 0:
        grant_coin_reward(game, coins_gp, source=str(bundle.source or "reward_bundle"), policy=str(coin_policy or "treasury"))

    _gp_ignored, added = add_generated_treasure_to_pool(
        loot_pool,
        gp=0,
        items=list(bundle.items or []),
        identify_magic=bool(identify_magic),
    )
    return RewardBundleApplyResult(coins_gp_awarded=coins_gp, items_added=list(added or []))
