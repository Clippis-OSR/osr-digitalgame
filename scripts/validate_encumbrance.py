"""Validate hybrid encumbrance math.

Run: python -m scripts.validate_encumbrance
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Stats:
    STR: int = 10


@dataclass
class _Actor:
    name: str
    stats: _Stats
    hp: int = 5


def main() -> None:
    from sww.encumbrance import compute_party_encumbrance

    party = [_Actor("A", _Stats(STR=9)), _Actor("B", _Stats(STR=13))]
    # Capacity: 10 + 12 = 22
    e = compute_party_encumbrance(
        members=party,
        gp=0,
        rations=0,
        torches=0,
        ammo={},
        party_items=[],
        equipdb=None,
        strip_plus_suffix=None,
    )
    assert e.capacity_slots == 22
    assert e.used_slots == 0
    assert e.state == "light"

    # Coins: 100gp/slot
    e2 = compute_party_encumbrance(
        members=party,
        gp=101,
        rations=0,
        torches=0,
        ammo={},
        party_items=[],
        equipdb=None,
        strip_plus_suffix=None,
    )
    assert e2.used_slots == 2

    # Supplies: rations 5/slot, torches 6/slot
    e3 = compute_party_encumbrance(
        members=party,
        gp=0,
        rations=6,
        torches=7,
        ammo={},
        party_items=[],
        equipdb=None,
        strip_plus_suffix=None,
    )
    assert e3.used_slots == 2 + 2  # rations:2, torches:2

    # Overload threshold triggers state
    big = compute_party_encumbrance(
        members=party,
        gp=0,
        rations=0,
        torches=0,
        ammo={},
        party_items=[{"name": f"Loot{i}", "kind": "item"} for i in range(40)],
        equipdb=None,
        strip_plus_suffix=None,
    )
    assert big.used_slots == 40
    assert big.state in {"heavy", "overloaded"}

    print("validate_encumbrance: OK")


if __name__ == "__main__":
    main()
