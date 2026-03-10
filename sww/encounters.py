\
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re
from .dice import Dice
from .data import load_json
from .models import Actor
from .rules import parse_desc_ac, parse_hd

@dataclass
class Encounter:
    kind: str
    foes: list[Actor]
    notes: str = ""

class EncounterTables:
    def __init__(self):
        self.tables: dict[str, Any] = load_json("encounter_tables_54_69.json")

    def roll_on(self, dice: Dice, table_no: int) -> str:
        t = self.tables[str(table_no)]
        r = dice.percent()
        for e in t["entries"]:
            if e["lo"] <= r <= e["hi"]:
                return e["result"]
        return t["entries"][-1]["result"]

class EncounterGenerator:
    """
    Wilderness encounter generator using Tables 54-69.

    Note: Tables 65 and 68 are terrain matrices in the book; PDF extraction is not clean enough
    to parse reliably here. For 'Humanoids and Giants' and 'Terrain-Specific' results,
    we approximate by choosing an appropriate monster from the loaded monster database.
    """
    def __init__(
        self,
        dice: Dice,
        monsters_data: list[dict[str, Any]],
        spells_data: list[dict[str, Any]] | None = None,
        monster_specials: dict[str, list[dict[str, Any]]] | None = None,
        monster_damage_profiles: dict[str, dict[str, Any]] | None = None,
    ):
        self.dice = dice
        self.monsters_data = monsters_data
        self.spells_data = spells_data or []
        self.monster_specials = monster_specials or {}
        self.monster_damage_profiles = monster_damage_profiles or {}
        self.et = EncounterTables()

    def _mk_monster(self, m: dict[str, Any]) -> Actor:
        hd = parse_hd(m["hit_dice"])
        ac = parse_desc_ac(m["armor_class"])
        save = int(re.sub(r"[^\d]", "", m["saving_throw"]) or "16")
        morale = 8
        hp = sum(self.dice.d(8) for _ in range(max(1, hd)))
        special = m.get('special','')
        # Specials/profiles are keyed by stable monster_id.
        mid = m.get('monster_id') or m.get('name','')
        specials_struct = list(self.monster_specials.get(mid, []) or [])
        dmg_prof = dict(self.monster_damage_profiles.get(mid, {}) or {})
        tags = []
        nl = m['name'].lower()
        sl = special.lower()
        if any(u in nl for u in ['skeleton','zombie','ghoul','wight','wraith','mummy','spectre','vampire','ghost','lich']) or 'undead' in sl:
            tags.append('undead')
        if any(a in nl for a in ['wolf','bear','lion','tiger','boar','ape','snake','rat','bat','hawk','eagle','shark','crocodile','dinosaur','ant','spider']) or 'animal' in sl:
            tags.append('animal')
        if 'dragon' in nl:
            tags.append('dragon')
        if any(h in nl for h in ['goblin','orc','kobold','gnoll','hobgoblin','ogre','troll','giant','bandit','berserker','lizard man','lizardmen']):
            tags.append('humanoid')
        if any(k in sl for k in ['spell','magic','wizard','sorcer','cleric']):
            tags.append('caster')
        # morale defaults by tag
        if 'undead' in tags:
            morale_val = None
        elif 'animal' in tags:
            morale_val = 7
        elif 'dragon' in tags:
            morale_val = 10
        elif 'humanoid' in tags:
            morale_val = 8
        else:
            morale_val = morale
        spells_known = []
        spells_prepared = []
        if 'caster' in tags:
            caster_type = 'Magic-User'
            if 'cleric' in sl:
                caster_type = 'Cleric'
            # spell level by HD
            spell_level = 1
            if hd >= 4: spell_level = 2
            if hd >= 6: spell_level = 3
            if hd >= 8: spell_level = 4
            if hd >= 10: spell_level = 5
            # Filter spells by caster type and max level
            pool = []
            for sp in self.spells_data:
                lvl_field = sp.get('spell_level','').lower()
                if caster_type.lower() in lvl_field:
                    # extract level number for that caster
                    import re as _re
                    m2 = _re.search(rf"{caster_type.lower()}\s*,\s*(\d+)(?:st|nd|rd|th)", lvl_field)
                    if m2 and int(m2.group(1)) <= spell_level:
                        pool.append(sp['name'])
            # choose a small prepared list
            self_rng = getattr(self.dice, 'rng', None)
            if pool:
                k = min(len(pool), max(1, hd//2))
                spells_known = self.dice.rng.sample(pool, k=k)
                spells_prepared = list(spells_known)
        a = Actor(
            name=m["name"], hp=hp, hp_max=hp, ac_desc=ac, hd=hd, save=save,
            morale=morale_val, alignment=m.get("alignment", "Neutrality"), is_pc=False,
            special_text=special, specials=specials_struct, tags=tags,
            spells_known=spells_known, spells_prepared=spells_prepared
        )
        # P3.1.15+: attach stable monster_id (required for rules lookup).
        mid_val = (m.get('monster_id') or '').strip()
        if not mid_val:
            # Defensive fallback: derive a deterministic slug from name.
            # Content validation should already prevent missing monster_id in monsters.json,
            # but this keeps spawned Actors consistent if a bad entry slips in.
            nm = str(m.get('name', '') or '').strip().lower()
            nm = re.sub(r"[^a-z0-9]+", "_", nm).strip('_')
            mid_val = nm or None
        try:
            setattr(a, 'monster_id', mid_val)
        except Exception:
            pass

        # Apply damage profile overrides (P2.9+).
        if dmg_prof:
            try:
                a.damage_profile.update(dmg_prof)
            except Exception:
                a.damage_profile = dmg_prof

        return a

    def _pick_by_keywords(self, keywords: list[str]) -> dict[str, Any] | None:
        kws = [k.lower() for k in keywords]
        cands = []
        for m in self.monsters_data:
            nl = m["name"].lower()
            if any(k in nl for k in kws):
                cands.append(m)
        return self.dice.rng.choice(cands) if cands else None

    def _pick_any(self) -> dict[str, Any]:
        return self.dice.rng.choice(self.monsters_data)

    def wilderness_encounter(self, terrain: str) -> Encounter | None:
        terrain = terrain.lower()
        table_map = {
            "clear": 54,
            "desert": 55,
            "forest": 56,
            "hills": 57,
            "mountain": 57,
            "river": 58,
            "sea": 59,
            "swamp": 60,
        }
        tno = table_map.get(terrain, 54)
        category = self.et.roll_on(self.dice, tno)

        # Resolve category to a sub-table
        if category.lower().startswith("animals"):
            sub = self.et.roll_on(self.dice, 61)
            m = self._pick_by_keywords([sub.split(" or ")[0], sub.split(" or ")[-1], sub])
            if not m:
                m = self._pick_any()
            foes = [self._mk_monster(m) for _ in range(self.dice.d(4))]
            return Encounter(kind=f"Animals ({sub})", foes=foes)

        if category.lower().startswith("dragon"):
            sub = self.et.roll_on(self.dice, 62)
            m = self._pick_by_keywords(["dragon", "hydra", "basilisk", "cockatrice"])
            foes = [self._mk_monster(m or self._pick_any())]
            return Encounter(kind=f"Draconic ({sub})", foes=foes, notes="Age categories are not yet simulated.")

        if category.lower().startswith("flying"):
            sub = self.et.roll_on(self.dice, 63)
            m = self._pick_by_keywords(["griffon","harpy","hippogriff","roc","wyvern","stirge","gargoyle","manticore","pegasi","chimera","djinni","efreet"])
            foes = [self._mk_monster(m or self._pick_any()) for _ in range(1 + self.dice.d(2))]
            return Encounter(kind=f"Flying Creatures ({sub})", foes=foes)

        if category.lower().startswith("humankind"):
            sub = self.et.roll_on(self.dice, 64)
            # We don't have full NPC stat blocks extracted yet; approximate with "Human, Bandit" etc if present.
            m = self._pick_by_keywords(["human", "bandit", "berserker", "wizard", "soldier", "sergeant"])
            foes = [self._mk_monster(m or self._pick_any()) for _ in range(1 + self.dice.d(4))]
            return Encounter(kind=f"Humankind ({sub})", foes=foes, notes="NPC subtypes are approximated in this build.")

        if category.lower().startswith("humanoids"):
            m = self._pick_by_keywords(["kobold","goblin","orc","hobgoblin","gnoll","ogre","troll","giant"])
            foes = [self._mk_monster(m or self._pick_any()) for _ in range(2 + self.dice.d(6))]
            return Encounter(kind="Humanoids and Giants", foes=foes, notes="Terrain-specific humanoid matrix (Table 65) not fully parsed yet.")

        if category.lower().startswith("misc"):
            sub = self.et.roll_on(self.dice, 66)
            m = self._pick_by_keywords([sub.split(" or ")[0], sub.split(" or ")[-1], sub])
            foes = [self._mk_monster(m or self._pick_any())]
            return Encounter(kind=f"Misc Monster ({sub})", foes=foes)

        if category.lower().startswith("swimming"):
            sub = self.et.roll_on(self.dice, 67)
            m = self._pick_by_keywords(["crocodile","dragon turtle","octopus","sea","serpent","squid","fish","nixie","mermen","leech","naga"])
            foes = [self._mk_monster(m or self._pick_any())]
            return Encounter(kind=f"Swimming ({sub})", foes=foes)

        if category.lower().startswith("undead"):
            sub = self.et.roll_on(self.dice, 69)
            m = self._pick_by_keywords(["skeleton","zombie","ghoul","wight","wraith","vampire","mummy","spectre"])
            foes = [self._mk_monster(m or self._pick_any()) for _ in range(1 + self.dice.d(3))]
            return Encounter(kind=f"Undead ({sub})", foes=foes)

        return None
