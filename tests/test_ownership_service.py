from sww.game import Game
from sww.inventory_service import add_item_to_actor
from sww.item_templates import build_item_instance
from sww.loot_pool import add_generated_treasure_to_pool
from sww.models import Actor
from sww.ownership_service import (
    move_item_actor_to_stash,
    move_item_loot_pool_to_actor,
    move_item_stash_to_actor,
    owned_item_location,
    remove_owned_item,
    ownership_invariant_violations,
)
from sww.ui_headless import HeadlessUI


def _game(seed: int = 12000) -> Game:
    return Game(HeadlessUI(), dice_seed=seed, wilderness_seed=seed + 1)


def _actor(name: str = "Owner") -> Actor:
    return Actor(name=name, hp=6, hp_max=6, ac_desc=8, hd=1, save=15, is_pc=True)


def test_loot_pool_to_actor_preserves_item_instance_identity():
    g = _game(12010)
    actor = _actor("Lina")
    g.party.members = [actor]

    _gp, added = add_generated_treasure_to_pool(g.loot_pool, items=[{"name": "Dagger", "kind": "weapon", "gp_value": 2}])
    entry = added[0]
    assert entry.item_instance is not None

    moved = move_item_loot_pool_to_actor(g.loot_pool, actor, entry.entry_id)

    assert moved.ok is True
    assert len(actor.inventory.items) == 1
    assert actor.inventory.items[0] is entry.item_instance
    assert len(g.loot_pool.entries) == 0


def test_actor_to_stash_removes_from_actor_and_adds_to_stash():
    g = _game(12020)
    actor = _actor("Mira")
    g.party.members = [actor]
    it = build_item_instance("weapon.sword_long", quantity=1, identified=True)
    assert add_item_to_actor(actor, it).ok

    moved = move_item_actor_to_stash(actor, g, it.instance_id, in_town=True)

    assert moved.ok is True
    assert all(str(x.instance_id) != str(it.instance_id) for x in actor.inventory.items)
    assert any(str(x.instance_id) == str(it.instance_id) for x in g.party_stash.items)


def test_stash_to_actor_works():
    g = _game(12030)
    actor = _actor("Nora")
    g.party.members = [actor]
    it = build_item_instance("ring.ring_protection_plus1", quantity=1, identified=False)
    g.party_stash.items.append(it)

    moved = move_item_stash_to_actor(g, actor, it.instance_id, in_town=True)

    assert moved.ok is True
    assert any(str(x.instance_id) == str(it.instance_id) for x in actor.inventory.items)
    assert all(str(x.instance_id) != str(it.instance_id) for x in g.party_stash.items)


def test_remove_owned_item_invalid_source_fails_cleanly():
    g = _game(12040)

    res = remove_owned_item(g, source_kind="bad_source", reference_id="x1")

    assert res.ok is False
    assert "unsupported source_kind" in str(res.error or "")


def test_owned_item_location_reports_actor_and_stash_locations():
    g = _game(12050)
    actor = _actor("Pax")
    g.party.members = [actor]
    actor_item = build_item_instance("weapon.dagger", quantity=1)
    stash_item = build_item_instance("treasure.generic", quantity=1)
    assert add_item_to_actor(actor, actor_item).ok
    g.party_stash.items.append(stash_item)

    loc_actor = owned_item_location(g, actor_item.instance_id)
    loc_stash = owned_item_location(g, stash_item.instance_id)

    assert loc_actor is not None
    assert loc_actor.location == "actor"
    assert loc_actor.owner == "Pax"
    assert loc_stash is not None
    assert loc_stash.location == "stash"



def test_ownership_invariants_hold_across_transfers_and_equipment():
    from sww.inventory_service import equip_item_on_actor, unequip_item_on_actor

    g = _game(12060)
    actor = _actor("Quinn")
    g.party.members = [actor]

    it = build_item_instance("weapon.sword_long", quantity=2, identified=True)
    assert add_item_to_actor(actor, it).ok
    assert equip_item_on_actor(actor, it.instance_id).ok
    assert ownership_invariant_violations(g) == []

    moved = move_item_actor_to_stash(actor, g, it.instance_id, quantity=1, in_town=True)
    assert moved.ok
    assert ownership_invariant_violations(g) == []

    back = move_item_stash_to_actor(g, actor, str(getattr(moved.item, "instance_id", it.instance_id)), quantity=1, in_town=True)
    assert back.ok
    uq = unequip_item_on_actor(actor, "main_hand")
    assert uq.ok
    assert ownership_invariant_violations(g) == []


def test_save_load_preserves_instance_equipment_ownership_invariants():
    from sww.inventory_service import equip_item_on_actor
    from sww.save_load import game_to_dict, apply_game_dict

    g = _game(12070)
    actor = _actor("Rhea")
    g.party.members = [actor]

    it = build_item_instance("weapon.sword_long", quantity=1, identified=True)
    assert add_item_to_actor(actor, it).ok
    assert equip_item_on_actor(actor, it.instance_id).ok

    data = game_to_dict(g)
    g2 = _game(12071)
    apply_game_dict(g2, data)

    actor2 = next((a for a in g2.party.members if a.name == "Rhea"), None)
    assert actor2 is not None
    assert str(getattr(actor2.equipment, "main_hand", ""))
    assert ownership_invariant_violations(g2) == []



def test_town_purchase_auto_equip_uses_ownership_first_equipment_refs():
    g = _game(12080)
    actor = _actor("Tess")
    g.party.members = [actor]

    ok = g._buy_item_for_actor(actor, name="Dagger", kind="weapon", auto_equip=True)

    assert ok is True
    assert len(actor.inventory.items) >= 1
    eq_ref = str(getattr(actor.equipment, "main_hand", "") or "")
    assert eq_ref
    assert any(str(getattr(it, "instance_id", "")) == eq_ref for it in (actor.inventory.items or []))
    assert ownership_invariant_violations(g) == []



def test_effective_weapon_does_not_prefer_stale_legacy_mirror():
    g = _game(12090)
    actor = _actor("Vale")
    g.party.members = [actor]

    it = build_item_instance("weapon.sword_long", quantity=1, identified=True)
    assert add_item_to_actor(actor, it).ok
    from sww.inventory_service import equip_item_on_actor
    assert equip_item_on_actor(actor, it.instance_id).ok

    # Simulate stale legacy mirror from older/non-migrated paths.
    actor.weapon = "Club"

    assert g.effective_melee_weapon(actor) != "Club"
    assert g.effective_melee_weapon(actor)
