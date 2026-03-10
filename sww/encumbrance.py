"""Hybrid encumbrance: slot system with coin abstraction (P4.11).

Design goals:
- Deterministic, content-light (no new content tables required)
- Works with existing party-wide inventory model (party_items + party gold + shared supplies)
- Provides meaningful pressure via movement gating when overloaded

Model (v1):
- Each actor contributes a number of carry *slots* based on STR.
- Party encumbrance is computed as total slots used vs total slots available.
- Coins: 100 gp per slot (rounded up).
- Supplies: stack-per-slot abstractions (rations, torches, ammo).
- Party loot items: default 1 slot, with heavier categories derived from equipment weights.

This is deliberately simple and can be expanded later (pack animals, containers, per-PC loadouts).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Iterable, Optional, Tuple


@dataclass(frozen=True)
class Encumbrance:
    used_slots: int
    capacity_slots: int
    state: str  # "light" | "heavy" | "overloaded"
    heavy_threshold: int
    overloaded_threshold: int


def slots_for_str(str_score: int) -> int:
    """Return carry slots contributed by an actor.

    Simple OSR-ish bands.
    """
    s = int(str_score or 0)
    if s <= 5:
        return 6
    if s <= 8:
        return 8
    if s <= 12:
        return 10
    if s <= 15:
        return 12
    if s <= 17:
        return 14
    return 16


def _weight_to_slots(weight_lb: int) -> int:
    # 1 slot per 5 lb, min 1, cap at 6 to avoid silly outliers.
    w = max(0, int(weight_lb or 0))
    return max(1, min(6, int(math.ceil(w / 5.0)) if w > 0 else 1))


def coins_to_slots(gp: int, *, coins_per_slot: int = 100) -> int:
    g = max(0, int(gp or 0))
    if g <= 0:
        return 0
    cps = max(1, int(coins_per_slot))
    return int(math.ceil(g / float(cps)))


def stacked_to_slots(count: int, *, per_slot: int) -> int:
    c = max(0, int(count or 0))
    if c <= 0:
        return 0
    ps = max(1, int(per_slot))
    return int(math.ceil(c / float(ps)))


def item_to_slots(it: Dict[str, Any], *, equipdb: Any = None, strip_plus_suffix=None) -> int:
    """Estimate slots for a party inventory item dict."""
    if not isinstance(it, dict):
        return 1
    kind = str(it.get("kind") or "").strip().lower()
    name = str(it.get("true_name") or it.get("name") or "").strip()
    base = strip_plus_suffix(name) if callable(strip_plus_suffix) else name

    # Small valuables: gems/jewelry are effectively negligible in slot play.
    if kind in {"gem", "jewelry"}:
        return 0
    nml = name.lower()
    if "gem" in nml or "jewelry" in nml:
        return 0

    # Equipment-derived weights (preferred)
    if equipdb is not None:
        try:
            if kind == "weapon" or base in getattr(equipdb, "melee", {}) or base in getattr(equipdb, "missile", {}):
                w = equipdb.get_weapon(base)
                return _weight_to_slots(int(getattr(w, "weight_lb", 0) or 0))
        except Exception:
            pass
        try:
            if kind == "armor" or base in getattr(equipdb, "armors", {}):
                a = equipdb.get_armor(base)
                return _weight_to_slots(int(getattr(a, "weight_lb", 0) or 0))
        except Exception:
            pass

    # Magic items / consumables
    if kind in {"potion", "scroll", "ring"}:
        return 1
    if kind in {"wand", "staff"}:
        return 2

    # Default catch-all
    return 1


def compute_party_encumbrance(
    *,
    members: Iterable[Any],
    gp: int,
    rations: int,
    torches: int,
    ammo: Dict[str, int],
    party_items: Iterable[Dict[str, Any]],
    equipdb: Any = None,
    strip_plus_suffix=None,
    coins_per_slot: int = 100,
) -> Encumbrance:
    cap = 0
    for m in list(members or []):
        if int(getattr(m, "hp", 1) or 0) <= 0:
            # dead bodies don't carry
            continue
        st = getattr(m, "stats", None)
        s = int(getattr(st, "STR", 10) if st is not None else 10)
        cap += slots_for_str(s)

    used = 0
    used += coins_to_slots(gp, coins_per_slot=coins_per_slot)
    # Supplies
    used += stacked_to_slots(rations, per_slot=5)
    used += stacked_to_slots(torches, per_slot=6)

    # Ammo stacks
    # Common stacks based on shop pack sizes.
    per_slot_map = {
        "arrows": 20,
        "bolts": 20,
        "stones": 20,
        "hand_axes": 5,
        "throwing_daggers": 5,
        "darts": 10,
        "javelins": 5,
        "throwing_spears": 5,
    }
    for k, cnt in dict(ammo or {}).items():
        ps = per_slot_map.get(str(k), 20)
        used += stacked_to_slots(int(cnt or 0), per_slot=int(ps))

    # Inventory items
    for it in list(party_items or []):
        used += int(item_to_slots(it, equipdb=equipdb, strip_plus_suffix=strip_plus_suffix) or 0)

    # Determine thresholds
    cap = max(0, int(cap))
    # Allow a small buffer to represent awkward packing before full overload.
    heavy_threshold = cap
    overload_buffer = max(2, cap // 4)
    overloaded_threshold = cap + overload_buffer

    if cap <= 0:
        state = "overloaded" if used > 0 else "light"
    elif used <= heavy_threshold:
        state = "light"
    elif used <= overloaded_threshold:
        state = "heavy"
    else:
        state = "overloaded"

    return Encumbrance(
        used_slots=int(used),
        capacity_slots=int(cap),
        state=str(state),
        heavy_threshold=int(heavy_threshold),
        overloaded_threshold=int(overloaded_threshold),
    )
