from sww.encumbrance import actor_carried_weight_lb
from sww.game import Game
from sww.inventory_service import (
    add_item_to_actor,
    list_stash_items,
    move_item_actor_to_stash,
    move_item_stash_to_actor,
)
from sww.models import Actor, ItemInstance
from sww.save_load import apply_game_dict, game_to_dict
from sww.ui_headless import HeadlessUI


def _new_game(seed: int = 9000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def _actor(name: str = "Bearer") -> Actor:
    return Actor(name=name, hp=6, hp_max=6, ac_desc=8, hd=1, save=15)


def test_item_moved_to_stash_leaves_actor_inventory():
    g = _new_game(9100)
    actor = _actor("A")
    g.party.members = [actor]
    it = ItemInstance(instance_id="stash-i1", template_id="weapon.sword", name="Sword", category="weapon", quantity=1)
    assert add_item_to_actor(actor, it).ok

    res = move_item_actor_to_stash(actor, g, it.instance_id, in_town=True)

    assert res.ok
    assert all(x.instance_id != it.instance_id for x in actor.inventory.items)
    assert any(x.instance_id == it.instance_id for x in list_stash_items(g))


def test_stash_item_can_be_moved_to_actor_in_town():
    g = _new_game(9110)
    actor = _actor("A")
    g.party.members = [actor]
    stash_item = ItemInstance(instance_id="stash-i2", template_id="ring.ring_protection_plus1", name="Ring +1", category="ring", quantity=1)
    g.party_stash.items.append(stash_item)

    res = move_item_stash_to_actor(g, actor, stash_item.instance_id, in_town=True)

    assert res.ok
    assert any(x.instance_id == stash_item.instance_id for x in actor.inventory.items)
    assert all(x.instance_id != stash_item.instance_id for x in g.party_stash.items)


def test_stash_does_not_affect_expedition_encumbrance():
    g = _new_game(9120)
    actor = _actor("A")
    g.party.members = [actor]
    it = ItemInstance(instance_id="stash-i3", template_id="weapon.sword", name="Sword", category="weapon", quantity=1)
    assert add_item_to_actor(actor, it).ok

    carried_before = actor_carried_weight_lb(actor, equipdb=g.equipdb)
    assert move_item_actor_to_stash(actor, g, it.instance_id, in_town=True).ok
    carried_after = actor_carried_weight_lb(actor, equipdb=g.equipdb)

    assert carried_after < carried_before
    # Stashed item exists but does not contribute to actor carried load.
    assert any(x.instance_id == it.instance_id for x in g.party_stash.items)


def test_save_load_roundtrip_preserves_party_stash_contents():
    g = _new_game(9130)
    stashed = ItemInstance(
        instance_id="stash-i4",
        template_id="amulet.amulet_health",
        name="Amulet of Health",
        category="amulet",
        quantity=1,
        identified=False,
        metadata={"value_gp": 750},
    )
    g.party_stash.items.append(stashed)
    g.party_stash.coins_gp = 123

    data = game_to_dict(g)
    g2 = _new_game(9140)
    apply_game_dict(g2, data)

    assert g2.party_stash.coins_gp == 123
    assert len(g2.party_stash.items) == 1
    assert g2.party_stash.items[0].instance_id == "stash-i4"
    assert g2.party_stash.items[0].identified is False


def test_unidentified_item_moved_to_stash_remains_unidentified():
    g = _new_game(9150)
    actor = _actor("A")
    g.party.members = [actor]
    it = ItemInstance(
        instance_id="stash-unid-1",
        template_id="ring.ring_protection_plus1",
        name="Ring of Protection +1",
        category="ring",
        quantity=1,
        identified=False,
        metadata={"unidentified_name": "Unidentified magic item", "value_gp": 100},
    )
    assert add_item_to_actor(actor, it).ok

    res = move_item_actor_to_stash(actor, g, it.instance_id, in_town=True)

    assert res.ok
    assert len(g.party_stash.items) == 1
    assert g.party_stash.items[0].identified is False
