"""Per-character, weight-based encumbrance with compatibility wrappers.

This module replaces the old party-slot abstraction with actor-level carried
weight calculations.  During migration we keep `compute_party_encumbrance(...)`
and the `Encumbrance` return shape so existing callers that only check
`enc.state` continue to work.

Assumptions (centralized and tunable):
- Coins: 10 coins = 1 lb (classic D&D convention).
- Torches: 1 lb each.
- Rations: 2 lb each.
- Ammo: light abstract defaults by ammo kind if exact item data is unavailable.
- Movement bands use simple old-school friendly thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Any, Dict, Iterable, Optional


# -----------------------------
# Tunable assumptions
# -----------------------------
COINS_PER_LB = 10.0
TORCH_WEIGHT_LB = 1.0
RATION_WEIGHT_LB = 2.0

AMMO_WEIGHT_LB_BY_KIND: dict[str, float] = {
    "arrows": 0.10,
    "bolts": 0.10,
    "stones": 0.10,
    "darts": 0.10,
    "javelins": 2.0,
    "throwing_spears": 3.0,
    "hand_axes": 3.0,
    "throwing_daggers": 1.0,
}

# Upper bound by band, state, movement rate (ft/turn style abstraction).
ENCUMBRANCE_BANDS: list[tuple[float, str, int]] = [
    (35.0, "unencumbered", 120),
    (70.0, "light", 90),
    (105.0, "heavy", 60),
    (150.0, "severe", 30),
]
OVERLOADED_MOVEMENT = 0


@dataclass(frozen=True)
class EncumbranceResult:
    """Actor-level weight and movement result."""

    carried_weight_lb: float
    movement_rate: int
    state: str


@dataclass(frozen=True)
class Encumbrance:
    """Compatibility result shape expected by existing systems.

    Legacy fields (`used_slots`, `capacity_slots`, thresholds) are now derived
    approximations from pounds so older UI/checks do not break while callers
    migrate to `EncumbranceResult`-oriented APIs.
    """

    used_slots: int
    capacity_slots: int
    state: str
    heavy_threshold: int
    overloaded_threshold: int
    movement_rate: int = 0
    carried_weight_lb: float = 0.0
    per_actor: dict[str, EncumbranceResult] = field(default_factory=dict)


def item_weight_lb(it: Dict[str, Any], *, equipdb: Any = None, strip_plus_suffix=None) -> float:
    """Return an item's weight in pounds from explicit value or equipment DB."""
    if not isinstance(it, dict):
        return 0.0
    try:
        explicit = it.get("weight_lb", None)
        if explicit is not None:
            return max(0.0, float(explicit))
    except Exception:
        pass

    kind = str(it.get("kind") or it.get("category") or "").strip().lower()
    name = str(it.get("true_name") or it.get("name") or "").strip()
    base = strip_plus_suffix(name) if callable(strip_plus_suffix) else name

    if equipdb is not None:
        try:
            if kind == "weapon" or base in getattr(equipdb, "melee", {}) or base in getattr(equipdb, "missile", {}):
                return max(0.0, float(getattr(equipdb.get_weapon(base), "weight_lb", 0.0) or 0.0))
        except Exception:
            pass
        try:
            if kind == "armor" or base in getattr(equipdb, "armors", {}):
                return max(0.0, float(getattr(equipdb.get_armor(base), "weight_lb", 0.0) or 0.0))
        except Exception:
            pass

    # Fallback defaults for unknown misc items.
    if kind in {"potion", "scroll"}:
        return 0.1
    if kind in {"ring", "gem", "jewelry"}:
        return 0.0
    if kind in {"wand", "rod"}:
        return 1.0
    if kind == "staff":
        return 4.0
    return 1.0


