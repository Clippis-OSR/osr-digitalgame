from sww.game import Game
from sww.inventory_service import add_item_to_actor, equip_item_on_actor, unequip_item_on_actor
from sww.loot_pool import LootEntry, create_loot_pool
from sww.models import Actor, ItemInstance
from sww.item_templates import build_item_instance
from sww.ui_headless import HeadlessUI


class _CaptureUI(HeadlessUI):
    def __init__(self, picks=None):
        super().__init__()
        self.picks = list(picks or [])
        self.captures: list[tuple[str, list[str]]] = []

    def choose(self, prompt: str, options: list[str]) -> int:
        self.captures.append((prompt, list(options)))
        if self.picks:
            return int(self.picks.pop(0))
        return max(0, len(options) - 1)


def _actor(name: str) -> Actor:
    return Actor(name=name, hp=6, hp_max=6, ac_desc=8, hd=1, save=15)


def test_unidentified_ring_inventory_line_is_readable():
    ui = _CaptureUI()
    g = Game(ui, dice_seed=7000, wilderness_seed=7001)
    a = _actor("A")
    ring = ItemInstance(
        instance_id="ux-r1",
        template_id="ring.r1",
        name="Ring",
        category="ring",
        quantity=1,
        identified=False,
        metadata={"wear_category": "ring", "weight_lb": 0.1},
    )

    line = g._ui_item_line(a, ring, include_weight=True)

    assert "unidentified" in line
    assert "lb" in line


def test_identified_potion_inspection_shows_known_properties():
    ui = _CaptureUI()
    g = Game(ui, dice_seed=7010, wilderness_seed=7011)
    potion = build_item_instance("potion.potion_healing", identified=True)

    lines = g._ui_item_inspection_lines(potion)

    assert any("Known properties:" in ln for ln in lines)
    assert any("effects:" in ln for ln in lines)


def test_cursed_stuck_weapon_error_is_readable():
    a = _actor("A")
    cursed = ItemInstance(
        instance_id="ux-c1",
        template_id="weapon.cursed_blade",
        name="Runed Sword",
        category="weapon",
        metadata={"cursed": True, "effects": [{"type": "sticky_equip", "value": True}]},
    )
    assert add_item_to_actor(a, cursed).ok
    assert equip_item_on_actor(a, cursed.instance_id).ok

    res = unequip_item_on_actor(a, "main_hand")

    assert not res.ok
    assert "cannot be unequipped" in str(res.error or "")


def test_loot_assignment_options_show_identification_and_recipients():
    ui = _CaptureUI(picks=[0, 0, 2])  # pick first loot item, choose actor destination, then Back at recipient chooser
    g = Game(ui, dice_seed=7020, wilderness_seed=7021)
    g.party.members = [_actor("A"), _actor("B")]
    g.loot_pool = create_loot_pool(
        coins_gp=42,
        entries=[LootEntry(entry_id="loot-1", kind="ring", name="Unknown Ring", quantity=1, gp_value=100, identified=False)],
    )

    g.assign_party_loot_to_character()

    prompts = {p: opts for p, opts in ui.captures}
    loot_opts = prompts.get("Assign which loot item?", [])
    assert loot_opts
    assert any("unidentified" in opt for opt in loot_opts)
    assert any("-> A, B" in opt for opt in loot_opts)


def test_loot_assignment_can_send_item_to_stash_in_town():
    ui = _CaptureUI(picks=[0, 1])  # pick item, choose stash destination
    g = Game(ui, dice_seed=7030, wilderness_seed=7031)
    g.party.members = [_actor("A")]
    g.loot_pool = create_loot_pool(
        entries=[LootEntry(entry_id="loot-2", kind="gem", name="Amber", quantity=1, gp_value=50, identified=True)],
    )

    g.assign_party_loot_to_character()

    assert len(g.loot_pool.entries) == 0
    assert len(g.party_stash.items) == 1


def test_loot_assignment_can_leave_item_behind():
    ui = _CaptureUI(picks=[0, 1])  # pick item, choose leave behind (no stash option outside town)
    g = Game(ui, dice_seed=7040, wilderness_seed=7041)
    g.party.members = [_actor("A")]
    try:
        g.travel_state.location = "dungeon"
    except Exception:
        pass
    g.loot_pool = create_loot_pool(
        entries=[LootEntry(entry_id="loot-3", kind="ring", name="Unknown Ring", quantity=1, gp_value=100, identified=False)],
    )

    g.assign_party_loot_to_character()

    assert len(g.loot_pool.entries) == 0
    assert len(g.party_stash.items) == 0
