"""Thin, data-driven runtime item-template layer.

This module bridges existing extracted equipment/magic-item data into a single
runtime template catalog used by per-character inventory/equipment systems.

Current support:
- Fully derived from extracted data: weapon, armor, shield, ammo.
- Explicit bespoke templates: potion, scroll, ring, wand (from items_magic_v1).
- Lightweight placeholders: gear, treasure (simple generic templates).

This is intentionally incremental and does not replace the broader content
subsystem yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
from typing import Any

from .data import load_json
from .models import ItemInstance


@dataclass(frozen=True)
class ItemTemplate:
    template_id: str
    name: str
    category: str
    weight_lb: float = 0.0
    value_gp: float = 0.0
    tags: list[str] = field(default_factory=list)
    equip_slot_hint: str | None = None
    magic: bool = False
    cursed: bool = False
    identified_name: str | None = None
    unidentified_name: str | None = None
    wear_category: str | None = None
    effects: list[dict[str, Any]] = field(default_factory=list)
    charges: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    rules: dict[str, Any] = field(default_factory=dict)


SUPPORTED_ITEM_EFFECT_TYPES: set[str] = {
    "attack_bonus",
    "damage_bonus",
    "ac_bonus",
    "save_bonus",
    "movement_bonus",
    "granted_tag",
    "sticky_equip",
    "light_source",
    "consumable_heal",
    "spell_cast",
}

_EFFECT_TYPE_ALIASES: dict[str, str] = {
    "heal": "consumable_heal",
    "cast_spell": "spell_cast",
}


_INSTANCE_SEQ = count(1)


def _next_instance_id() -> str:
    return f"itm-{next(_INSTANCE_SEQ):06d}"


def _slot_hint_for_category(category: str) -> str | None:
    c = str(category or "").strip().lower()
    if c == "weapon":
        return "main_hand"
    if c == "armor":
        return "armor"
    if c == "shield":
        return "shield"
    if c == "ammo":
        return "ammo_kind"
    if c in {"ring", "gear"}:
        return "worn_misc"
    if c == "wand":
        return "main_hand"
    return None


def _normalized_effects(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        effect_type = str(row.get("type") or row.get("effect_type") or row.get("effect") or "").strip().lower()
        if not effect_type:
            continue
        mapped = _EFFECT_TYPE_ALIASES.get(effect_type, effect_type)
        payload = dict(row)
        payload["type"] = mapped
        out.append(payload)
    return out


class ItemTemplateCatalog:
    """Runtime item template catalog.

    Templates are keyed by `template_id` and can be looked up for loot, inventory,
    and item-instance construction.
    """

    def __init__(self) -> None:
        self._templates: dict[str, ItemTemplate] = {}

    def add(self, t: ItemTemplate) -> None:
        self._templates[str(t.template_id)] = t

    def get_item_template(self, template_id: str) -> ItemTemplate:
        tid = str(template_id or "")
        if tid not in self._templates:
            raise KeyError(f"Unknown item template_id: {tid}")
        return self._templates[tid]

    def all_templates(self) -> list[ItemTemplate]:
        return list(self._templates.values())


def _derive_weapon_templates(catalog: ItemTemplateCatalog) -> None:
    for row in list(load_json("weapons_melee.json") or []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        catalog.add(
            ItemTemplate(
                template_id=f"weapon.{name.lower().replace(' ', '_').replace(',', '').replace('(', '').replace(')', '')}",
                name=name,
                category="weapon",
                weight_lb=float(row.get("weight_lb", 0.0) or 0.0),
                value_gp=float(row.get("cost_gp", 0.0) or 0.0),
                tags=["weapon", "melee"],
                equip_slot_hint="main_hand",
                rules={"damage": row.get("damage"), "footnotes": list(row.get("footnotes") or [])},
            )
        )

    for row in list(load_json("weapons_missile.json") or []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        lower = name.lower()
        if "arrow" in lower or "bolt" in lower or "stone" in lower:
            category = "ammo"
            tags = ["ammo"]
            slot = "ammo_kind"
        else:
            category = "weapon"
            tags = ["weapon", "missile"]
            slot = "missile_weapon"
        catalog.add(
            ItemTemplate(
                template_id=f"{category}.{name.lower().replace(' ', '_').replace(',', '').replace('(', '').replace(')', '')}",
                name=name,
                category=category,
                weight_lb=float(row.get("weight_lb", 0.0) or 0.0),
                value_gp=float(row.get("cost_gp", 0.0) or 0.0),
                tags=tags,
                equip_slot_hint=slot,
                rules={
                    "damage": row.get("damage"),
                    "rate_of_fire": row.get("rate_of_fire"),
                    "range": row.get("range"),
                },
            )
        )


def _derive_armor_templates(catalog: ItemTemplateCatalog) -> None:
    for row in list(load_json("armor_table_24.json") or []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        category = "shield" if name.lower() == "shield" else "armor"
        catalog.add(
            ItemTemplate(
                template_id=f"{category}.{name.lower()}",
                name=name,
                category=category,
                weight_lb=float(row.get("weight_lb", 0.0) or 0.0),
                value_gp=float(row.get("cost_gp", 0.0) or 0.0),
                tags=[category],
                equip_slot_hint="shield" if category == "shield" else "armor",
                rules={"ac_mod_desc": row.get("ac_mod_desc"), "ac_mod_asc": row.get("ac_mod_asc")},
            )
        )


def _add_bespoke_templates(catalog: ItemTemplateCatalog) -> None:
    # Magic items (potion/scroll/ring/wand) from extracted file.
    magic = load_json("items_magic_v1.json") or {}
    for row in list((magic or {}).get("items") or []):
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "").strip().lower()
        if kind not in {"potion", "scroll", "ring", "wand", "treasure", "gear", "ammo"}:
            continue
        item_id = str(row.get("item_id") or "").strip()
        name = str(row.get("name") or item_id)
        if not item_id:
            continue
        catalog.add(
            ItemTemplate(
                template_id=f"{kind}.{item_id}",
                name=name,
                category=kind,
                weight_lb=float((row.get("weight_lb") if row.get("weight_lb") is not None else 0.0) or 0.0),
                value_gp=float(row.get("value_gp", 0.0) or 0.0),
                tags=[str(t) for t in (row.get("tags") or [])],
                equip_slot_hint=_slot_hint_for_category(kind),
                magic=True,
                cursed=bool(row.get("cursed", False)),
                identified_name=row.get("identified_name"),
                unidentified_name=row.get("unidentified_name"),
                wear_category=row.get("wear_category"),
                effects=_normalized_effects(list(row.get("effects") or [])),
                charges=(int(row["charges"]) if row.get("charges") is not None else None),
                metadata=dict(row.get("metadata") or {}),
                rules={
                    "effects": _normalized_effects(list(row.get("effects") or [])),
                    "payload": dict(row.get("payload") or {}),
                },
            )
        )

    # General equipment as simple gear templates (placeholder weights at 0 by default).
    for row in list(load_json("equipment_general.json") or []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        tid = f"gear.{name.lower().replace(' ', '_').replace(',', '').replace('(', '').replace(')', '')}"
        if tid in {t.template_id for t in catalog.all_templates()}:
            continue
        catalog.add(
            ItemTemplate(
                template_id=tid,
                name=name,
                category="gear",
                weight_lb=0.0,
                value_gp=float(row.get("cost_gp", 0.0) or 0.0),
                tags=["gear"],
                equip_slot_hint="worn_misc",
                rules={"notes": row.get("notes")},
            )
        )

    # Minimal generic treasure placeholder for runtime convenience.
    catalog.add(
        ItemTemplate(
            template_id="treasure.generic",
            name="Treasure",
            category="treasure",
            weight_lb=0.0,
            value_gp=0.0,
            tags=["treasure"],
            equip_slot_hint=None,
            rules={},
        )
    )


def build_item_template_catalog() -> ItemTemplateCatalog:
    catalog = ItemTemplateCatalog()
    _derive_weapon_templates(catalog)
    _derive_armor_templates(catalog)
    _add_bespoke_templates(catalog)
    return catalog


_DEFAULT_CATALOG: ItemTemplateCatalog | None = None


def _default_catalog() -> ItemTemplateCatalog:
    global _DEFAULT_CATALOG
    if _DEFAULT_CATALOG is None:
        _DEFAULT_CATALOG = build_item_template_catalog()
    return _DEFAULT_CATALOG


def get_item_template(template_id: str) -> ItemTemplate:
    return _default_catalog().get_item_template(template_id)


def find_template_id_by_name(name: str, *, preferred_categories: list[str] | None = None) -> str | None:
    """Best-effort lookup by display name for migration bridges."""
    nm = str(name or "").strip().lower()
    if not nm:
        return None
    cats = [c.strip().lower() for c in (preferred_categories or []) if str(c).strip()]
    for t in _default_catalog().all_templates():
        if str(t.name).strip().lower() != nm:
            continue
        if cats and str(t.category).strip().lower() not in cats:
            continue
        return t.template_id
    return None


def template_weight_lb(template: ItemTemplate) -> float:
    return max(0.0, float(template.weight_lb or 0.0))


def _coerce_template(template_or_instance: ItemTemplate | ItemInstance | None) -> ItemTemplate | None:
    if isinstance(template_or_instance, ItemTemplate):
        return template_or_instance
    if isinstance(template_or_instance, ItemInstance):
        try:
            return get_item_template(template_or_instance.template_id)
        except KeyError:
            return None
    return None


def item_is_magic(template_or_instance: ItemTemplate | ItemInstance | None) -> bool:
    t = _coerce_template(template_or_instance)
    if t is not None:
        return bool(t.magic)
    if isinstance(template_or_instance, ItemInstance):
        return "magic" in {str(x).lower() for x in list((template_or_instance.metadata or {}).get("tags") or [])}
    return False


def item_is_cursed(template_or_instance: ItemTemplate | ItemInstance | None) -> bool:
    t = _coerce_template(template_or_instance)
    if t is not None:
        return bool(t.cursed)
    if isinstance(template_or_instance, ItemInstance):
        return bool((template_or_instance.metadata or {}).get("cursed", False))
    return False


def item_effects(template_or_instance: ItemTemplate | ItemInstance | None) -> list[dict[str, Any]]:
    if isinstance(template_or_instance, ItemInstance):
        from_metadata = _normalized_effects(list((template_or_instance.metadata or {}).get("effects") or []))
        if from_metadata:
            return from_metadata
    t = _coerce_template(template_or_instance)
    if t is not None:
        return [dict(e) for e in list(t.effects or [])]
    return []


def item_unsupported_effect_types(template_or_instance: ItemTemplate | ItemInstance | None) -> list[str]:
    types = [str(e.get("type") or "") for e in item_effects(template_or_instance)]
    return sorted({t for t in types if t and t not in SUPPORTED_ITEM_EFFECT_TYPES})


def item_display_name(
    instance: ItemInstance,
    template: ItemTemplate | None = None,
    *,
    identified: bool | None = None,
) -> str:
    t = template or _coerce_template(instance)
    is_identified = bool(instance.identified if identified is None else identified)
    if t is None:
        return str(instance.name or "Unknown item")
    if is_identified:
        return str(t.identified_name or t.name)
    return str(t.unidentified_name or t.wear_category or "Unidentified magic item")


def build_item_instance(
    template_id: str,
    *,
    quantity: int = 1,
    identified: bool = True,
    equipped: bool = False,
    magic_bonus: int = 0,
    instance_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ItemInstance:
    t = get_item_template(template_id)
    iid = str(instance_id or _next_instance_id())
    q = max(1, int(quantity or 1))
    md = dict(metadata or {})
    md.setdefault("weight_lb", template_weight_lb(t))
    md.setdefault("value_gp", float(t.value_gp or 0.0))
    md.setdefault("equip_slot_hint", t.equip_slot_hint)
    md.setdefault("magic", bool(t.magic))
    md.setdefault("cursed", bool(t.cursed))
    md.setdefault("identified_name", t.identified_name)
    md.setdefault("unidentified_name", t.unidentified_name)
    md.setdefault("wear_category", t.wear_category)
    md.setdefault("effects", [dict(e) for e in list(t.effects or [])])
    md.setdefault("charges", t.charges)
    md.setdefault("template_metadata", dict(t.metadata or {}))
    md.setdefault("unsupported_effect_types", item_unsupported_effect_types(t))
    md.setdefault("rules", dict(t.rules or {}))

    return ItemInstance(
        instance_id=iid,
        template_id=t.template_id,
        name=t.name,
        category=t.category,
        quantity=q,
        identified=bool(identified),
        equipped=bool(equipped),
        magic_bonus=int(magic_bonus or 0),
        metadata=md,
    )
