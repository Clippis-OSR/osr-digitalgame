from sww.item_templates import build_item_instance
from sww.models import Actor
from sww.save_load import actor_from_dict, actor_to_dict
from sww.town_services import identify_item_in_town


def test_identify_item_in_town_succeeds_with_enough_gold():
    a = Actor(name="Iris", hp=5, hp_max=5, ac_desc=8, hd=1, save=15, is_pc=True)
    inst = build_item_instance("potion.potion_healing", identified=False)
    a.inventory.items.append(inst)

    res = identify_item_in_town(a, inst.instance_id, party_gold=30)

    assert res.ok is True
    assert res.party_gold_after == 15
    assert a.inventory.items[0].identified is True
    assert a.inventory.items[0].name == "Potion of Healing"


def test_identify_item_in_town_fails_without_enough_gold():
    a = Actor(name="Iris", hp=5, hp_max=5, ac_desc=8, hd=1, save=15, is_pc=True)
    inst = build_item_instance("ring.ring_protection_plus1", identified=False)
    a.inventory.items.append(inst)

    res = identify_item_in_town(a, inst.instance_id, party_gold=10)

    assert res.ok is False
    assert res.error == "not enough gold"
    assert a.inventory.items[0].identified is False


def test_identified_state_persists_after_save_load_roundtrip():
    a = Actor(name="Iris", hp=5, hp_max=5, ac_desc=8, hd=1, save=15, is_pc=True)
    inst = build_item_instance("potion.potion_healing", identified=False)
    a.inventory.items.append(inst)

    res = identify_item_in_town(a, inst.instance_id, party_gold=30)
    assert res.ok

    payload = actor_to_dict(a)
    out = actor_from_dict(payload)

    assert out.inventory.items[0].identified is True
    assert out.inventory.items[0].name == "Potion of Healing"
