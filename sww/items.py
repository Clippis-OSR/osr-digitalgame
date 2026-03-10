"""Minimal item registry + template instantiation (P4.10.2).

This module is intentionally small and deterministic-friendly:
- Items are *data-defined* (p44/data/items_magic_v1.json)
- Templates (e.g. weapon_plus1_any) are expanded deterministically using RNG streams

The intent is to mirror the CL/treasure compilation philosophy:
no silent stubs, stable IDs, and explicit instance payloads.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    s = name.strip().lower()
    s = _SLUG_RE.sub("_", s)
    s = s.strip("_")
    return s or "item"


@dataclass(frozen=True)
class ItemRegistry:
    items_by_id: Dict[str, Dict[str, Any]]
    templates_by_id: Dict[str, Dict[str, Any]]

    @property
    def item_ids(self) -> set[str]:
        return set(self.items_by_id.keys())

    @property
    def template_ids(self) -> set[str]:
        return set(self.templates_by_id.keys())


def load_magic_items(root: Path) -> ItemRegistry:
    """Load the minimal magic item dataset."""
    path = root / "data" / "items_magic_v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    items = {str(it["item_id"]): it for it in (data.get("items") or [])}
    templates = {str(t["template_id"]): t for t in (data.get("templates") or [])}
    return ItemRegistry(items_by_id=items, templates_by_id=templates)


def _load_weapon_pool(root: Path) -> List[Dict[str, Any]]:
    melee = json.loads((root / "data" / "weapons_melee.json").read_text(encoding="utf-8"))
    missile = json.loads((root / "data" / "weapons_missile.json").read_text(encoding="utf-8"))
    pool: List[Dict[str, Any]] = []
    for w in melee:
        pool.append({"name": w["name"], "damage": w.get("damage"), "kind": "weapon"})
    for w in missile:
        pool.append({"name": w["name"], "damage": w.get("damage"), "kind": "weapon"})
    return pool


def _load_armor_pool(root: Path) -> List[Dict[str, Any]]:
    armor = json.loads((root / "data" / "armor_table_24.json").read_text(encoding="utf-8"))
    pool: List[Dict[str, Any]] = []
    for a in armor:
        pool.append(
            {
                "name": a["name"],
                "ac_mod_desc": a.get("ac_mod_desc"),
                "ac_mod_asc": a.get("ac_mod_asc"),
                "kind": "armor",
            }
        )
    return pool


def instantiate_magic_item(
    *,
    root: Path,
    registry: ItemRegistry,
    item_or_template_id: str,
    rng: Any,
    instance_tag: str,
) -> Dict[str, Any]:
    """Create a concrete item instance dict.

    `rng` is expected to be your deterministic RNG stream object (supports rng.roll("1dN")).
    `instance_tag` is a stable caller-provided string used for instance_id namespacing (e.g. room_id).
    """

    iid = str(item_or_template_id).strip()
    if iid in registry.items_by_id:
        base = registry.items_by_id[iid]
        return {
            "item_instance_id": f"{iid}::{instance_tag}",
            "item_id": iid,
            "name": base.get("name", iid),
            "kind": base.get("kind", "magic"),
            "value_gp": base.get("value_gp"),
            "payload": base.get("payload"),
            "effects": base.get("effects"),
        }

    if iid not in registry.templates_by_id:
        raise KeyError(f"Unknown item/template id: {iid}")

    tmpl = registry.templates_by_id[iid]
    kind = str(tmpl.get("kind"))
    bonus = int(tmpl.get("bonus", 0))

    if kind == "weapon_enchant_any":
        pool = _load_weapon_pool(root)
        roll = int(rng.roll(f"1d{len(pool)}"))
        chosen = pool[roll - 1]
        base_slug = slugify(chosen["name"])
        item_id = f"weapon_{base_slug}_plus{bonus}"
        return {
            "item_instance_id": f"{item_id}::{instance_tag}",
            "item_id": item_id,
            "template_id": iid,
            "name": f"{chosen['name']} +{bonus}",
            "kind": "weapon",
            "payload": {
                "base_name": chosen["name"],
                "damage": chosen.get("damage"),
                "to_hit_bonus": bonus,
                "damage_bonus": bonus,
            },
        }

    if kind == "armor_enchant_any":
        pool = _load_armor_pool(root)
        roll = int(rng.roll(f"1d{len(pool)}"))
        chosen = pool[roll - 1]
        base_slug = slugify(chosen["name"])
        item_id = f"armor_{base_slug}_plus{bonus}"
        # In ascending AC systems, higher is better; in descending, lower is better.
        # We store the bonus separately; application happens in the AC pipeline.
        return {
            "item_instance_id": f"{item_id}::{instance_tag}",
            "item_id": item_id,
            "template_id": iid,
            "name": f"{chosen['name']} +{bonus}",
            "kind": "armor",
            "payload": {
                "base_name": chosen["name"],
                "ac_bonus": bonus,
                "ac_mod_desc": chosen.get("ac_mod_desc"),
                "ac_mod_asc": chosen.get("ac_mod_asc"),
            },
        }

    raise ValueError(f"Unsupported template kind: {kind}")
