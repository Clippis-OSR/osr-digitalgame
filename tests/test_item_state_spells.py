from sww.game import PC, Stats
from sww.inventory_service import add_item_to_actor, equip_item_on_actor, unequip_item_on_actor
from sww.item_state_spells import apply_identify_to_item, apply_remove_curse_to_item
from sww.item_templates import build_item_instance
from sww.loot_pool import create_loot_pool, add_generated_treasure_to_pool, loot_pool_entries_as_legacy_dicts
from sww.spellcasting import apply_spell_out_of_combat
from sww.ui_headless import HeadlessUI




class _DummyParty:
    def __init__(self, members):
        self.members = list(members)

    def living(self):
        return [m for m in self.members if int(getattr(m, "hp", 0) or 0) > 0]


class _DummyGame:
    def __init__(self, ui, members):
        self.ui = ui
        self.party = _DummyParty(members)
        self.party_items = []
        self.loot_pool = create_loot_pool()
        self.dungeon_rooms = {}
        self.current_room_id = None

    def _sync_legacy_party_items_from_loot_pool(self):
        self.party_items = loot_pool_entries_as_legacy_dicts(self.loot_pool)

class _SeqUI(HeadlessUI):
    def __init__(self, seq):
        super().__init__()
        self._seq = list(seq)

    def choose(self, prompt: str, options: list[str]) -> int:
        if self._seq:
            return int(self._seq.pop(0))
        return max(0, len(options) - 1)


def _pc(name: str) -> PC:
    return PC(
        name=name,
        race="Human",
        hp=6,
        hp_max=6,
        ac_desc=8,
        hd=1,
        save=15,
        morale=9,
        alignment="Neutrality",
        is_pc=True,
        cls="Magic-User",
        level=3,
        xp=0,
        stats=Stats(10, 10, 10, 10, 10, 10),
    )


def test_identify_spell_reveals_unidentified_item():
    ui = _SeqUI([0])
    caster = _pc("Caster")
    target = _pc("Bearer")
    g = _DummyGame(ui, [caster, target])
    item = build_item_instance("potion.potion_healing", identified=False)
    target.inventory.items.append(item)

    apply_spell_out_of_combat(game=g, caster=caster, spell_name="Identify", context="dungeon")

    assert target.inventory.items[0].identified is True
    assert target.inventory.items[0].name == "Potion of Healing"


def test_remove_curse_spell_allows_previously_blocked_unequip():
    ui = _SeqUI([0])
    caster = _pc("Caster")
    bearer = _pc("Bearer")
    g = _DummyGame(ui, [caster, bearer])
    cursed = build_item_instance(
        "weapon.sword_long",
        identified=False,
        metadata={"cursed": True, "effects": [{"type": "sticky_equip", "value": True}]},
    )
    add_item_to_actor(bearer, cursed)
    assert equip_item_on_actor(bearer, cursed.instance_id).ok
    blocked = unequip_item_on_actor(bearer, "main_hand")
    assert not blocked.ok

    apply_spell_out_of_combat(game=g, caster=caster, spell_name="Remove Curse", context="dungeon")

    after = unequip_item_on_actor(bearer, "main_hand")
    assert after.ok


def test_remove_curse_helper_is_safe_on_non_cursed_items():
    bearer = _pc("Bearer")
    normal = build_item_instance("weapon.sword_long", identified=True)
    bearer.inventory.items.append(normal)

    res = apply_remove_curse_to_item(bearer, normal.instance_id)

    assert res.ok
    assert res.changed is False


def test_identify_helper_targets_specific_instance():
    bearer = _pc("Bearer")
    a = build_item_instance("potion.potion_healing", identified=False)
    b = build_item_instance("potion.potion_healing", identified=False)
    bearer.inventory.items.extend([a, b])

    res = apply_identify_to_item(bearer, a.instance_id)

    assert res.ok
    assert a.identified is True
    assert b.identified is False


def test_detect_magic_marks_loot_pool_entries_without_direct_party_items_write():
    ui = _SeqUI([])
    caster = _pc("Caster")
    g = _DummyGame(ui, [caster])
    _gp, added = add_generated_treasure_to_pool(
        g.loot_pool,
        items=[{"name": "Mysterious Ring", "kind": "ring", "gp_value": 50, "identified": False}],
        identify_magic=False,
    )
    assert len(added) == 1
    assert g.party_items == []

    apply_spell_out_of_combat(game=g, caster=caster, spell_name="Detect Magic", context="dungeon")

    assert g.loot_pool.entries[0].metadata.get("aura") is True
    assert len(g.party_items) == 1
