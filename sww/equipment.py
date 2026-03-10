\
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re

from .data import load_json

@dataclass(frozen=True)
class Weapon:
    name: str
    damage: str
    weight_lb: int
    cost_gp: float
    kind: str  # melee/missile/ammo
    rate_of_fire: str | None = None
    range_ft: int | None = None
    footnotes: list[str] | None = None

    def damage_expr(self) -> str:
        # Normalize common "See Arrows" etc should not appear on PC weapon, only ammo/bows.
        return self.damage

@dataclass(frozen=True)
class Armor:
    name: str
    ac_mod_desc: int
    weight_lb: int
    cost_gp: int

class EquipmentDB:
    def __init__(self):
        self.melee = {w["name"]: w for w in load_json("weapons_melee.json")}
        self.missile = {w["name"]: w for w in load_json("weapons_missile.json")}
        self.armors = {a["name"]: a for a in load_json("armor_table_24.json")}

    def list_weapons(self, kind: str) -> list[str]:
        if kind == "melee":
            return sorted(self.melee.keys())
        return sorted(self.missile.keys())

    def get_weapon(self, name: str) -> Weapon:
        if name in self.melee:
            w = self.melee[name]
            return Weapon(name=w["name"], damage=w["damage"], weight_lb=w["weight_lb"], cost_gp=w["cost_gp"],
                          kind="melee", footnotes=w.get("footnotes", []))
        if name in self.missile:
            w = self.missile[name]
            # parse "70ft" to int
            rng = w.get("range","")
            m = re.search(r"(\d+)\s*ft", rng)
            rng_ft = int(m.group(1)) if m else None
            return Weapon(name=w["name"], damage=w["damage"], weight_lb=w["weight_lb"], cost_gp=w["cost_gp"],
                          kind="missile", rate_of_fire=w.get("rate_of_fire"), range_ft=rng_ft)
        raise KeyError(name)

    def get_armor(self, name: str) -> Armor:
        a = self.armors[name]
        return Armor(name=a["name"], ac_mod_desc=a["ac_mod_desc"], weight_lb=a["weight_lb"], cost_gp=a["cost_gp"])


def compute_ac_desc(*, base_ac_desc: int = 9, armor_name: str | None = None, shield: bool = False, dex_score: int | None = None) -> int:
    """Compute descending AC from equipment and DEX.

    Assumptions (S&W-ish, descending AC):
    - Base unarmored AC is 9.
    - Armor and shield apply their "ac_mod_desc" values from armor_table_24.json (negative improves AC).
    - DEX modifier applies directly to AC (positive DEX improves AC => lower AC).

    This stays deterministic and deliberately simple.
    """
    from .models import mod_ability
    from .data import load_json

    ac = int(base_ac_desc)

    # Load armor table on demand (tiny JSON); avoids circular deps.
    try:
        armor_rows = load_json("armor_table_24.json")
        armor_mods = {str(a.get("name")): int(a.get("ac_mod_desc")) for a in armor_rows if isinstance(a, dict)}
    except Exception:
        armor_mods = {"Shield": -1, "Leather": -2, "Ring": -3, "Chain": -4, "Plate": -6}

    if armor_name:
        ac += int(armor_mods.get(str(armor_name), 0))
    if shield:
        ac += int(armor_mods.get("Shield", -1))

    if dex_score is not None:
        ac -= int(mod_ability(int(dex_score)))

    return ac
