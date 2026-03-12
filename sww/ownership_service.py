"""Ownership-aware item movement service.

Centralizes deterministic ownership transitions across loot_pool, actor
inventory, and party stash during migration away from anonymous shared loot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .inventory_service import (
    InventoryServiceResult,
    add_item_to_actor,
    find_item_on_actor,
    move_item_actor_to_stash as _move_item_actor_to_stash,
    move_item_stash_to_actor as _move_item_stash_to_actor,
    remove_item_from_actor,
)
from .item_templates import build_item_instance, find_template_id_by_name
from .loot_pool import LootEntry, LootPool, discard_loot_item
from .models import Actor, ItemInstance, PartyStash


@dataclass(frozen=True)
class OwnershipMoveResult:
    ok: bool
    error: str | None = None
    item: ItemInstance | None = None
    quantity_changed: int = 0
    from_location: str | None = None
    to_location: str | None = None


@dataclass(frozen=True)
class OwnershipLocation:
    location: str
    owner: str | None = None
    entry_id: str | None = None


def _as_result(res: InventoryServiceResult, *, src: str, dst: str) -> OwnershipMoveResult:
    return OwnershipMoveResult(
        ok=bool(res.ok),
        error=res.error,
        item=res.item,
        quantity_changed=int(res.quantity_changed or 0),
        from_location=src,
        to_location=dst,
    )


def _ensure_party_stash(stash_or_game: Any) -> PartyStash:
    if isinstance(stash_or_game, PartyStash):
        return stash_or_game
    existing = getattr(stash_or_game, "party_stash", None)
    if isinstance(existing, PartyStash):
        return existing
    stash = PartyStash()
    try:
        setattr(stash_or_game, "party_stash", stash)
    except Exception:
        pass
    return stash


def _template_id_for_entry(entry: LootEntry) -> str:
    tid = str(getattr(entry, "template_id", "") or "").strip()
    if tid:
        return tid
    kind = str(getattr(entry, "kind", "gear") or "gear").strip().lower()
    name = str(getattr(entry, "true_name", None) or getattr(entry, "name", "Unknown Item") or "Unknown Item")
    pref = [kind] if kind != "relic" else ["treasure", "gear"]
    try:
        hit = find_template_id_by_name(name, preferred_categories=pref)
    except Exception:
        hit = None
    if hit:
        return str(hit)
    return "treasure.generic" if kind in {"gem", "jewelry", "treasure", "relic"} else "gear.backpack_30-pound_capacity"


def _entry_to_item_instance(entry: LootEntry) -> ItemInstance | None:
    if entry.item_instance is not None:
        return entry.item_instance
    try:
        inst = build_item_instance(
            _template_id_for_entry(entry),
            quantity=max(1, int(getattr(entry, "quantity", 1) or 1)),
            identified=bool(getattr(entry, "identified", True)),
            metadata={
                "source_kind": getattr(entry, "kind", "gear"),
                "gp_value": getattr(entry, "gp_value", None),
                "true_name": getattr(entry, "true_name", None),
            },
        )
    except Exception:
        return None
    entry.item_instance = inst
    return inst


def move_item_loot_pool_to_actor(pool: LootPool, actor: Actor, entry_id: str) -> OwnershipMoveResult:
    eid = str(entry_id or "")
    entry = next((e for e in list(pool.entries or []) if str(getattr(e, "entry_id", "")) == eid), None)
    if entry is None:
        return OwnershipMoveResult(ok=False, error="loot entry not found", from_location="loot_pool", to_location=f"actor:{getattr(actor, 'name', 'actor')}")

    inst = _entry_to_item_instance(entry)
    if inst is None:
        return OwnershipMoveResult(ok=False, error="loot entry has no valid item instance", from_location="loot_pool", to_location=f"actor:{getattr(actor, 'name', 'actor')}")

    added = add_item_to_actor(actor, inst)
    if not added.ok:
        return _as_result(added, src="loot_pool", dst=f"actor:{getattr(actor, 'name', 'actor')}")

    pool.entries = [e for e in list(pool.entries or []) if str(getattr(e, "entry_id", "")) != eid]
    return OwnershipMoveResult(ok=True, item=inst, quantity_changed=max(1, int(getattr(inst, "quantity", 1) or 1)), from_location="loot_pool", to_location=f"actor:{getattr(actor, 'name', 'actor')}")


def move_item_loot_pool_to_stash(pool: LootPool, stash_or_game: Any, entry_id: str) -> OwnershipMoveResult:
    eid = str(entry_id or "")
    entry = next((e for e in list(pool.entries or []) if str(getattr(e, "entry_id", "")) == eid), None)
    if entry is None:
        return OwnershipMoveResult(ok=False, error="loot entry not found", from_location="loot_pool", to_location="stash")

    inst = _entry_to_item_instance(entry)
    if inst is None:
        return OwnershipMoveResult(ok=False, error="loot entry has no valid item instance", from_location="loot_pool", to_location="stash")

    stash = _ensure_party_stash(stash_or_game)
    stash.items.append(inst)
    pool.entries = [e for e in list(pool.entries or []) if str(getattr(e, "entry_id", "")) != eid]
    # Transitional bridge: keep legacy loot_pool.stash mirror alive while save paths exist.
    pool.stash.append(entry)
    return OwnershipMoveResult(ok=True, item=inst, quantity_changed=max(1, int(getattr(inst, "quantity", 1) or 1)), from_location="loot_pool", to_location="stash")


def move_item_actor_to_stash(actor: Actor, stash_or_game: Any, instance_id: str, *, quantity: int = 1, in_town: bool = True) -> OwnershipMoveResult:
    return _as_result(
        _move_item_actor_to_stash(actor, stash_or_game, instance_id, quantity=quantity, in_town=in_town),
        src=f"actor:{getattr(actor, 'name', 'actor')}",
        dst="stash",
    )


def move_item_stash_to_actor(stash_or_game: Any, actor: Actor, instance_id: str, *, quantity: int = 1, in_town: bool = True) -> OwnershipMoveResult:
    return _as_result(
        _move_item_stash_to_actor(stash_or_game, actor, instance_id, quantity=quantity, in_town=in_town),
        src="stash",
        dst=f"actor:{getattr(actor, 'name', 'actor')}",
    )


def remove_owned_item(game_or_ctx: Any, *, source_kind: str, reference_id: str, actor: Actor | None = None, quantity: int = 1) -> OwnershipMoveResult:
    sk = str(source_kind or "").strip().lower()
    ref = str(reference_id or "")

    if sk == "actor":
        if actor is None:
            return OwnershipMoveResult(ok=False, error="actor is required for actor source", from_location="actor", to_location="removed")
        return _as_result(remove_item_from_actor(actor, ref, quantity), src=f"actor:{getattr(actor, 'name', 'actor')}", dst="removed")

    if sk == "stash":
        stash = _ensure_party_stash(game_or_ctx)
        item = next((it for it in list(stash.items or []) if str(getattr(it, "instance_id", "")) == ref), None)
        if item is None:
            return OwnershipMoveResult(ok=False, error="stash item not found", from_location="stash", to_location="removed")
        q = int(quantity or 0)
        if q <= 0:
            return OwnershipMoveResult(ok=False, error="quantity must be >= 1", from_location="stash", to_location="removed")
        if int(getattr(item, "quantity", 1) or 1) < q:
            return OwnershipMoveResult(ok=False, error="insufficient quantity", from_location="stash", to_location="removed")
        if int(getattr(item, "quantity", 1) or 1) == q:
            stash.items = [it for it in list(stash.items or []) if str(getattr(it, "instance_id", "")) != ref]
            return OwnershipMoveResult(ok=True, item=item, quantity_changed=q, from_location="stash", to_location="removed")
        item.quantity = int(item.quantity) - q
        part = ItemInstance(
            instance_id=f"{item.instance_id}:removed:{q}",
            template_id=item.template_id,
            name=item.name,
            category=item.category,
            quantity=q,
            identified=bool(item.identified),
            cursed_known=bool(item.cursed_known),
            custom_label=item.custom_label,
            equipped=False,
            magic_bonus=int(item.magic_bonus or 0),
            metadata=dict(item.metadata or {}),
        )
        return OwnershipMoveResult(ok=True, item=part, quantity_changed=q, from_location="stash", to_location="removed")

    if sk in {"loot_pool", "legacy"}:
        pool = getattr(game_or_ctx, "loot_pool", game_or_ctx)
        if not isinstance(pool, LootPool):
            return OwnershipMoveResult(ok=False, error="loot pool not available", from_location="loot_pool", to_location="removed")
        pick = next((e for e in list(pool.entries or []) if str(getattr(e, "entry_id", "")) == ref), None)
        if pick is None:
            return OwnershipMoveResult(ok=False, error="loot entry not found", from_location="loot_pool", to_location="removed")
        inst = _entry_to_item_instance(pick)
        if not discard_loot_item(pool, ref):
            return OwnershipMoveResult(ok=False, error="could not remove loot entry", from_location="loot_pool", to_location="removed")
        return OwnershipMoveResult(ok=True, item=inst, quantity_changed=max(1, int(getattr(pick, "quantity", 1) or 1)), from_location="loot_pool", to_location="removed")

    return OwnershipMoveResult(ok=False, error=f"unsupported source_kind: {sk}", from_location=(sk or None), to_location="removed")


def owned_item_location(game: Any, instance_id: str) -> OwnershipLocation | None:
    iid = str(instance_id or "")
    for actor in list(getattr(getattr(game, "party", None), "members", []) or []):
        if actor is None:
            continue
        if find_item_on_actor(actor, iid) is not None:
            return OwnershipLocation(location="actor", owner=str(getattr(actor, "name", "actor") or "actor"), entry_id=None)

    stash = _ensure_party_stash(game)
    if any(str(getattr(it, "instance_id", "")) == iid for it in list(stash.items or [])):
        return OwnershipLocation(location="stash", owner="stash", entry_id=None)

    pool = getattr(game, "loot_pool", None)
    if isinstance(pool, LootPool):
        for e in list(pool.entries or []):
            inst = getattr(e, "item_instance", None)
            if inst is not None and str(getattr(inst, "instance_id", "")) == iid:
                return OwnershipLocation(location="loot_pool", owner="loot_pool", entry_id=str(getattr(e, "entry_id", "")))
    return None
