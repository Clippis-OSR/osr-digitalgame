"""Town inventory services (deterministic, save-safe).

Owns town-facing stash/sell/loot-assignment orchestration while keeping
Game as state owner/router.
"""

from __future__ import annotations

from typing import Any

from .coin_rewards import CoinDestination
from .inventory_service import find_item_on_actor
from .item_templates import owned_item_brief_label
from .ownership_service import move_item_loot_pool_to_actor, move_item_loot_pool_to_stash, remove_owned_item


class TownInventoryService:
    def __init__(self, game: Any):
        self.g = game

    def sale_unit_value_gp(self, item: Any) -> int:
        md = dict(getattr(item, "metadata", {}) or {})
        return max(0, int(md.get("value_gp", md.get("gp_value", 0)) or 0))

    def sale_price_gp(self, *, unit_value_gp: int, quantity: int) -> int:
        gross = max(0, int(unit_value_gp or 0)) * max(1, int(quantity or 1))
        if gross <= 0:
            return 0
        return max(1, int(gross * 0.5))

    def loot_sale_source_label(self, entry: Any, *, from_legacy_compat: bool) -> str:
        md = dict(getattr(entry, "metadata", {}) or {})
        owner = str(md.get("owner_actor") or "").strip()
        if owner:
            return f"actor:{owner}"
        source = str(md.get("source") or "").strip().lower()
        if source in {"stash", "party_stash"}:
            return "stash"
        if from_legacy_compat:
            return "legacy compatibility pool"
        return "expedition loot pool"

    def preferred_sellable_items(self, *, include_legacy_compat: bool = False) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for src in self.g.loot_source_fallback_order(intent="sellable"):
            if src == "actor_inventory":
                for actor in list(getattr(getattr(self.g, "party", None), "members", []) or []):
                    if actor is None:
                        continue
                    try:
                        actor.ensure_inventory_initialized()
                    except Exception:
                        continue
                    for item in list(getattr(getattr(actor, "inventory", None), "items", []) or []):
                        qty = max(1, int(getattr(item, "quantity", 1) or 1))
                        unit = self.sale_unit_value_gp(item)
                        price = self.sale_price_gp(unit_value_gp=unit, quantity=qty)
                        if price <= 0:
                            continue
                        nm = owned_item_brief_label(item)
                        rows.append({
                            "source_kind": "actor",
                            "owner": str(getattr(actor, "name", "actor") or "actor"),
                            "instance_id": str(getattr(item, "instance_id", "") or ""),
                            "name": nm,
                            "quantity": qty,
                            "identified": bool(getattr(item, "identified", True)),
                            "price_gp": int(price),
                            "label": f"{nm} (actor:{getattr(actor, 'name', 'actor')}) sell {price} gp",
                        })
            elif src == "party_stash":
                stash_items = list(getattr(getattr(self.g, "party_stash", None), "items", []) or [])
                for item in stash_items:
                    qty = max(1, int(getattr(item, "quantity", 1) or 1))
                    unit = self.sale_unit_value_gp(item)
                    price = self.sale_price_gp(unit_value_gp=unit, quantity=qty)
                    if price <= 0:
                        continue
                    nm = owned_item_brief_label(item)
                    rows.append({
                        "source_kind": "stash",
                        "owner": "stash",
                        "instance_id": str(getattr(item, "instance_id", "") or ""),
                        "name": nm,
                        "quantity": qty,
                        "identified": bool(getattr(item, "identified", True)),
                        "price_gp": int(price),
                        "label": f"{nm} (stash) sell {price} gp",
                    })

        if include_legacy_compat:
            from_legacy_compat = bool(self.g._ensure_loot_pool_hydrated_from_legacy())
            for e in list(getattr(self.g.loot_pool, "entries", []) or []):
                val = int(getattr(e, "gp_value", 0) or 0)
                if val <= 0:
                    continue
                qty = int(getattr(e, "quantity", 1) or 1)
                price = self.sale_price_gp(unit_value_gp=val, quantity=qty)
                u = "unidentified" if not bool(getattr(e, "identified", True)) else "identified"
                qtxt = f" x{qty}" if qty > 1 else ""
                rows.append({
                    "source_kind": "legacy",
                    "owner": self.loot_sale_source_label(e, from_legacy_compat=from_legacy_compat),
                    "entry_id": str(getattr(e, "entry_id", "") or ""),
                    "name": str(getattr(e, "name", "loot") or "loot"),
                    "quantity": qty,
                    "identified": bool(getattr(e, "identified", True)),
                    "price_gp": int(price),
                    "label": f"{e.name} ({u}, legacy) {qtxt}sell {price} gp".replace("  ", " "),
                })
        return rows

    def list_sellable_items(self, *, include_legacy_compat: bool = False) -> list[dict[str, Any]]:
        return self.preferred_sellable_items(include_legacy_compat=include_legacy_compat)

    def sell_actor_item(self, actor: Any, instance_id: str, *, source: str = "sell_loot") -> bool:
        item = find_item_on_actor(actor, instance_id)
        if item is None:
            return False
        qty = max(1, int(getattr(item, "quantity", 1) or 1))
        price = self.sale_price_gp(unit_value_gp=self.sale_unit_value_gp(item), quantity=qty)
        if price <= 0:
            return False
        sold_name = owned_item_brief_label(item)
        removed = remove_owned_item(self.g, source_kind="actor", reference_id=instance_id, actor=actor, quantity=qty)
        if not bool(getattr(removed, "ok", False)):
            return False
        coin_res = self.g._grant_coin_reward(price, source=source, destination=CoinDestination.TREASURY)
        self.g.ui.log(f"Sold {sold_name} (actor:{actor.name}) for {price} gp to {self.g._coin_destination_label(str(getattr(coin_res, 'destination', '') or ''))}.")
        return True

    def sell_stash_item(self, instance_id: str, *, source: str = "sell_loot") -> bool:
        stash = getattr(self.g, "party_stash", None)
        if stash is None:
            return False
        iid = str(instance_id or "")
        item = next((it for it in list(getattr(stash, "items", []) or []) if str(getattr(it, "instance_id", "") or "") == iid), None)
        if item is None:
            return False
        qty = max(1, int(getattr(item, "quantity", 1) or 1))
        price = self.sale_price_gp(unit_value_gp=self.sale_unit_value_gp(item), quantity=qty)
        if price <= 0:
            return False
        sold_name = owned_item_brief_label(item)
        removed = remove_owned_item(self.g, source_kind="stash", reference_id=iid, quantity=qty)
        if not bool(getattr(removed, "ok", False)):
            return False
        coin_res = self.g._grant_coin_reward(price, source=source, destination=CoinDestination.TREASURY)
        self.g.ui.log(f"Sold {sold_name} (stash) for {price} gp to {self.g._coin_destination_label(str(getattr(coin_res, 'destination', '') or ''))}.")
        return True

    def sell_legacy_party_item(self, entry_id: str, *, source: str = "sell_loot") -> bool:
        entries = list(getattr(self.g.loot_pool, "entries", []) or [])
        pick = next((e for e in entries if str(getattr(e, "entry_id", "") or "") == str(entry_id or "")), None)
        if pick is None:
            return False
        qty = max(1, int(getattr(pick, "quantity", 1) or 1))
        val = max(0, int(getattr(pick, "gp_value", 0) or 0))
        price = self.sale_price_gp(unit_value_gp=val, quantity=qty)
        if price <= 0:
            return False
        sold_name = str(getattr(pick, "name", "loot") or "loot")
        sold_from = self.loot_sale_source_label(pick, from_legacy_compat=True)
        removed = remove_owned_item(self.g, source_kind="legacy", reference_id=str(entry_id or ""), quantity=qty)
        if not bool(getattr(removed, "ok", False)):
            return False
        self.g._sync_legacy_party_items_if_needed(reason="sell_legacy_party_item")
        coin_res = self.g._grant_coin_reward(price, source=source, destination=CoinDestination.TREASURY)
        self.g.ui.log(f"Sold {sold_name} ({sold_from}) for {price} gp to {self.g._coin_destination_label(str(getattr(coin_res, 'destination', '') or ''))}.")
        return True

    def sell_loot(self) -> None:
        sellables = self.list_sellable_items(include_legacy_compat=False)
        using_legacy = False
        if not sellables:
            compat = self.list_sellable_items(include_legacy_compat=True)
            compat = [r for r in compat if str(r.get("source_kind")) == "legacy"]
            if not compat:
                self.g.ui.log("You have no loot to sell.")
                return
            c = self.g.ui.choose("No owned sellables found. Use legacy pooled loot compatibility sale?", ["Yes", "No"])
            if c != 0:
                return
            sellables = compat
            using_legacy = True

        labels = [str(r.get("label") or "Sell item") for r in sellables]
        c = self.g.ui.choose("Sell which?", labels + ["Back"])
        if c == len(labels):
            return
        row = dict(sellables[c] or {})
        sk = str(row.get("source_kind") or "")
        ok = False
        if sk == "actor":
            actor_name = str(row.get("owner") or "")
            actor = next((a for a in list(getattr(getattr(self.g, "party", None), "members", []) or []) if str(getattr(a, "name", "") or "") == actor_name), None)
            if actor is not None:
                ok = self.sell_actor_item(actor, str(row.get("instance_id") or ""), source="sell_loot")
        elif sk == "stash":
            ok = self.sell_stash_item(str(row.get("instance_id") or ""), source="sell_loot")
        elif sk == "legacy" and using_legacy:
            ok = self.sell_legacy_party_item(str(row.get("entry_id") or ""), source="sell_loot")
        if not ok:
            self.g.ui.log("Could not complete sale.")

    def assign_party_loot_to_character(self) -> None:
        self.g._ensure_loot_pool_hydrated_from_legacy()
        entries = list(getattr(self.g.loot_pool, "entries", []) or [])
        if not entries:
            self.g.ui.log("No party loot to assign.")
            return
        living = self.g.party.living()
        if not living:
            self.g.ui.log("No living party members available.")
            return

        self.g.ui.log(f"Loot pool: {int(getattr(self.g.loot_pool, 'coins_gp', 0) or 0)} gp in coins, {len(entries)} item(s).")
        who_preview = ", ".join([a.name for a in living[:3]]) + ("..." if len(living) > 3 else "")
        loot_labels = []
        for e in entries:
            u = "unidentified" if not bool(getattr(e, "identified", True)) else "identified"
            qty = int(getattr(e, "quantity", 1) or 1)
            qtxt = f" x{qty}" if qty > 1 else ""
            loot_labels.append(f"{str(e.name or 'Unknown')} ({str(e.kind or 'item')}, {u}){qtxt} -> {who_preview}")
        li = self.g.ui.choose("Assign which loot item?", loot_labels + ["Back"])
        if li == len(loot_labels):
            return

        picked = entries[li]
        dest_opts = ["Assign to party member"]
        can_stash = str(getattr(getattr(self.g, "travel_state", None), "location", "town") or "town").strip().lower() == "town"
        if can_stash:
            dest_opts.append("Send to stash")
        dest_opts.append("Leave behind")
        dest_opts.append("Back")

        di = self.g.ui.choose("Loot destination?", dest_opts)
        if di == len(dest_opts) - 1:
            return

        picked_name = str(getattr(picked, "name", "loot") or "loot")
        if dest_opts[di] == "Assign to party member":
            who_labels = [a.name for a in living]
            wi = self.g.ui.choose("Assign to who?", who_labels + ["Back"])
            if wi == len(who_labels):
                return
            actor = living[wi]
            moved = move_item_loot_pool_to_actor(self.g.loot_pool, actor, picked.entry_id)
            if not moved.ok:
                self.g.ui.log("Could not assign item to actor inventory.")
                return
            self.g._sync_legacy_party_items_if_needed(reason="assign_loot_to_actor")
            moved_item = getattr(moved, "item", None)
            auto_equipped = bool(getattr(moved_item, "equipped", False)) if moved_item is not None else False
            equip_note = "auto-equipped" if auto_equipped else "added to inventory"
            self.g.ui.log(f"Assigned {picked_name} to actor:{actor.name} ({equip_note}).")
            return

        if dest_opts[di] == "Send to stash":
            moved = move_item_loot_pool_to_stash(self.g.loot_pool, self.g, picked.entry_id)
            if not moved.ok:
                self.g.ui.log("Could not move item to stash.")
                return
            self.g._sync_legacy_party_items_if_needed(reason="assign_loot_to_stash")
            self.g.ui.log(f"Routed {picked_name} to shared stash.")
            return

        removed = remove_owned_item(
            self.g,
            source_kind="legacy",
            reference_id=str(getattr(picked, "entry_id", "") or ""),
            quantity=max(1, int(getattr(picked, "quantity", 1) or 1)),
        )
        if not removed.ok:
            self.g.ui.log("Could not leave item behind.")
            return
        self.g._sync_legacy_party_items_if_needed(reason="leave_loot_behind")
        self.g.ui.log(f"Left {picked_name} behind (removed from loot pool).")
