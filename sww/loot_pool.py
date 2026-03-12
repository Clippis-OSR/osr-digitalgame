"""Temporary loot-pool runtime layer.

Ownership lifecycle (transitional):
1. Generated treasure enters :class:`LootPool` as pending, unowned loot.
2. Each entry is explicitly assigned to one owner destination:
   - a specific actor inventory,
   - party/town stash,
   - or discarded/left behind.
3. Legacy ``party_items`` can mirror the pool for compatibility, but should not
   be treated as the source of truth for new reward generation.

Coin handling note:
- ``LootPool.coins_gp`` tracks unassigned expedition coins.
- Most current game flows still settle coins immediately through the central
  coin reward policy helper for compatibility (see ``coin_rewards.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
from typing import Any

from .inventory_service import add_item_to_actor
from .item_templates import build_item_instance, find_template_id_by_name, item_is_magic
from .models import Actor, ItemInstance

_ENTRY_SEQ = count(1)


@dataclass
class LootEntry:
    entry_id: str
    kind: str
    name: str
    quantity: int = 1
    gp_value: int | None = None
    identified: bool = True
    true_name: str | None = None
    template_id: str | None = None
    item_instance: ItemInstance | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LootPool:
    coins_gp: int = 0
    entries: list[LootEntry] = field(default_factory=list)
    stash: list[LootEntry] = field(default_factory=list)


def create_loot_pool(*, coins_gp: int = 0, entries: list[LootEntry] | None = None, stash: list[LootEntry] | None = None) -> LootPool:
    return LootPool(coins_gp=max(0, int(coins_gp or 0)), entries=list(entries or []), stash=list(stash or []))


def add_item_to_loot_pool(pool: LootPool, item: Any, *, identify_magic: bool = False) -> LootEntry:
    """Add one generated item row to pending loot ownership stage."""
    entry = _normalize_item_entry(item, identify_magic=identify_magic)
    pool.entries.append(entry)
    return entry


def _next_entry_id() -> str:
    return f"loot-{next(_ENTRY_SEQ):06d}"


def _template_id_for_loot_name(name: str, kind: str) -> str | None:
    pref = [kind]
    if kind == "relic":
        pref = ["treasure", "gear"]
    try:
        tid = find_template_id_by_name(name, preferred_categories=pref)
    except Exception:
        tid = None
    if tid:
        return tid
    if kind in {"gem", "jewelry", "treasure", "relic"}:
        return "treasure.generic"
    return "gear.backpack_30-pound_capacity"


def _is_magic_kind(kind: str) -> bool:
    return kind in {"potion", "scroll", "ring", "wand", "relic"}


def _normalize_item_entry(row: Any, *, identify_magic: bool) -> LootEntry:
    if not isinstance(row, dict):
        row = {"name": str(row), "kind": "gear", "gp_value": None}

    kind = str(row.get("kind") or "gear").strip().lower()
    display_name = str(row.get("name") or "Unknown Item")
    true_name = row.get("true_name")
    identified = bool(row.get("identified", True))
    if _is_magic_kind(kind) and "identified" not in row and true_name:
        identified = bool(identify_magic)

    resolved_name = str(true_name or display_name)
    tid = _template_id_for_loot_name(resolved_name, kind)
    inst: ItemInstance | None = None
    if tid:
        try:
            md = {
                "source_kind": kind,
                "gp_value": row.get("gp_value"),
                "true_name": true_name,
            }
            md.update(dict(row.get("metadata") or {}))
            if row.get("charges") is not None:
                md["charges"] = int(row.get("charges") or 0)
            if row.get("cursed") is not None:
                md["cursed"] = bool(row.get("cursed"))
            inst = build_item_instance(
                tid,
                quantity=max(1, int(row.get("quantity", 1) or 1)),
                identified=identified,
                metadata=md,
            )
            # Preserve user-facing names for generic fallbacks.
            if resolved_name and inst.name != resolved_name and tid in {"treasure.generic", "gear.backpack_30-pound_capacity"}:
                inst.name = resolved_name
                inst.category = "treasure" if kind in {"gem", "jewelry", "treasure", "relic"} else "gear"
        except Exception:
            inst = None

    if _is_magic_kind(kind) and inst is not None and "identified" not in row and true_name:
        inst.identified = bool(identify_magic)
    if _is_magic_kind(kind) and inst is not None and "identified" not in row and not true_name:
        try:
            if item_is_magic(inst):
                inst.identified = bool(identify_magic)
        except Exception:
            pass

    if inst is not None and not true_name:
        true_name = inst.name

    shown_name = str(display_name if not identified else (true_name or display_name))
    return LootEntry(
        entry_id=_next_entry_id(),
        kind=kind,
        name=shown_name,
        quantity=max(1, int(row.get("quantity", 1) or 1)),
        gp_value=(None if row.get("gp_value") is None else int(row.get("gp_value") or 0)),
        identified=identified,
        true_name=(None if true_name is None else str(true_name)),
        template_id=tid,
        item_instance=inst,
        metadata=dict(row.get("metadata") or {}),
    )


def add_generated_treasure_to_pool(
    pool: LootPool,
    *,
    gp: int = 0,
    items: list[Any] | None = None,
    identify_magic: bool = False,
) -> tuple[int, list[LootEntry]]:
    gp_i = max(0, int(gp or 0))
    pool.coins_gp += gp_i
    added: list[LootEntry] = []
    for row in list(items or []):
        e = add_item_to_loot_pool(pool, row, identify_magic=identify_magic)
        added.append(e)
    return gp_i, added


def assign_loot_to_actor(pool: LootPool, actor: Actor, entry_id: str) -> bool:
    eid = str(entry_id or "")
    pick = next((e for e in pool.entries if str(e.entry_id) == eid), None)
    if pick is None:
        return False
    if pick.item_instance is None:
        tid = str(pick.template_id or _template_id_for_loot_name(str(pick.true_name or pick.name), str(pick.kind)) or "")
        if not tid:
            return False
        try:
            pick.item_instance = build_item_instance(
                tid,
                quantity=max(1, int(pick.quantity or 1)),
                identified=bool(pick.identified),
                metadata={"source_kind": pick.kind, "gp_value": pick.gp_value, "true_name": pick.true_name},
            )
        except Exception:
            return False
    if pick.item_instance is None:
        return False
    res = add_item_to_actor(actor, pick.item_instance)
    if not res.ok:
        return False
    pool.entries = [e for e in pool.entries if str(e.entry_id) != eid]
    return True


def assign_loot_item_to_actor(pool: LootPool, actor: Actor, entry_id: str) -> bool:
    """Assign one pending loot entry to a specific actor's inventory."""
    return assign_loot_to_actor(pool, actor, entry_id)


