from sww.item_templates import build_item_instance, get_item_template, template_weight_lb


def test_weapon_template_loads_from_extracted_data():
    t = get_item_template("weapon.sword_long")
    assert t.category == "weapon"
    assert t.name == "Sword, long"
    assert template_weight_lb(t) > 0


def test_armor_template_loads_from_extracted_data():
    t = get_item_template("armor.chain")
    assert t.category == "armor"
    assert t.name == "Chain"
    assert template_weight_lb(t) > 0


def test_build_item_instance_from_template():
    inst = build_item_instance("weapon.sword_long", quantity=2, identified=True)
    assert inst.template_id == "weapon.sword_long"
    assert inst.quantity == 2
    assert inst.name == "Sword, long"
    assert "weight_lb" in inst.metadata
