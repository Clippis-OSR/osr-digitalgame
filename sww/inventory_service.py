"""Inventory/equipment service layer for per-character item operations.

This module centralizes transitional inventory/equipment mutation so game logic
can avoid ad-hoc direct writes on Actor.inventory / Actor.equipment.

Design notes:
- This layer is UI-agnostic.
- Operations are deterministic and return structured results.
- Legacy equipment fields are synchronized via Actor bridge helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .item_templates import get_item_template, item_known_name
from .models import Actor, ItemInstance


@dataclass(frozen=True)
class InventoryServiceResult:
    ok: bool
    error: str | None = None
    item: ItemInstance | None = None
    quantity_changed: int = 0


def _ensure_actor_ready(actor: Actor) -> None:
    actor.ensure_inventory_initialized()
    actor.ensure_equipment_initialized()


def _item_has_sticky_curse(item: ItemInstance) -> bool:
    """Return True when an equipped item should resist normal unequip/remove.

    Narrow scope for now: cursed items with `sticky_equip` effect payload.
    """
    try:
        md = dict(getattr(item, "metadata", {}) or {})
        if bool(md.get("curse_suppressed", False)):
            return False
        cursed = bool(md.get("cursed", False)) or bool(item_is_cursed(item))
        if not cursed:
            return False
        for eff in list(item_effects(item) or []):
            if str((eff or {}).get("type") or "").strip().lower() != "sticky_equip":
                continue
            if "value" not in eff:
                return True
            return bool(eff.get("value"))
    except Exception:
        return False
    return False


def _infer_slot_for_item(item: ItemInstance) -> str | None:
    cat = str(item.category or "").strip().lower()
    name = str(item.name or "").strip().lower()
    if cat == "armor":
        return "armor"
    if cat == "shield":
        return "shield"
    if cat in {"ammo", "missile_ammo"}:
        return "ammo_kind"
    if cat in {"missile_weapon", "ranged_weapon", "bow", "crossbow", "sling"}:
        return "missile_weapon"
    if cat in {"weapon", "melee_weapon"}:
        return "main_hand"
    if cat in {"worn_misc", "wondrous", "misc_worn", "ring", "cloak", "boots", "helm"}:
        return "worn_misc"
    # Lightweight name fallbacks for legacy/unknown categories.
    if "shield" in name:
        return "shield"
    if "bow" in name or "crossbow" in name or "sling" in name:
        return "missile_weapon"
    if "arrow" in name or "bolt" in name or "stone" in name:
        return "ammo_kind"
    return None


def find_item_on_actor(actor: Actor, instance_id: str) -> ItemInstance | None:
    _ensure_actor_ready(actor)
    iid = str(instance_id or "")
    for item in actor.inventory.items:
        if str(item.instance_id) == iid:
            return item
    return None


def add_item_to_actor(actor: Actor, item_instance: ItemInstance) -> InventoryServiceResult:
    _ensure_actor_ready(actor)
    if not isinstance(item_instance, ItemInstance):
        return InventoryServiceResult(ok=False, error="item_instance must be ItemInstance")
    if int(item_instance.quantity or 0) <= 0:
        return InventoryServiceResult(ok=False, error="quantity must be >= 1")

    existing = find_item_on_actor(actor, item_instance.instance_id)
    if existing is not None:
        existing.quantity = int(existing.quantity) + int(item_instance.quantity)
        return InventoryServiceResult(ok=True, item=existing, quantity_changed=int(item_instance.quantity))

    actor.inventory.items.append(item_instance)
    return InventoryServiceResult(ok=True, item=item_instance, quantity_changed=int(item_instance.quantity))


def remove_item_from_actor(actor: Actor, instance_id: str, quantity: int = 1) -> InventoryServiceResult:
    _ensure_actor_ready(actor)
    q = int(quantity or 0)
    if q <= 0:
        return InventoryServiceResult(ok=False, error="quantity must be >= 1")

    item = find_item_on_actor(actor, instance_id)
    if item is None:
        return InventoryServiceResult(ok=False, error="item not found")
    if int(item.quantity or 0) < q:
        return InventoryServiceResult(ok=False, error="insufficient quantity")
    if bool(getattr(item, "equipped", False)) and _item_has_sticky_curse(item):
        item.cursed_known = True
        return InventoryServiceResult(ok=False, error="item is cursed and cannot be removed")

    item.quantity = int(item.quantity) - q
    if int(item.quantity) == 0:
        # If item was equipped in any slot, unequip first.
        _unequip_instance_any_slot(actor, item.instance_id)
        actor.inventory.items = [it for it in actor.inventory.items if str(it.instance_id) != str(item.instance_id)]

    actor.sync_new_equipment_to_legacy()
    return InventoryServiceResult(ok=True, item=item, quantity_changed=-q)


def _unequip_instance_any_slot(actor: Actor, instance_id: str) -> None:
    eq = actor.equipment
    iid = str(instance_id)
    for slot in ("armor", "shield", "main_hand", "off_hand", "missile_weapon"):
        if str(getattr(eq, slot) or "") == iid:
            setattr(eq, slot, None)
    if str(eq.ammo_kind or "") == iid:
        eq.ammo_kind = None
    eq.worn_misc = [x for x in (eq.worn_misc or []) if str(x) != iid]


def equip_item_on_actor(actor: Actor, instance_id: str) -> InventoryServiceResult:
    _ensure_actor_ready(actor)
    item = find_item_on_actor(actor, instance_id)
    if item is None:
        return InventoryServiceResult(ok=False, error="item must be in inventory before equip")

    slot = _infer_slot_for_item(item)
    if slot is None:
        return InventoryServiceResult(ok=False, error="item category cannot be equipped")

    eq = actor.equipment
    iid = str(item.instance_id)

    if slot == "worn_misc":
        if iid not in eq.worn_misc:
            eq.worn_misc.append(iid)
    elif slot == "shield":
        # Sensible off-hand interaction: shield occupies off-hand.
        if eq.off_hand and eq.off_hand != iid:
            return InventoryServiceResult(ok=False, error="off-hand already occupied")
        eq.shield = iid
        eq.off_hand = iid
    elif slot == "off_hand":
        if eq.shield and eq.shield != iid:
            return InventoryServiceResult(ok=False, error="shield already equipped in off-hand")
        eq.off_hand = iid
    else:
        setattr(eq, slot, iid)

    item.equipped = True
    actor.sync_new_equipment_to_legacy()
    return InventoryServiceResult(ok=True, item=item, quantity_changed=0)


def unequip_item_on_actor(actor: Actor, slot_name: str) -> InventoryServiceResult:
    _ensure_actor_ready(actor)
    slot = str(slot_name or "").strip()
    eq = actor.equipment
    valid = {"armor", "shield", "main_hand", "off_hand", "missile_weapon", "ammo_kind", "worn_misc"}
    if slot not in valid:
        return InventoryServiceResult(ok=False, error="invalid slot")

    if slot == "worn_misc":
        if not eq.worn_misc:
            return InventoryServiceResult(ok=False, error="slot is already empty")
        iid = str(eq.worn_misc[-1])
    else:
        iid = str(getattr(eq, slot) or "")
        if not iid:
            return InventoryServiceResult(ok=False, error="slot is already empty")

    item = find_item_on_actor(actor, iid)
    if item is not None and _item_has_sticky_curse(item):
        item.cursed_known = True
        return InventoryServiceResult(ok=False, error="item is cursed and cannot be unequipped")

    if slot == "worn_misc":
        eq.worn_misc = list(eq.worn_misc[:-1])
    else:
        setattr(eq, slot, None)
        if slot == "shield" and str(eq.off_hand or "") == iid:
            eq.off_hand = None
        if slot == "off_hand" and str(eq.shield or "") == iid:
            eq.shield = None

    if item is not None:
        item.equipped = False

    actor.sync_new_equipment_to_legacy()
    return InventoryServiceResult(ok=True, item=item, quantity_changed=0)


def give_item_between_actors(src_actor: Actor, dst_actor: Actor, instance_id: str, quantity: int = 1) -> InventoryServiceResult:
    _ensure_actor_ready(src_actor)
    _ensure_actor_ready(dst_actor)
    q = int(quantity or 0)
    if q <= 0:
        return InventoryServiceResult(ok=False, error="quantity must be >= 1")

    src_item = find_item_on_actor(src_actor, instance_id)
    if src_item is None:
        return InventoryServiceResult(ok=False, error="source item not found")
    if int(src_item.quantity or 0) < q:
        return InventoryServiceResult(ok=False, error="insufficient quantity")

    if int(src_item.quantity or 0) == q:
        removed = remove_item_from_actor(src_actor, instance_id, q)
        if not removed.ok:
            return removed
        src_item.equipped = False
        return add_item_to_actor(dst_actor, src_item)

    # Split stack deterministically.
    src_item.quantity = int(src_item.quantity) - q
    moved = ItemInstance(
        instance_id=f"{src_item.instance_id}:split:{q}",
        template_id=src_item.template_id,
        name=src_item.name,
        category=src_item.category,
        quantity=q,
        identified=bool(src_item.identified),
        cursed_known=bool(src_item.cursed_known),
        custom_label=src_item.custom_label,
        equipped=False,
        magic_bonus=int(src_item.magic_bonus or 0),
        metadata=dict(src_item.metadata or {}),
    )
    added = add_item_to_actor(dst_actor, moved)
    if not added.ok:
        src_item.quantity = int(src_item.quantity) + q
        return added
    return InventoryServiceResult(ok=True, item=moved, quantity_changed=q)


def actor_equipped_weapon(actor: Actor) -> str | None:
    _ensure_actor_ready(actor)
    item = find_item_on_actor(actor, str(actor.equipment.main_hand or ""))
    if item is not None:
        try:
            tmpl = get_item_template(item.template_id)
        except Exception:
            tmpl = None
        return item_known_name(item, tmpl)
    return actor.equipment.main_hand or actor.weapon


def actor_equipped_armor(actor: Actor) -> str | None:
    _ensure_actor_ready(actor)
    item = find_item_on_actor(actor, str(actor.equipment.armor or ""))
    if item is not None:
        try:
            tmpl = get_item_template(item.template_id)
        except Exception:
            tmpl = None
        return item_known_name(item, tmpl)
    return actor.equipment.armor or actor.armor


def actor_has_ammo_for_current_missile_weapon(actor: Actor) -> bool:
    _ensure_actor_ready(actor)
    eq = actor.equipment
    if not eq.missile_weapon:
        return False

    ammo_ref = str(eq.ammo_kind or "")
    if not ammo_ref:
        return False

    # First: ammo_kind references an inventory item instance.
    ammo_item = find_item_on_actor(actor, ammo_ref)
    if ammo_item is not None:
        return int(ammo_item.quantity or 0) > 0

    # Fallback: ammo_kind is a legacy name key in ammo dict.
    k = ammo_ref.strip().lower()
    return int((actor.inventory.ammo or {}).get(k, 0) or 0) > 0
