from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List

from .data import load_json


@lru_cache(maxsize=1)
def _theme_payload() -> Dict[str, Any]:
    payload = load_json("dungeon_gen/dungeon_themes.json") or {}
    if not isinstance(payload, dict):
        return {"themes": [], "role_defaults": {}}
    payload.setdefault("themes", [])
    payload.setdefault("role_defaults", {})
    return payload


def dungeon_theme_catalog() -> List[Dict[str, Any]]:
    themes = _theme_payload().get("themes") or []
    return [dict(t) for t in themes if isinstance(t, dict)]


def dungeon_role_defaults() -> Dict[str, Any]:
    defaults = _theme_payload().get("role_defaults") or {}
    return dict(defaults) if isinstance(defaults, dict) else {}


def pick_theme_by_index(index: int) -> Dict[str, Any]:
    catalog = dungeon_theme_catalog()
    if not catalog:
        return {
            "theme_id": "unknown",
            "name": "Unknown Depths",
            "faction": "unknown",
            "secondary_faction": "unknown",
            "monster_keywords": [],
            "hazards": [],
            "shrines": [],
            "features": [],
            "role_weight_modifiers": {},
            "adjacency_bias": {},
            "special_packs": {},
        }
    return dict(catalog[int(index) % len(catalog)])