def _lookup_equipped_weight_lb(actor: Any, *, equipdb: Any = None, strip_plus_suffix=None) -> float:
    total = 0.0
    eq = getattr(actor, "equipment", None)

    def _weapon_w(name: str | None) -> float:
        if not name or equipdb is None:
            return 0.0
        base = strip_plus_suffix(name) if callable(strip_plus_suffix) else str(name)
        try:
            return max(0.0, float(getattr(equipdb.get_weapon(base), "weight_lb", 0.0) or 0.0))
        except Exception:
            return 0.0

    def _armor_w(name: str | None) -> float:
        if not name or equipdb is None:
            return 0.0
        base = strip_plus_suffix(name) if callable(strip_plus_suffix) else str(name)
        try:
            return max(0.0, float(getattr(equipdb.get_armor(base), "weight_lb", 0.0) or 0.0))
        except Exception:
            return 0.0

    if eq is not None:
        total += _armor_w(getattr(eq, "armor", None))
        # Shields are in armor DB in current extracted data.
        total += _armor_w(getattr(eq, "shield", None))
        total += _weapon_w(getattr(eq, "main_hand", None))
        total += _weapon_w(getattr(eq, "off_hand", None))
        total += _weapon_w(getattr(eq, "missile_weapon", None))
    else:
        # Legacy fallback path.
        total += _weapon_w(getattr(actor, "weapon", None))
        total += _armor_w(getattr(actor, "armor", None))
        if bool(getattr(actor, "shield", False)):
            total += _armor_w(getattr(actor, "shield_name", None) or "Shield")

    return total


def actor_carried_weight_lb(actor: Any, *, equipdb: Any = None, strip_plus_suffix=None) -> float:
    """Compute carried pounds for one actor (equipment + inventory + supplies)."""
    total = 0.0
    total += _lookup_equipped_weight_lb(actor, equipdb=equipdb, strip_plus_suffix=strip_plus_suffix)

    inv = getattr(actor, "inventory", None)
    if inv is not None:
        for it in list(getattr(inv, "items", []) or []):
            if isinstance(it, dict):
                qty = int(it.get("quantity", 1) or 1)
                total += item_weight_lb(it, equipdb=equipdb, strip_plus_suffix=strip_plus_suffix) * max(0, qty)
            else:
                qty = int(getattr(it, "quantity", 1) or 1)
                per_item = item_weight_lb(
                    {
                        "name": getattr(it, "name", ""),
                        "category": getattr(it, "category", "misc"),
                        "weight_lb": getattr(it, "weight_lb", None),
                    },
                    equipdb=equipdb,
                    strip_plus_suffix=strip_plus_suffix,
                )
                total += per_item * max(0, qty)

        gp = int(getattr(inv, "coins_gp", 0) or 0)
        sp = int(getattr(inv, "coins_sp", 0) or 0)
        cp = int(getattr(inv, "coins_cp", 0) or 0)
        total += max(0, gp + sp + cp) / COINS_PER_LB

        total += max(0, int(getattr(inv, "torches", 0) or 0)) * TORCH_WEIGHT_LB
        total += max(0, int(getattr(inv, "rations", 0) or 0)) * RATION_WEIGHT_LB

        ammo = dict(getattr(inv, "ammo", {}) or {})
        for k, cnt in ammo.items():
            kind = str(k).strip().lower()
            total += max(0, int(cnt or 0)) * float(AMMO_WEIGHT_LB_BY_KIND.get(kind, 0.10))

    return float(round(total, 3))


def actor_movement_from_weight(carried_weight_lb: float) -> EncumbranceResult:
    """Map carried weight to movement rate/state using centralized bands."""
    w = max(0.0, float(carried_weight_lb or 0.0))
    for upper, state, mv in ENCUMBRANCE_BANDS:
        if w <= upper:
            return EncumbranceResult(carried_weight_lb=w, movement_rate=int(mv), state=str(state))
    return EncumbranceResult(carried_weight_lb=w, movement_rate=OVERLOADED_MOVEMENT, state="overloaded")


