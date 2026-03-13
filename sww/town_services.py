"""Town-phase services (deterministic, save-safe)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .inventory_service import add_item_to_actor, equip_item_on_actor, find_item_on_actor
from .item_templates import build_item_instance, get_item_template, item_is_magic
from .models import Actor, ItemInstance


@dataclass(frozen=True)
class TownIdentifyResult:
    ok: bool
    error: str | None = None
    gold_spent: int = 0
    party_gold_after: int = 0
    item: ItemInstance | None = None


def identify_cost_gp(item: ItemInstance) -> int:
    """Conservative baseline costs for town identification."""
    cat = str(getattr(item, "category", "") or "").strip().lower()
    magic = False
    try:
        magic = bool(item_is_magic(item))
    except Exception:
        magic = bool((item.metadata or {}).get("magic", False))

    if not magic:
        return 8
    if cat in {"potion", "scroll", "ammo"}:
        return 15
    return 25


def identify_item_in_town(
    actor: Actor,
    instance_id: str,
    *,
    party_gold: int,
    reveal_curse: bool = False,
    reveal_charges: bool = False,
) -> TownIdentifyResult:
    """Identify one specific inventory item instance."""
    item = find_item_on_actor(actor, str(instance_id or ""))
    if item is None:
        return TownIdentifyResult(ok=False, error="item not found", party_gold_after=int(party_gold or 0))

    cur_gold = int(party_gold or 0)
    if bool(getattr(item, "identified", False)):
        return TownIdentifyResult(ok=True, gold_spent=0, party_gold_after=cur_gold, item=item)

    cost = identify_cost_gp(item)
    if cur_gold < cost:
        return TownIdentifyResult(ok=False, error="not enough gold", party_gold_after=cur_gold, item=item)

    item.identified = True
    try:
        tmpl = get_item_template(str(item.template_id or ""))
    except Exception:
        tmpl = None

    if tmpl is not None:
        item.name = str(tmpl.identified_name or tmpl.name)
        if reveal_curse and bool(getattr(tmpl, "cursed", False)):
            item.cursed_known = True
    if reveal_charges:
        md = dict(item.metadata or {})
        if "charges" in md:
            md["charges_known"] = True
            item.metadata = md

    return TownIdentifyResult(ok=True, gold_spent=cost, party_gold_after=cur_gold - cost, item=item)


class TownService:
    """Town-phase orchestrator extracted from Game.

    Game remains state/router owner; this service centralizes town interactions.
    """

    def __init__(self, game: Any):
        self.g = game

    def buy_item_for_actor(self, actor: Actor, *, name: str, kind: str, auto_equip: bool) -> bool:
        pref = ["weapon"] if kind == "weapon" else (["armor", "shield"] if kind == "armor" else [kind])
        tid = self.g._template_id_for_equipment_name(name, preferred_categories=pref)
        if not tid:
            return False
        try:
            inst = build_item_instance(tid, quantity=1, identified=True, metadata={"source": "town_shop"})
            base_id = str(name or tid)
            taken: set[str] = set()
            for m in list(getattr(getattr(self.g, "party", None), "members", []) or []):
                for it in list(getattr(getattr(m, "inventory", None), "items", []) or []):
                    taken.add(str(getattr(it, "instance_id", "") or ""))
            for it in list(getattr(getattr(self.g, "party_stash", None), "items", []) or []):
                taken.add(str(getattr(it, "instance_id", "") or ""))
            for e in list(getattr(getattr(self.g, "loot_pool", None), "entries", []) or []):
                ii = getattr(e, "item_instance", None)
                if ii is not None:
                    taken.add(str(getattr(ii, "instance_id", "") or ""))
            cand = base_id
            idx = 2
            while cand in taken:
                cand = f"{base_id} #{idx}"
                idx += 1
            inst.instance_id = cand
            r = add_item_to_actor(actor, inst)
            if not r.ok:
                return False
            if auto_equip:
                equip_item_on_actor(actor, inst.instance_id)
                try:
                    actor.sync_new_equipment_to_legacy()
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def town_arms_store(self) -> None:
        while True:
            self.g.ui.hr()
            self.g.ui.log(f"Gold: {self.g.gold} gp")
            mode = self.g.ui.choose("Arms & Armour", ["Buy weapon", "Buy armor", "Back"])
            if mode == 2:
                return

            living = self.g.party.living()
            if not living:
                self.g.ui.log("No one in the party can use equipment right now.")
                return

            labels = []
            for a in living:
                w = self.g.effective_melee_weapon(a) or self.g.effective_missile_weapon(a) or "None"
                ar = self.g.effective_armor(a) or "None"
                labels.append(f"{a.name}  (Weapon: {w} | Armor: {ar} | AC {int(getattr(a, 'ac_desc', 9) or 9)})")
            who = self.g.ui.choose("Who will use it?", labels + ["Back"])
            if who == len(living):
                continue
            actor = living[who]

            if mode == 0:
                weapons = list((self.g.equipdb.melee or {}).values()) + list((self.g.equipdb.missile or {}).values())
                weapons = [w for w in weapons if isinstance(w, dict) and w.get("cost_gp") is not None]
                weapons.sort(key=lambda w: (float(w.get("cost_gp", 0) or 0), str(w.get("name", "")).lower()))
                opts = [f"{w.get('name')} — {w.get('cost_gp')} gp" for w in weapons]
                sel = self.g.ui.choose("Buy weapon", opts + ["Back"])
                if sel == len(weapons):
                    continue
                row = weapons[sel]
                kind = "weapon"
            else:
                armors = [a for a in (self.g.equipdb.armors or {}).values() if isinstance(a, dict) and a.get("cost_gp") is not None]
                armors.sort(key=lambda a: (float(a.get("cost_gp", 0) or 0), str(a.get("name", "")).lower()))
                opts = [f"{a.get('name')} — {a.get('cost_gp')} gp (AC mod {a.get('ac_mod_desc')})" for a in armors]
                sel = self.g.ui.choose("Buy armor", opts + ["Back"])
                if sel == len(armors):
                    continue
                row = armors[sel]
                kind = "armor"

            cost = int(float(row.get("cost_gp", 0) or 0))
            if self.g.gold < cost:
                self.g.ui.log("Not enough gold.")
                continue

            before_ac = int(getattr(actor, "ac_desc", 9) or 9)
            before_weapon = self.g.effective_melee_weapon(actor) or self.g.effective_missile_weapon(actor) or "None"
            before_armor = self.g.effective_armor(actor) or "None"
            before_enc = self.g.party_encumbrance()

            self.g.gold -= cost
            bought_name = str(row.get("name"))
            if not self.buy_item_for_actor(actor, name=bought_name, kind=kind, auto_equip=True):
                if kind == "weapon":
                    actor.weapon = bought_name
                elif bought_name.strip().lower() == "shield":
                    actor.shield = True
                    actor.shield_name = bought_name
                else:
                    actor.armor = bought_name
                actor.sync_legacy_equipment_to_new()

            after_ac = int(getattr(actor, "ac_desc", 9) or 9)
            after_weapon = self.g.effective_melee_weapon(actor) or self.g.effective_missile_weapon(actor) or "None"
            after_armor = self.g.effective_armor(actor) or "None"
            after_enc = self.g.party_encumbrance()
            self.g.ui.log(f"Bought {bought_name} for {cost} gp.")
            self.g.ui.log(f"Auto-equip: yes. {actor.name} now has Weapon {after_weapon} | Armor {after_armor}.")
            if before_weapon != after_weapon:
                self.g.ui.log(f"Weapon role: {before_weapon} -> {after_weapon}.")
            if before_armor != after_armor:
                self.g.ui.log(f"Armor role: {before_armor} -> {after_armor}.")
            if before_ac != after_ac:
                self.g.ui.log(f"AC: {before_ac} -> {after_ac} (lower is better).")
            self.g.ui.log(
                f"Encumbrance: {before_enc.state}/{before_enc.movement_rate} -> {after_enc.state}/{after_enc.movement_rate}."
            )

    def town_buy_basic_resupply(self) -> bool:
        cost = 16
        if self.g.gold < cost:
            self.g.ui.log(f"Not enough gold. Need {cost} gp.")
            return False
        before = (int(self.g.rations), int(self.g.torches), int(self.g.arrows), int(self.g.bolts))
        self.g.gold -= cost
        self.g.rations = int(getattr(self.g, "rations", 0) or 0) + 6
        self.g.torches = int(getattr(self.g, "torches", 0) or 0) + 4
        self.g.arrows = int(getattr(self.g, "arrows", 0) or 0) + 20
        self.g.bolts = int(getattr(self.g, "bolts", 0) or 0) + 20
        self.g.ui.log(
            "Purchased basic delve kit: "
            f"rations {before[0]}->{self.g.rations}, torches {before[1]}->{self.g.torches}, "
            f"arrows {before[2]}->{self.g.arrows}, bolts {before[3]}->{self.g.bolts}."
        )
        return True

    def town_identify_items(self) -> None:
        unknown: list[tuple[Actor, Any, int]] = []
        for actor in (self.g.party.living() or []):
            actor.ensure_inventory_initialized()
            for item in (actor.inventory.items or []):
                if bool(getattr(item, "identified", True)):
                    continue
                if not bool(item_is_magic(item)):
                    continue
                unknown.append((actor, item, int(identify_cost_gp(item))))

        if not unknown:
            self.g.ui.log("No unidentified magic items in character inventories.")
            return

        total_cost = sum(c for _, _, c in unknown)
        c = self.g.ui.choose("Sage Appraisal", [f"Identify one item", f"Identify all ({total_cost} gp)", "Back"])
        if c == 2:
            return

        if c == 1:
            if self.g.gold < total_cost:
                self.g.ui.log(f"Cannot identify all: need {total_cost} gp, have {self.g.gold} gp.")
                return
            identified_n = 0
            for actor, item, _cost in unknown:
                res = identify_item_in_town(actor, item.instance_id, party_gold=self.g.gold, reveal_curse=False, reveal_charges=False)
                if not res.ok:
                    continue
                self.g.gold = int(res.party_gold_after)
                identified_n += 1
            self.g.ui.log(f"The sage identifies {identified_n} item(s).")
            return

        labels = [f"{a.name}: {self.g._ui_item_line(a, it, include_weight=False)} ({cost} gp)" for a, it, cost in unknown]
        j = self.g.ui.choose("Identify which item?", labels + ["Back"])
        if j == len(labels):
            return
        actor, item, _cost = unknown[j]
        res = identify_item_in_town(actor, item.instance_id, party_gold=self.g.gold, reveal_curse=False, reveal_charges=False)
        if not res.ok:
            if str(res.error or "") == "not enough gold":
                need = int(identify_cost_gp(item))
                self.g.ui.log(f"Cannot identify {getattr(item, 'name', 'item')}: need {need} gp, have {self.g.gold} gp.")
            else:
                self.g.ui.log("Could not identify that item right now.")
            return
        self.g.gold = int(res.party_gold_after)
        self.g.ui.log(f"Identified: {getattr(res.item, 'name', 'Unknown Item')}.")

    def town_services_menu(self) -> None:
        while True:
            self.g.ui.hr()
            self.g.ui.log(f"Gold: {self.g.gold} gp")
            c = self.g.ui.choose("Town Services", ["Temple healing", "Sage identification", "Buy basic delve kit (16 gp)", "Lodging", "Back"])
            if c == 4:
                return
            if c == 0:
                self.g._town_temple_healing()
            elif c == 1:
                self.town_identify_items()
            elif c == 2:
                self.town_buy_basic_resupply()
            elif c == 3:
                self.g._town_lodging_service()

    def manage_retainers(self) -> None:
        while True:
            self.g._cleanup_retainer_state()
            self.g.ui.hr()
            self.g.ui.log(f"Hired retainers: {len(self.g.hired_retainers)} | Active: {len(self.g.active_retainers)}")
            i = self.g.ui.choose("Retainers", [
                "View hiring board",
                "Hire",
                "Assign to expedition",
                "Dismiss from expedition",
                "Back",
            ])
            if i == 0:
                self.g.generate_retainer_board()
                for r in self.g.retainer_board:
                    role = self.g._retainer_role(r)
                    fee = int((getattr(r, "status", {}) or {}).get("retainer_hire_cost", 0) or 0)
                    self.g.ui.log(f"- {r.name} ({role}) | HP {r.hp}/{r.hp_max} | Loyalty {r.loyalty} | Hire {fee} gp | Upkeep {r.wage_gp} gp")
            elif i == 1:
                self.g.generate_retainer_board()
                labels = [
                    f"{r.name} ({self.g._retainer_role(r)}) Hire {int((getattr(r, 'status', {}) or {}).get('retainer_hire_cost', 0) or 0)} gp, Upkeep {r.wage_gp} gp"
                    for r in self.g.retainer_board
                ]
                j = self.g.ui.choose("Hire which?", labels + ["Back"])
                if j == len(labels):
                    continue
                self.g._hire_retainer_from_board(j)
            elif i == 2:
                if not self.g.hired_retainers:
                    self.g.ui.log("No hired retainers.")
                    continue
                labels = [f"{r.name} ({self.g._retainer_role(r)})" for r in self.g.hired_retainers if not r.on_expedition]
                if not labels:
                    self.g.ui.log("No available hired retainers to assign.")
                    continue
                j = self.g.ui.choose("Assign which?", labels + ["Back"])
                if j == len(labels):
                    continue
                self.g._assign_hired_retainer(j)
            elif i == 3:
                if not self.g.active_retainers:
                    self.g.ui.log("No active retainers.")
                    continue
                labels = [f"{r.name}" for r in self.g.active_retainers]
                j = self.g.ui.choose("Dismiss which?", labels + ["Back"])
                if j == len(labels):
                    continue
                self.g._dismiss_active_retainer(j)
            else:
                return
