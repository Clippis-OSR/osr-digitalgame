from dataclasses import dataclass

from sww.encumbrance import (
    actor_carried_weight_lb,
    actor_movement_from_weight,
    compute_party_movement,
)
from sww.models import Actor, CharacterEquipment, CharacterInventory


@dataclass
class _W:
    weight_lb: float


class _DummyEquipDB:
    def __init__(self):
        self._weapons = {"Sword": _W(3.0), "Spear": _W(6.0)}
        self._armors = {"Leather": _W(15.0), "Shield": _W(10.0)}

    def get_weapon(self, name: str):
        return self._weapons[name]

    def get_armor(self, name: str):
        return self._armors[name]


def test_weapon_and_armor_contribute_to_actor_weight():
    a = Actor(name="A", hp=5, hp_max=5, ac_desc=8, hd=1, save=15)
    a.equipment = CharacterEquipment(main_hand="Sword", armor="Leather", shield="Shield")

    w = actor_carried_weight_lb(a, equipdb=_DummyEquipDB())
    assert w == 28.0


def test_coins_contribute_weight():
    a = Actor(name="A", hp=5, hp_max=5, ac_desc=8, hd=1, save=15)
    a.inventory = CharacterInventory(coins_gp=100)

    w = actor_carried_weight_lb(a, equipdb=_DummyEquipDB())
    assert w == 10.0


def test_overloaded_actor_moves_slower():
    light = actor_movement_from_weight(20)
    heavy = actor_movement_from_weight(170)

    assert heavy.movement_rate < light.movement_rate
    assert heavy.state == "overloaded"


def test_party_movement_uses_slowest_member():
    equipdb = _DummyEquipDB()

    a = Actor(name="Fast", hp=5, hp_max=5, ac_desc=8, hd=1, save=15)
    a.inventory = CharacterInventory(coins_gp=0)

    b = Actor(name="Slow", hp=5, hp_max=5, ac_desc=8, hd=1, save=15)
    b.inventory = CharacterInventory(coins_gp=300)  # 30 lb => slower than Fast baseline

    party = compute_party_movement(members=[a, b], equipdb=equipdb)
    b_res = actor_movement_from_weight(actor_carried_weight_lb(b, equipdb=equipdb))

    assert party.movement_rate == b_res.movement_rate