def move_loot_to_party_stash(pool: LootPool, entry_id: str) -> bool:
    eid = str(entry_id or "")
    pick = next((e for e in pool.entries if str(e.entry_id) == eid), None)
    if pick is None:
        return False
    pool.stash.append(pick)
    pool.entries = [e for e in pool.entries if str(e.entry_id) != eid]
    return True


def assign_loot_item_to_stash(pool: LootPool, entry_id: str) -> bool:
    """Assign one pending loot entry to party/town stash ownership."""
    return move_loot_to_party_stash(pool, entry_id)


def leave_loot_behind(pool: LootPool, entry_id: str) -> bool:
    eid = str(entry_id or "")
    before = len(pool.entries)
    pool.entries = [e for e in pool.entries if str(e.entry_id) != eid]
    return len(pool.entries) != before


def discard_loot_item(pool: LootPool, entry_id: str) -> bool:
    """Discard a pending loot entry (left behind / sold / destroyed)."""
    return leave_loot_behind(pool, entry_id)


def loot_pool_entries_as_legacy_dicts(pool: LootPool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in list(pool.entries or []):
        out.append(
            {
                "entry_id": e.entry_id,
                "name": e.name,
                "kind": e.kind,
                "gp_value": e.gp_value,
                "quantity": int(e.quantity or 1),
                "identified": bool(e.identified),
                "true_name": e.true_name,
                "template_id": e.template_id,
                "metadata": dict(e.metadata or {}),
                "charges": ((e.item_instance.metadata or {}).get("charges") if getattr(e, "item_instance", None) is not None else None),
                "cursed": ((e.item_instance.metadata or {}).get("cursed") if getattr(e, "item_instance", None) is not None else None),
            }
        )
    return out
