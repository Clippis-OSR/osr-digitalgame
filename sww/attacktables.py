\
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from .data import load_json

@dataclass(frozen=True)
class TableRow:
    lo: int
    hi: int
    needed: list[int]  # 19 entries for AC 9..-9

def _parse_range(label: str) -> tuple[int,int]:
    label = label.strip()
    if "-" in label:
        a,b = label.split("-",1)
        return int(a), int(b)
    return int(label), int(label)

class AttackTables:
    """
    Loads Tables 29-32 (attack roll required to hit AC), as extracted from the PDF.
    We use descending AC columns 9..-9.
    """
    def __init__(self):
        data: dict[str, Any] = load_json("attack_tables_29_32.json")
        self.ac_cols: list[int] = data["29"]["ac_columns"]

        def build_rows(rows_dict: dict[str, list[int]]) -> list[TableRow]:
            out=[]
            for label, needed in rows_dict.items():
                lo,hi = _parse_range(label)
                out.append(TableRow(lo=lo, hi=hi, needed=needed))
            out.sort(key=lambda r: (r.lo, r.hi))
            return out

        self.t29 = build_rows(data["29"]["rows"])  # Cleric, Druid, Monk
        self.t30 = build_rows(data["30"]["rows"])  # Fighter, Paladin, Ranger
        self.t31 = build_rows(data["31"]["rows"])  # MU, Thief, Assassin
        self.t32_mon = data["32"]["rows"]          # Monster by HD label

    def needed_pc(self, cls: str, level: int, target_ac_desc: int) -> int:
        cls_l = cls.strip().lower()
        if cls_l in ("fighter","paladin","ranger"):
            rows = self.t30
        elif cls_l in ("cleric","druid","monk"):
            rows = self.t29
        else:
            rows = self.t31

        col = self._ac_index(target_ac_desc)
        row = self._row_for_level(rows, level)
        return row.needed[col]

    def needed_monster(self, hd: int, target_ac_desc: int) -> int:
        col = self._ac_index(target_ac_desc)
        # Table 32 is keyed as "<1HD", "1HD", ... "14HD" in our extraction.
        if hd < 1:
            key = "<1HD"
        else:
            key = f"{hd}HD"
            if key not in self.t32_mon:
                # clamp to max known
                max_hd = max(int(k.replace("HD","")) for k in self.t32_mon.keys() if k.endswith("HD") and k[0].isdigit())
                key = f"{min(hd, max_hd)}HD"
        needed = self.t32_mon[key][col]
        return needed

    def _ac_index(self, ac_desc: int) -> int:
        if ac_desc not in self.ac_cols:
            # clamp to range
            if ac_desc > 9:
                ac_desc = 9
            if ac_desc < -9:
                ac_desc = -9
            # find closest
            ac_desc = min(self.ac_cols, key=lambda x: abs(x-ac_desc))
        return self.ac_cols.index(ac_desc)

    def _row_for_level(self, rows: list[TableRow], level: int) -> TableRow:
        for r in rows:
            if r.lo <= level <= r.hi:
                return r
        return rows[-1]
