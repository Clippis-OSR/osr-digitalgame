from sww.item_templates import (
    SUPPORTED_ITEM_EFFECT_TYPES,
    build_item_instance,
    get_item_template,
    item_display_name,
    item_effects,
    item_is_cursed,
    item_is_magic,
    item_known_name,
    item_short_description,
    item_inspect_text,
    item_unsupported_effect_types,
    template_weight_lb,
)


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


def test_magic_template_fields_and_helpers_roundtrip():
    t = get_item_template("potion.potion_healing")
    assert item_is_magic(t)
    assert not item_is_cursed(t)
    assert item_effects(t)
    assert item_effects(t)[0]["type"] == "consumable_heal"
    assert not item_unsupported_effect_types(t)


def test_item_instance_metadata_contains_magic_runtime_payload():
    inst = build_item_instance("potion.potion_healing", identified=False)
    assert item_is_magic(inst)
    assert inst.metadata.get("effects")
    assert inst.metadata.get("unsupported_effect_types") == []
    assert item_display_name(inst) == "Cloudy Potion"


def test_unsupported_effect_type_detection_is_explicit():
    inst = build_item_instance(
        "gear.lantern_bullseye",
        metadata={"effects": [{"type": "future_placeholder"}]},
    )
    assert item_unsupported_effect_types(inst) == ["future_placeholder"]


def test_supported_effect_catalog_includes_expected_runtime_types():
    assert "attack_bonus" in SUPPORTED_ITEM_EFFECT_TYPES
    assert "spell_cast" in SUPPORTED_ITEM_EFFECT_TYPES


def test_identified_item_uses_real_name():
    t = get_item_template("potion.potion_healing")
    inst = build_item_instance("potion.potion_healing", identified=True)
    assert item_known_name(inst, t) == t.name


def test_unidentified_item_uses_placeholder_name():
    inst = build_item_instance("potion.potion_healing", identified=False)
    assert item_known_name(inst).lower().endswith("potion")


def test_unidentified_mundane_item_displays_sensibly():
    inst = build_item_instance("weapon.sword_long", identified=False)
    assert item_known_name(inst) == "Runed Sword"
    assert item_short_description(inst).startswith("Runed Sword")
    assert "Runed Sword" in item_inspect_text(inst)


def test_custom_label_overrides_template_display():
    inst = build_item_instance("ring.ring_protection_plus1", identified=False, custom_label="Grandma's Ring")
    assert item_known_name(inst) == "Grandma's Ring"
