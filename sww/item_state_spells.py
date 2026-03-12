"""Narrow spell integrations for item state changes.

This module keeps Identify / Remove Curse item-state effects explicit and
save-safe without introducing a large generic item-effect engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .item_templates import get_item_template, item_effects, item_is_cursed


@dataclass(frozen=True)
class ItemStateSpellResult:
    ok: bool
    error: str | None = None
    changed: bool = False
    actor_name: str | None = None
    instance_id: str | None = None


def _iter_actor_items(actor_or_party: Any):
    if hasattr(actor_or_party, "inventory"):
        actor = actor_or_party
        actor.ensure_inventory_initialized()
        for item in list(actor.inventory.items or []):
            yield actor, item
        return
    members = list(getattr(getattr(actor_or_party, "party", actor_or_party), "members", []) or [])
    for actor in members:
        if not hasattr(actor, "inventory"):
            continue
        actor.ensure_inventory_initialized()
        for item in list(actor.inventory.items or []):
            yield actor, item


def apply_identify_to_item(
    actor_or_party: Any,
    instance_id: str,
    *,
    reveal_curse: bool = False,
    reveal_effects: bool = False,
) -> ItemStateSpellResult:
    iid = str(instance_id or "")
    for actor, item in _iter_actor_items(actor_or_party):
        if str(getattr(item, "instance_id", "")) != iid:
            continue
        changed = False
        if not bool(getattr(item, "identified", True)):
            item.identified = True
            changed = True
        try:
            tmpl = get_item_template(str(getattr(item, "template_id", "") or ""))
        except Exception:
            tmpl = None
        if tmpl is not None:
            new_name = str(getattr(tmpl, "identified_name", None) or getattr(tmpl, "name", "") or getattr(item, "name", ""))
            if new_name and str(getattr(item, "name", "")) != new_name:
                item.name = new_name
                changed = True
            if reveal_curse and bool(getattr(tmpl, "cursed", False)) and not bool(getattr(item, "cursed_known", False)):
                item.cursed_known = True
                changed = True
        if reveal_effects:
            md = dict(getattr(item, "metadata", {}) or {})
            if "effects" not in md:
                md["effects"] = [dict(e) for e in list(item_effects(item) or [])]
                item.metadata = md
                changed = True
        return ItemStateSpellResult(ok=True, changed=changed, actor_name=str(getattr(actor, "name", "")), instance_id=iid)
    return ItemStateSpellResult(ok=False, error="item not found", instance_id=iid)


def apply_remove_curse_to_item(
    actor_or_party: Any,
    instance_id: str,
    *,
    suppress_sticky: bool = True,
) -> ItemStateSpellResult:
    iid = str(instance_id or "")
    for actor, item in _iter_actor_items(actor_or_party):
        if str(getattr(item, "instance_id", "")) != iid:
            continue
        changed = False
        md = dict(getattr(item, "metadata", {}) or {})
        cursed = bool(md.get("cursed", False)) or bool(item_is_cursed(item))
        if cursed:
            if not bool(getattr(item, "cursed_known", False)):
                item.cursed_known = True
                changed = True
            if suppress_sticky:
                if not bool(md.get("curse_suppressed", False)):
                    md["curse_suppressed"] = True
                    item.metadata = md
                    changed = True
        return ItemStateSpellResult(ok=True, changed=changed, actor_name=str(getattr(actor, "name", "")), instance_id=iid)
    return ItemStateSpellResult(ok=False, error="item not found", instance_id=iid)
