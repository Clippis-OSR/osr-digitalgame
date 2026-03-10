\
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re

from .dice import Dice
from .data import load_json

@dataclass
class TreasureItem:
    kind: str
    name: str
    details: str | None = None
    gp_value: int | None = None

class TreasureTables:
    def __init__(self):
        self.tables: dict[str, Any] = load_json("treasure_tables_79_107.json")

    def roll_on(self, dice: Dice, table_no: int) -> str:
        t = self.tables[str(table_no)]
        die = t["die"]  # like 1d100
        r = dice.roll(die).total
        for e in t["entries"]:
            if e["lo"] <= r <= e["hi"]:
                return e["result"]
        # fallback
        return t["entries"][-1]["result"]

class TreasureGenerator:
    def __init__(self, dice: Dice):
        self.dice = dice
        self.tt = TreasureTables()

    def _eval_gp_expr(self, expr: str) -> int | None:
        """
        Evaluate very small dice expressions used in gem tables, like:
          '1d100 + 25 gp'
          '1d6 x300 gp'
          '1d100 x10 gp'
          '1d6 gp'
        """
        s = expr.lower().replace("gp","").strip()
        s = s.replace("×","x").replace("–","-")
        # patterns
        m = re.search(r"(\d+d\d+)(?:\s*\+\s*(\d+))?\s*$", s)
        if m and "x" not in s:
            base = self.dice.roll(m.group(1)).total
            add = int(m.group(2) or 0)
            return base + add
        m = re.search(r"(\d+d\d+)\s*x\s*(\d+)\s*$", s)
        if m:
            base = self.dice.roll(m.group(1)).total
            mult = int(m.group(2))
            return base * mult
        return None

    def roll_minor_gem_or_jewelry(self) -> TreasureItem:
        res = self.tt.roll_on(self.dice, 79)
        gp = self._eval_gp_expr(res.split("worth",1)[-1]) if "worth" in res else None
        return TreasureItem(kind="gem/jewelry (minor)", name=res, gp_value=gp)

    def roll_medium_gem_or_jewelry(self) -> TreasureItem:
        res = self.tt.roll_on(self.dice, 80)
        # if the major table got glued into this one in PDF, keep result text as-is
        gp = self._eval_gp_expr(res.split("worth",1)[-1]) if "worth" in res else None
        return TreasureItem(kind="gem/jewelry (medium)", name=res, gp_value=gp)

    def roll_major_gem_or_jewelry(self) -> TreasureItem:
        res = self.tt.roll_on(self.dice, 81)
        gp = self._eval_gp_expr(res.split("worth",1)[-1]) if "worth" in res else None
        return TreasureItem(kind="gem/jewelry (major)", name=res, gp_value=gp)

    def roll_potion(self) -> TreasureItem:
        res = self.tt.roll_on(self.dice, 85)
        return TreasureItem(kind="potion", name=res)

    def roll_scroll_general(self) -> TreasureItem:
        res = self.tt.roll_on(self.dice, 86)
        return TreasureItem(kind="scroll (general)", name=res)

    def roll_protection_scroll(self) -> TreasureItem:
        res = self.tt.roll_on(self.dice, 87)
        return TreasureItem(kind="protection scroll", name=res)

    def roll_cursed_scroll(self) -> TreasureItem:
        res = self.tt.roll_on(self.dice, 88)
        return TreasureItem(kind="cursed scroll", name=res)

    def roll_magic_armor_weapon(self, offset: int = 0) -> TreasureItem:
        # Table 89 is rolled via "1d6+6" etc in tables 82-84. We treat that by rolling 1d18 with offset.
        # Table 89 entries are numbered 1..18.
        t = self.tt.tables["89"]
        r = self.dice.d(18) + offset
        r = max(1, min(18, r))
        for e in t["entries"]:
            if e["lo"] <= r <= e["hi"]:
                return TreasureItem(kind="magic armor/weapon", name=e["result"])
        return TreasureItem(kind="magic armor/weapon", name=t["entries"][-1]["result"])

    def roll_remarkable_item(self, roll: int) -> TreasureItem:
        # Table 98 is 1d60, but other tables add offsets. clamp.
        t = self.tt.tables["98"]
        roll = max(1, min(60, roll))
        for e in t["entries"]:
            if e["lo"] <= roll <= e["hi"]:
                return TreasureItem(kind="remarkable item (overview)", name=e["result"])
        return TreasureItem(kind="remarkable item (overview)", name=t["entries"][-1]["result"])

    def roll_minor_magic_item(self) -> list[TreasureItem]:
        # Table 82: 1d4
        res = self.tt.roll_on(self.dice, 82).lower()
        if "potions" in res:
            return [self.roll_potion()]
        if "scrolls" in res:
            n = self.dice.d(6)
            return [self.roll_scroll_general() for _ in range(n)]
        if "magic armor" in res:
            n = self.dice.d(6)
            return [self.roll_magic_armor_weapon(offset=0) for _ in range(n)]
        if "remarkable" in res:
            roll = self.dice.d(20)
            return [self.roll_remarkable_item(roll)]
        return [TreasureItem(kind="minor magic item", name=res)]

    def roll_medium_magic_item(self) -> list[TreasureItem]:
        res = self.tt.roll_on(self.dice, 83).lower()
        if "potions" in res:
            return [self.roll_potion() for _ in range(3)]
        if "scrolls" in res:
            n = self.dice.d(6) + 6
            return [self.roll_scroll_general() for _ in range(n)]
        if "magic armor" in res:
            n = self.dice.d(6) + 6
            return [self.roll_magic_armor_weapon(offset=6) for _ in range(n)]
        if "remarkable" in res:
            roll = self.dice.d(20) + 20
            return [self.roll_remarkable_item(roll)]
        return [TreasureItem(kind="medium magic item", name=res)]

    def roll_major_magic_item(self) -> list[TreasureItem]:
        res = self.tt.roll_on(self.dice, 84).lower()
        if "potions" in res:
            return [self.roll_potion() for _ in range(6)]
        if "scrolls" in res:
            n = self.dice.d(6) + 12
            return [self.roll_scroll_general() for _ in range(n)]
        if "magic armor" in res:
            n = self.dice.d(6) + 12
            return [self.roll_magic_armor_weapon(offset=12) for _ in range(n)]
        if "remarkable" in res:
            roll = self.dice.d(20) + 40
            return [self.roll_remarkable_item(roll)]
        return [TreasureItem(kind="major magic item", name=res)]

    def roll_hoard(self, tier: str) -> list[TreasureItem]:
        """
        Tier: 'minor' / 'medium' / 'major'
        We implement the book's gem/jewelry + magic item tables (79-107).
        Coinage tables are not yet extracted; we approximate coinage as 3d6*10 gp minor, 6d6*10 gp medium, 10d6*10 gp major.
        """
        items: list[TreasureItem] = []
        if tier == "minor":
            items.append(TreasureItem(kind="coins", name="Coinage", gp_value=self.dice.roll("3d6").total * 10))
            # 50% chance gem, 25% chance magic item
            if self.dice.d(100) <= 50:
                items.append(self.roll_minor_gem_or_jewelry())
            if self.dice.d(100) <= 25:
                items.extend(self.roll_minor_magic_item())
        elif tier == "medium":
            items.append(TreasureItem(kind="coins", name="Coinage", gp_value=self.dice.roll("6d6").total * 10))
            if self.dice.d(100) <= 65:
                items.append(self.roll_medium_gem_or_jewelry())
            if self.dice.d(100) <= 40:
                items.extend(self.roll_medium_magic_item())
        else:
            items.append(TreasureItem(kind="coins", name="Coinage", gp_value=self.dice.roll("10d6").total * 10))
            if self.dice.d(100) <= 80:
                items.append(self.roll_major_gem_or_jewelry())
            if self.dice.d(100) <= 60:
                items.extend(self.roll_major_magic_item())
        return items

    def dungeon_treasure(self, depth: int) -> tuple[int, list[dict[str, Any]]]:
        """Return (gp, items[]) for dungeon treasure at a given depth.

        The game engine expects gp to be credited immediately and "items" to be
        appended into Game.party_items as dicts with fields:
          {"name": str, "kind": str, "gp_value": int|None}

        We map dungeon depth to a hoard tier and split out coinage into gp.
        """
        d = max(1, int(depth or 1))
        if d <= 2:
            tier = "minor"
        elif d <= 5:
            tier = "medium"
        else:
            tier = "major"

        hoard = self.roll_hoard(tier)
        gp_total = 0
        items_out: list[dict[str, Any]] = []
        for it in hoard:
            if it.kind == "coins" and (it.gp_value is not None):
                gp_total += int(it.gp_value)
                continue
            items_out.append({"name": it.name, "kind": it.kind, "gp_value": it.gp_value})
        return gp_total, items_out
