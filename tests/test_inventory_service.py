from sww.inventory_service import (
    add_item_to_actor,
    remove_item_from_actor,
    equip_item_on_actor,
    unequip_item_on_actor,
    find_item_on_actor,
    give_item_between_actors,
    actor_equipped_weapon,
)
from sww.models import Actor, ItemInstance


def _actor(name: str) -> Actor:
    return Actor(name=name, hp=5, hp_max=5, ac_desc=8, hd=1, save=15)


def test_add_and_remove_item():
    a = _actor("A")
    it = ItemInstance(instance_id="i1", template_id="weapon.sword", name="Sword", category="weapon", quantity=2)
    assert add_item_to_actor(a, it).ok

    assert find_item_on_actor(a, "i1") is not None
    res = remove_item_from_actor(a, "i1", quantity=1)
    assert res.ok
    assert find_item_on_actor(a, "i1").quantity == 1


def test_equip_and_unequip_item_syncs_legacy_fields():
    a = _actor("A")
    it = ItemInstance(instance_id="i1", template_id="weapon.sword", name="Sword", category="weapon")
    add_item_to_actor(a, it)

    eq = equip_item_on_actor(a, "i1")
    assert eq.ok
    assert a.equipment.main_hand == "i1"
    assert actor_equipped_weapon(a) == "Sword"
    # transitional legacy sync
    assert a.weapon == "i1"

    uq = unequip_item_on_actor(a, "main_hand")
    assert uq.ok
    assert a.equipment.main_hand is None


def test_give_item_between_actors_moves_quantity():
    src = _actor("Src")
    dst = _actor("Dst")
    it = ItemInstance(instance_id="i1", template_id="ammo.arrows", name="Arrows", category="ammo", quantity=5)
    add_item_to_actor(src, it)

    res = give_item_between_actors(src, dst, "i1", quantity=2)
    assert res.ok
    assert find_item_on_actor(src, "i1").quantity == 3
    moved = [x for x in dst.inventory.items if x.template_id == "ammo.arrows"][0]
    assert moved.quantity == 2


def test_invalid_equip_fails_cleanly_when_item_missing():
    a = _actor("A")
    res = equip_item_on_actor(a, "missing")
    assert not res.ok
    assert "must be in inventory" in str(res.error or "")


def test_cursed_sticky_item_blocks_normal_unequip_and_reveals_curse():
    a = _actor("A")
    it = ItemInstance(
        instance_id="c1",
        template_id="weapon.cursed_blade",
        name="Runed Sword",
        category="weapon",
        identified=False,
        cursed_known=False,
        metadata={"cursed": True, "effects": [{"type": "sticky_equip", "value": True}]},
    )
    add_item_to_actor(a, it)

    eq = equip_item_on_actor(a, "c1")
    assert eq.ok

    uq = unequip_item_on_actor(a, "main_hand")
    assert not uq.ok
    assert "cannot be unequipped" in str(uq.error or "")
    assert find_item_on_actor(a, "c1").cursed_known is True


def test_non_cursed_item_still_unequips_normally():
    a = _actor("A")
    it = ItemInstance(instance_id="n1", template_id="weapon.sword", name="Sword", category="weapon")
    add_item_to_actor(a, it)
    assert equip_item_on_actor(a, "n1").ok

    uq = unequip_item_on_actor(a, "main_hand")
    assert uq.ok
    assert a.equipment.main_hand is None
