from sww.models import Actor, CharacterEquipment, CharacterInventory, ItemInstance
from sww.save_load import actor_to_dict, dict_to_actor, migrate_save_data


def test_actor_roundtrip_persists_new_inventory_and_equipment_fields():
    a = Actor(name="Rurik", hp=5, hp_max=5, ac_desc=7, hd=1, save=15)
    a.inventory = CharacterInventory(
        items=[
            ItemInstance(
                item_id="weapon.longsword",
                instance_id="itm-1",
                name="Longsword",
                quantity=1,
                weight_lb=3.0,
                kind="weapon",
            )
        ],
        coins_gp=42,
        capacity_lb=60.0,
    )
    a.equipment = CharacterEquipment(main_hand="weapon.longsword", armor="armor.chainmail")

    payload = actor_to_dict(a)
    out = dict_to_actor(payload)

    assert out.inventory.coins_gp == 42
    assert out.inventory.capacity_lb == 60.0
    assert len(out.inventory.items) == 1
    assert out.inventory.items[0].item_id == "weapon.longsword"
    assert out.equipment.main_hand == "weapon.longsword"
    assert out.equipment.armor == "armor.chainmail"


def test_migrate_11_to_12_seeds_new_fields_from_legacy_equipment():
    save_v11 = {
        "save_version": 11,
        "version": 11,
        "campaign": {
            "party": {
                "members": [
                    {
                        "name": "Ela",
                        "hp": 4,
                        "hp_max": 4,
                        "ac_desc": 8,
                        "hd": 1,
                        "save": 15,
                        "weapon": "Sword",
                        "armor": "Leather",
                        "shield": True,
                        "shield_name": "Shield",
                    }
                ]
            }
        },
    }

    out = migrate_save_data(save_v11)
    member = out["campaign"]["party"]["members"][0]

    assert out["save_version"] == 12
    assert "inventory" in member
    assert member["equipment"]["main_hand"] == "Sword"
    assert member["equipment"]["armor"] == "Leather"
    assert member["equipment"]["shield"] == "Shield"
