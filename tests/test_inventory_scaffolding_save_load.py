from sww.models import Actor, CharacterEquipment, CharacterInventory, ItemInstance
from sww.save_load import actor_to_dict, dict_to_actor, migrate_save_data


def test_actor_roundtrip_persists_new_inventory_and_equipment_fields():
    a = Actor(name="Rurik", hp=5, hp_max=5, ac_desc=7, hd=1, save=15)
    a.inventory = CharacterInventory(
        items=[
            ItemInstance(
                instance_id="itm-1",
                template_id="weapon.longsword",
                name="Longsword",
                category="weapon",
                quantity=1,
                magic_bonus=1,
            )
        ],
        coins_gp=42,
        coins_sp=7,
        coins_cp=3,
        torches=2,
        rations=1,
        ammo={"arrows": 20},
    )
    a.equipment = CharacterEquipment(main_hand="Sword", armor="Chainmail", ammo_kind="arrows")

    payload = actor_to_dict(a)
    out = dict_to_actor(payload)

    assert out.inventory.coins_gp == 42
    assert out.inventory.coins_sp == 7
    assert out.inventory.coins_cp == 3
    assert out.inventory.torches == 2
    assert out.inventory.rations == 1
    assert out.inventory.ammo["arrows"] == 20
    assert len(out.inventory.items) == 1
    assert out.inventory.items[0].template_id == "weapon.longsword"
    assert out.inventory.items[0].category == "weapon"
    assert out.equipment.main_hand == "Sword"
    assert out.equipment.armor == "Chainmail"
    assert out.equipment.ammo_kind == "arrows"


def test_bridge_helpers_sync_legacy_and_new_equipment():
    a = Actor(name="Ela", hp=4, hp_max=4, ac_desc=8, hd=1, save=15, weapon="Spear", armor="Leather", shield=True, shield_name="Shield")

    a.sync_legacy_equipment_to_new()
    assert a.equipment.main_hand == "Spear"
    assert a.equipment.armor == "Leather"
    assert a.equipment.shield == "Shield"

    a.equipment.main_hand = "Mace"
    a.equipment.shield = None
    a.sync_new_equipment_to_legacy()
    assert a.weapon == "Mace"


def test_migrate_12_to_13_normalizes_inventory_item_keys_and_ammo_kind():
    save_v12 = {
        "save_version": 12,
        "version": 12,
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
                        "inventory": {
                            "items": [{"instance_id": "i1", "item_id": "weapon.sword", "kind": "weapon", "name": "Sword"}],
                            "coins_gp": 5,
                        },
                        "equipment": {"main_hand": "Sword", "ammo": "arrows"},
                    }
                ]
            }
        },
    }

    out = migrate_save_data(save_v12)
    member = out["campaign"]["party"]["members"][0]

    assert out["save_version"] == 13
    assert member["inventory"]["items"][0]["template_id"] == "weapon.sword"
    assert member["inventory"]["items"][0]["category"] == "weapon"
    assert member["equipment"]["ammo_kind"] == "arrows"
