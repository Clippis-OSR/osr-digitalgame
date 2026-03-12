from sww.game import Game
from sww.models import Actor, CharacterEquipment, CharacterInventory, ItemInstance
from sww.ui_headless import HeadlessUI


def _game(seed: int = 7100) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def test_equipped_melee_weapon_affects_damage_resolution():
    g = _game(7101)
    a = Actor(name="A", hp=5, hp_max=5, ac_desc=9, hd=1, save=15, is_pc=True)
    sword = ItemInstance(instance_id="itm-sword", template_id="weapon.sword_long", name="Sword, long", category="weapon")
    a.inventory = CharacterInventory(items=[sword])
    a.equipment = CharacterEquipment(main_hand="itm-sword")
    a.weapon = None

    expr = g._weapon_damage_expr(a, kind="melee")
    assert expr == "1d8"


def test_shield_affects_defensive_calculation():
    g = _game(7102)
    d = Actor(name="D", hp=5, hp_max=5, ac_desc=9, hd=1, save=15, is_pc=True)
    leather = ItemInstance(instance_id="itm-leather", template_id="armor.leather", name="Leather", category="armor")
    shield = ItemInstance(instance_id="itm-shield", template_id="shield.shield", name="Shield", category="shield")
    d.inventory = CharacterInventory(items=[leather, shield])
    d.equipment = CharacterEquipment(armor="itm-leather", shield="itm-shield")

    with_shield = g._effective_ac_desc(d)
    d.equipment.shield = None
    without_shield = g._effective_ac_desc(d)

    assert with_shield < without_shield


def test_no_ammo_is_flagged_for_equipped_missile_weapon():
    g = _game(7103)
    a = Actor(name="Archer", hp=5, hp_max=5, ac_desc=9, hd=1, save=15, is_pc=True)
    bow = ItemInstance(instance_id="itm-bow", template_id="weapon.bow_long", name="Bow, long", category="weapon")
    arr = ItemInstance(instance_id="itm-arr", template_id="ammo.arrows_20", name="Arrows (20)", category="ammo", quantity=0)
    a.inventory = CharacterInventory(items=[bow, arr])
    a.equipment = CharacterEquipment(missile_weapon="itm-bow", ammo_kind="itm-arr")

    ammo_ref = g._effective_ammo_ref_for_actor(a, g.effective_missile_weapon(a))
    assert g._actor_has_ammo_for_ref(a, ammo_ref) is False
