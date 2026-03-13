from sww.inventory_service import (
    add_item_to_actor,
    remove_item_from_actor,
    equip_item_on_actor,
    unequip_item_on_actor,
    find_item_on_actor,
    give_item_between_actors,
    actor_equipped_weapon,
    actor_worn_magic_by_category,
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


def test_equip_and_unequip_item_keeps_runtime_state_in_new_equipment_only():
    a = _actor("A")
    it = ItemInstance(instance_id="i1", template_id="weapon.sword", name="Sword", category="weapon")
    add_item_to_actor(a, it)

    eq = equip_item_on_actor(a, "i1")
    assert eq.ok
    assert a.equipment.main_hand == "i1"
    assert actor_equipped_weapon(a) == "Sword"
    # legacy mirror is no longer auto-synced at runtime
    assert a.weapon is None

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


def test_actor_can_wear_two_rings_but_third_fails_cleanly():
    a = _actor("A")
    r1 = ItemInstance(instance_id="r1", template_id="ring.r1", name="Ring of Red", category="ring", metadata={"wear_category": "ring"})
    r2 = ItemInstance(instance_id="r2", template_id="ring.r2", name="Ring of Blue", category="ring", metadata={"wear_category": "ring"})
    r3 = ItemInstance(instance_id="r3", template_id="ring.r3", name="Ring of Green", category="ring", metadata={"wear_category": "ring"})
    add_item_to_actor(a, r1)
    add_item_to_actor(a, r2)
    add_item_to_actor(a, r3)

    assert equip_item_on_actor(a, "r1").ok
    assert equip_item_on_actor(a, "r2").ok
    third = equip_item_on_actor(a, "r3")
    assert not third.ok
    assert "cannot wear more ring items" in str(third.error or "")
    worn = actor_worn_magic_by_category(a)
    assert worn.get("ring") == 2


def test_only_one_cloak_allowed():
    a = _actor("A")
    c1 = ItemInstance(instance_id="c1", template_id="wondrous.c1", name="Cloak of Shadows", category="cloak", metadata={"wear_category": "cloak"})
    c2 = ItemInstance(instance_id="c2", template_id="wondrous.c2", name="Cloak of Feathers", category="cloak", metadata={"wear_category": "cloak"})
    add_item_to_actor(a, c1)
    add_item_to_actor(a, c2)

    assert equip_item_on_actor(a, "c1").ok
    second = equip_item_on_actor(a, "c2")
    assert not second.ok
    assert "cannot wear more cloak items" in str(second.error or "")


def test_mundane_equipment_still_behaves_normally():
    a = _actor("A")
    w = ItemInstance(instance_id="w1", template_id="weapon.sword", name="Sword", category="weapon")
    add_item_to_actor(a, w)

    eq = equip_item_on_actor(a, "w1")
    assert eq.ok
    assert a.equipment.main_hand == "w1"