def compute_party_movement(
    *,
    members: Iterable[Any],
    equipdb: Any = None,
    strip_plus_suffix=None,
) -> EncumbranceResult:
    """Return expedition movement from the slowest living member."""
    results: list[EncumbranceResult] = []
    for m in list(members or []):
        if int(getattr(m, "hp", 1) or 0) <= 0:
            continue
        w = actor_carried_weight_lb(m, equipdb=equipdb, strip_plus_suffix=strip_plus_suffix)
        results.append(actor_movement_from_weight(w))
    if not results:
        return EncumbranceResult(carried_weight_lb=0.0, movement_rate=120, state="unencumbered")

    slowest = min(results, key=lambda r: (int(r.movement_rate), -float(r.carried_weight_lb)))
    return slowest


# -----------------------------
# Compatibility wrapper
# -----------------------------
def compute_party_encumbrance(
    *,
    members: Iterable[Any],
    gp: int = 0,
    rations: int = 0,
    torches: int = 0,
    ammo: Dict[str, int] | None = None,
    party_items: Iterable[Dict[str, Any]] | None = None,
    equipdb: Any = None,
    strip_plus_suffix=None,
    coins_per_slot: int = 100,
) -> Encumbrance:
    """Compatibility API for callers still importing the old function.

    Temporary behavior:
    - Computes per-actor carried weights and party movement from slowest member.
    - Legacy slot metrics are derived approximations (5 lb per pseudo-slot).
    - `gp/rations/torches/ammo/party_items` are kept for old call sites that still
      pass shared party supplies; these are distributed to the first living member.
    """
    living = [m for m in list(members or []) if int(getattr(m, "hp", 1) or 0) > 0]

    # Lightweight compatibility: if old shared resources are passed, attach their
    # weight to the first living member's computed load.
    extra_shared_lb = 0.0
    extra_shared_lb += max(0, int(gp or 0)) / COINS_PER_LB
    extra_shared_lb += max(0, int(rations or 0)) * RATION_WEIGHT_LB
    extra_shared_lb += max(0, int(torches or 0)) * TORCH_WEIGHT_LB
    for k, cnt in dict(ammo or {}).items():
        extra_shared_lb += max(0, int(cnt or 0)) * float(AMMO_WEIGHT_LB_BY_KIND.get(str(k).lower(), 0.10))
    for it in list(party_items or []):
        qty = int((it or {}).get("quantity", 1) or 1) if isinstance(it, dict) else 1
        extra_shared_lb += item_weight_lb(it if isinstance(it, dict) else {}, equipdb=equipdb, strip_plus_suffix=strip_plus_suffix) * max(0, qty)

    per_actor: dict[str, EncumbranceResult] = {}
    party_weights: list[float] = []
    for idx, m in enumerate(living):
        w = actor_carried_weight_lb(m, equipdb=equipdb, strip_plus_suffix=strip_plus_suffix)
        if idx == 0 and extra_shared_lb > 0:
            w += float(extra_shared_lb)
        res = actor_movement_from_weight(w)
        per_actor[str(getattr(m, "name", f"actor_{idx}"))] = res
        party_weights.append(w)

    if per_actor:
        slowest_name = min(per_actor, key=lambda n: (per_actor[n].movement_rate, -per_actor[n].carried_weight_lb))
        slowest = per_actor[slowest_name]
    else:
        slowest = EncumbranceResult(carried_weight_lb=0.0, movement_rate=120, state="unencumbered")

    total_weight = float(round(sum(party_weights), 3))
    used_slots = int(ceil(total_weight / 5.0)) if total_weight > 0 else 0
    capacity_slots = int(ceil((len(living) * ENCUMBRANCE_BANDS[2][0]) / 5.0)) if living else 0

    return Encumbrance(
        used_slots=used_slots,
        capacity_slots=capacity_slots,
        state=str(slowest.state),
        heavy_threshold=int(ceil((len(living) * ENCUMBRANCE_BANDS[1][0]) / 5.0)) if living else 0,
        overloaded_threshold=int(ceil((len(living) * ENCUMBRANCE_BANDS[-1][0]) / 5.0)) if living else 0,
        movement_rate=int(slowest.movement_rate),
        carried_weight_lb=total_weight,
        per_actor=per_actor,
    )
