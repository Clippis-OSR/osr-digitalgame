from sww.game import Game
from sww.loot_pool import create_loot_pool
from sww.treasure_runtime import (
    LootRoll,
    add_loot_roll_to_pool,
    loot_rows_from_loot_roll,
    roll_xp_tradeout_hoard,
)
from sww.ui_headless import HeadlessUI


class _SeqRng:
    def __init__(self, seq):
        self._seq = list(seq)

    def randint(self, a: int, b: int) -> int:
        if self._seq:
            v = int(self._seq.pop(0))
        else:
            v = int(a)
        if v < int(a):
            return int(a)
        if v > int(b):
            return int(b)
        return int(v)


def test_deterministic_tradeout_roll_can_produce_magic_item():
    g = Game(HeadlessUI(), dice_seed=8100, wilderness_seed=8101)
    # 100gp tradeout roll sequence:
    # - 1/10 check => 1 (tradeout)
    # - d20 => 20 (magic)
    # - tradeout pick r=4 (remarkable)
    # - follow-up table rolls deterministic low values
    g.dice_rng = _SeqRng([1, 20, 4, 1, 1, 1, 20])

    out = roll_xp_tradeout_hoard(game=g, budget_gp=100, instance_tag="seeded")

    assert out.magic_count >= 1
    assert len(list(out.magic_items or [])) >= 1


def test_generated_magic_item_enters_loot_pool_unidentified_by_default():
    roll = LootRoll(
        table_id="XP_TRADEOUTS",
        gp_from_coins=0,
        gp_from_gems=0,
        gp_from_jewelry=0,
        gems_count=0,
        jewelry_count=0,
        magic_count=1,
        magic_items=[{"kind": "ring", "name": "Jeweled Ring", "true_name": "Ring of Protection +1", "identified": False}],
    )
    pool = create_loot_pool()

    _gp, added = add_loot_roll_to_pool(pool=pool, roll=roll, identify_magic=False)

    assert len(added) == 1
    assert added[0].identified is False
    assert added[0].item_instance is not None
    assert added[0].item_instance.identified is False


def test_wand_charges_persist_through_loot_pool_integration():
    roll = LootRoll(
        table_id="XP_TRADEOUTS",
        gp_from_coins=0,
        gp_from_gems=0,
        gp_from_jewelry=0,
        gems_count=0,
        jewelry_count=0,
        magic_count=1,
        magic_items=[
            {
                "kind": "wand",
                "name": "Carved Wand",
                "true_name": "Wand of Fireballs",
                "identified": False,
                "charges": 17,
            }
        ],
    )
    pool = create_loot_pool()

    _gp, added = add_loot_roll_to_pool(pool=pool, roll=roll, identify_magic=False)

    assert len(added) == 1
    assert added[0].item_instance is not None
    assert int((added[0].item_instance.metadata or {}).get("charges", 0) or 0) == 17


def test_loot_rows_from_roll_transparently_emit_magic_rows():
    roll = LootRoll(
        table_id="XP_TRADEOUTS",
        gp_from_coins=25,
        gp_from_gems=50,
        gp_from_jewelry=0,
        gems_count=1,
        jewelry_count=0,
        magic_count=1,
        magic_items=[{"kind": "potion", "name": "Mysterious Potion", "true_name": "Potion of Healing", "identified": False}],
    )

    gp, rows = loot_rows_from_loot_roll(roll=roll, identify_magic=False)

    assert gp == 75
    assert len(rows) == 1
    assert rows[0]["kind"] == "potion"
    assert rows[0]["identified"] is False
