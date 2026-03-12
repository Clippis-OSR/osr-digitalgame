from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .models import (
    Actor,
    CharacterEquipment,
    CharacterInventory,
    ItemInstance,
    Party,
    Stats,
)
from .travel_state import TravelState
from .events import (
    normalize_player_event,
    append_player_event,
    project_clues_from_events,
    project_discoveries_from_events,
    project_rumors_from_events,
    project_district_notes_from_events,
)

# Increment whenever the on-disk schema changes.
# We write both "save_version" and the legacy "version" key for compatibility.
SAVE_VERSION = 14

class SaveSchemaError(ValueError):
    """Raised when a save file is missing required keys or has invalid types."""


def _is_strict_content() -> bool:
    try:
        return str(os.environ.get("STRICT_CONTENT", "")).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return False


def _type_name(x: Any) -> str:
    try:
        return type(x).__name__
    except Exception:
        return "unknown"


def _schema_fail(path: str, msg: str) -> None:
    raise SaveSchemaError(f"Save schema error at '{path}': {msg}")


def _ensure_dict(parent: dict, key: str, path: str, default: Optional[dict] = None, strict: bool = False) -> dict:
    if default is None:
        default = {}
    if key not in parent or parent.get(key) is None:
        if strict:
            _schema_fail(path, "missing required object")
        parent[key] = dict(default)
        return parent[key]
    val = parent.get(key)
    if not isinstance(val, dict):
        if strict:
            _schema_fail(path, f"expected object, got {_type_name(val)}")
        parent[key] = dict(default)
        return parent[key]
    return val


def _ensure_list(parent: dict, key: str, path: str, default: Optional[list] = None, strict: bool = False) -> list:
    if default is None:
        default = []
    if key not in parent or parent.get(key) is None:
        if strict:
            _schema_fail(path, "missing required array")
        parent[key] = list(default)
        return parent[key]
    val = parent.get(key)
    if not isinstance(val, list):
        if strict:
            _schema_fail(path, f"expected array, got {_type_name(val)}")
        parent[key] = list(default)
        return parent[key]
    return val


def _ensure_int(parent: dict, key: str, path: str, default: int = 0, strict: bool = False) -> int:
    if key not in parent or parent.get(key) is None:
        if strict:
            _schema_fail(path, "missing required integer")
        parent[key] = int(default)
        return int(default)
    try:
        v = int(parent.get(key))
        parent[key] = v
        return v
    except Exception:
        if strict:
            _schema_fail(path, f"expected integer, got {_type_name(parent.get(key))}")
        parent[key] = int(default)
        return int(default)


def _ensure_str(parent: dict, key: str, path: str, default: str = "", strict: bool = False) -> str:
    if key not in parent or parent.get(key) is None:
        if strict:
            _schema_fail(path, "missing required string")
        parent[key] = default
        return default
    val = parent.get(key)
    if not isinstance(val, str):
        if strict:
            _schema_fail(path, f"expected string, got {_type_name(val)}")
        parent[key] = default
        return default
    return val




def _serialize_rng_states(game: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for attr in ("dice_rng", "levelup_rng", "temple_rng", "wilderness_rng", "wilderness_encounter_rng"):
        rng = getattr(game, attr, None)
        if rng is None or not hasattr(rng, "export_state"):
            continue
        try:
            out[attr] = rng.export_state()
        except Exception:
            continue
    return out


def _restore_rng_states(game: Any, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    for attr, blob in payload.items():
        rng = getattr(game, attr, None)
        if rng is None or not hasattr(rng, "import_state"):
            continue
        try:
            rng.import_state(blob)
        except Exception:
            continue

def validate_and_guard_save_data(data: Any, strict: bool) -> Dict[str, Any]:
    """Validate save payload shape and apply safe defaults when not strict.

    Lightweight and defensive: prevents crashes from malformed saves and produces clear
    errors in STRICT_CONTENT mode.
    """
    if not isinstance(data, dict):
        _schema_fail("$", f"expected top-level object, got {_type_name(data)}")

    # Ensure some version key exists so migration has a starting point.
    if "save_version" not in data and "version" not in data:
        if strict:
            _schema_fail("save_version", "missing save_version/version")
        data["save_version"] = 1
        data["version"] = 1

    _ensure_dict(data, "meta", "meta", default={"created_utc": _utc_now_iso()}, strict=strict)
    _ensure_dict(data, "campaign", "campaign", default={}, strict=strict)
    _ensure_dict(data, "dungeon", "dungeon", default={}, strict=False)
    # P6.1: optional dungeon_instance container under dungeon
    try:
        dung = data.get("dungeon") or {}
        if isinstance(dung, dict) and "dungeon_instance" in dung and dung.get("dungeon_instance") is not None and not isinstance(dung.get("dungeon_instance"), dict):
            if strict:
                _schema_fail("dungeon.dungeon_instance", f"expected object, got {_type_name(dung.get('dungeon_instance'))}")
            dung["dungeon_instance"] = None
            data["dungeon"] = dung
    except Exception:
        pass
    _ensure_dict(data, "wilderness", "wilderness", default={}, strict=False)
    _ensure_dict(data, "replay", "replay", default={}, strict=False)
    _ensure_dict(data, "journal", "journal", default={}, strict=False)
    _ensure_dict(data, "events", "events", default={}, strict=False)
    _ensure_dict(data, "factions", "factions", default={}, strict=False)
    _ensure_dict(data, "clocks", "clocks", default={}, strict=False)
    _ensure_dict(data, "contracts", "contracts", default={}, strict=False)
    _ensure_dict(data, "boons", "boons", default={}, strict=False)
    _ensure_dict(data, "content", "content", default={"hashes": {}, "engine_version": None}, strict=False)

    # Campaign essentials
    camp = data["campaign"]
    _ensure_int(camp, "campaign_day", "campaign.campaign_day", default=1, strict=strict)
    _ensure_int(camp, "day_watch", "campaign.day_watch", default=0, strict=strict)
    _ensure_int(camp, "gold", "campaign.gold", default=0, strict=strict)
    _ensure_dict(camp, "party", "campaign.party", default={"members": []}, strict=strict)
    party = camp["party"]
    _ensure_list(party, "members", "campaign.party.members", default=[], strict=strict)

    # Wilderness essentials
    wild = data["wilderness"]
    ph = wild.get("party_hex")
    if ph is None:
        wild["party_hex"] = [0, 0]
    elif not (isinstance(ph, (list, tuple)) and len(ph) == 2):
        if strict:
            _schema_fail("wilderness.party_hex", "expected [q, r] array of length 2")
        wild["party_hex"] = [0, 0]
    else:
        try:
            wild["party_hex"] = [int(ph[0]), int(ph[1])]
        except Exception:
            if strict:
                _schema_fail("wilderness.party_hex", "hex coords must be integers")
            wild["party_hex"] = [0, 0]
    if not isinstance(wild.get("world_hexes", {}), dict):
        if strict:
            _schema_fail("wilderness.world_hexes", f"expected object, got {_type_name(wild.get('world_hexes'))}")
        wild["world_hexes"] = {}

    ts = wild.get("travel_state")
    if ts is not None and not isinstance(ts, dict):
        if strict:
            _schema_fail("wilderness.travel_state", f"expected object, got {_type_name(ts)}")
        wild["travel_state"] = {}

    # Dungeon essentials
    dung = data["dungeon"]
    if not isinstance(dung.get("dungeon_rooms", {}), dict):
        if strict:
            _schema_fail("dungeon.dungeon_rooms", f"expected object, got {_type_name(dung.get('dungeon_rooms'))}")
        dung["dungeon_rooms"] = {}

    ev = data["events"]
    if not isinstance(ev.get("event_history", []), list):
        if strict:
            _schema_fail("events.event_history", f"expected array, got {_type_name(ev.get('event_history'))}")
        ev["event_history"] = []
    try:
        ev["next_eid"] = int(ev.get("next_eid", len(ev.get("event_history", []) or [])) or 0)
    except Exception:
        ev["next_eid"] = int(len(ev.get("event_history", []) or []))

    # Replay essentials
    rep = data["replay"]
    if not isinstance(rep.get("commands", []), list):
        rep["commands"] = []
    if not isinstance(rep.get("rng_events", []), list):
        rep["rng_events"] = []
    if not isinstance(rep.get("ai_events", []), list):
        rep["ai_events"] = []

    return data


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def build_save_metadata(game: Any) -> Dict[str, Any]:
    """Build human-friendly metadata to display in slot lists."""
    try:
        members = getattr(getattr(game, "party", None), "members", []) or []
        pc_names = [getattr(m, "name", "?") for m in members if getattr(m, "is_pc", False)]
    except Exception:
        pc_names = []

    # Wilderness summary
    try:
        q, r = getattr(game, "party_hex", (0, 0))
    except Exception:
        q, r = (0, 0)

    return {
        "created_utc": _utc_now_iso(),
        "engine": "SWW Text CRPG",
        "summary": {
            "day": _safe_int(getattr(game, "campaign_day", 1), 1),
            "watch": _safe_int(getattr(game, "day_watch", 0), 0),
            "gold": _safe_int(getattr(game, "gold", 0), 0),
            "pcs": pc_names,
            "dungeon_depth": _safe_int(getattr(game, "dungeon_depth", 1), 1),
            "dungeon_level": _safe_int(getattr(game, "dungeon_level", 1), 1),
            "hex": [int(q), int(r)],
        },
    }


def _stats_to_dict(stats: Optional[Stats]) -> Optional[dict]:
    if stats is None:
        return None
    return asdict(stats) if is_dataclass(stats) else dict(stats)  # type: ignore[arg-type]


def _dict_to_stats(d: Optional[dict]) -> Optional[Stats]:
    if not d:
        return None
    return Stats(**d)




def _item_instance_to_dict(item: Any) -> dict:
    if isinstance(item, ItemInstance):
        return {
            "instance_id": str(item.instance_id or ""),
            "template_id": str(item.template_id or ""),
            "name": str(item.name or ""),
            "category": str(item.category or "misc"),
            "quantity": int(item.quantity or 1),
            "identified": bool(item.identified),
            "cursed_known": bool(getattr(item, "cursed_known", False)),
            "custom_label": (None if getattr(item, "custom_label", None) is None else str(getattr(item, "custom_label", ""))),
            "equipped": bool(item.equipped),
            "magic_bonus": int(item.magic_bonus or 0),
            "metadata": dict(item.metadata or {}),
        }
    if isinstance(item, dict):
        return {
            "instance_id": str(item.get("instance_id") or ""),
            "template_id": str(item.get("template_id") or item.get("item_id") or ""),
            "name": str(item.get("name") or ""),
            "category": str(item.get("category") or item.get("kind") or "misc"),
            "quantity": int(item.get("quantity", 1) or 1),
            "identified": bool(item.get("identified", True)),
            "cursed_known": bool(item.get("cursed_known", False)),
            "custom_label": (None if item.get("custom_label") is None else str(item.get("custom_label") or "")),
            "equipped": bool(item.get("equipped", False)),
            "magic_bonus": int(item.get("magic_bonus", 0) or 0),
            "metadata": dict(item.get("metadata") or item.get("data") or {}),
        }
    return {
        "instance_id": "",
        "template_id": "",
        "name": "",
        "category": "misc",
        "quantity": 1,
        "identified": True,
        "cursed_known": False,
        "custom_label": None,
        "equipped": False,
        "magic_bonus": 0,
        "metadata": {},
    }


def _dict_to_item_instance(d: Any) -> ItemInstance:
    if not isinstance(d, dict):
        return ItemInstance(instance_id="", template_id="", name="", category="misc")
    return ItemInstance(
        instance_id=str(d.get("instance_id") or ""),
        template_id=str(d.get("template_id") or d.get("item_id") or ""),
        name=str(d.get("name") or ""),
        category=str(d.get("category") or d.get("kind") or "misc"),
        quantity=int(d.get("quantity", 1) or 1),
        identified=bool(d.get("identified", True)),
        cursed_known=bool(d.get("cursed_known", False)),
        custom_label=(None if d.get("custom_label") is None else str(d.get("custom_label") or "")),
        equipped=bool(d.get("equipped", False)),
        magic_bonus=int(d.get("magic_bonus", 0) or 0),
        metadata=dict(d.get("metadata") or d.get("data") or {}),
    )


def _inventory_to_dict(inv: Any) -> dict:
    if isinstance(inv, CharacterInventory):
        items = [_item_instance_to_dict(it) for it in (inv.items or [])]
        return {
            "items": items,
            "coins_gp": int(inv.coins_gp or 0),
            "coins_sp": int(inv.coins_sp or 0),
            "coins_cp": int(inv.coins_cp or 0),
            "torches": int(inv.torches or 0),
            "rations": int(inv.rations or 0),
            "ammo": dict(inv.ammo or {}),
        }
    if isinstance(inv, dict):
        return {
            "items": [_item_instance_to_dict(it) for it in (inv.get("items") or [])],
            "coins_gp": int(inv.get("coins_gp", 0) or 0),
            "coins_sp": int(inv.get("coins_sp", 0) or 0),
            "coins_cp": int(inv.get("coins_cp", 0) or 0),
            "torches": int(inv.get("torches", 0) or 0),
            "rations": int(inv.get("rations", 0) or 0),
            "ammo": dict(inv.get("ammo") or {}),
        }
    return {"items": [], "coins_gp": 0, "coins_sp": 0, "coins_cp": 0, "torches": 0, "rations": 0, "ammo": {}}


def _dict_to_inventory(d: Any) -> CharacterInventory:
    if not isinstance(d, dict):
        return CharacterInventory()
    return CharacterInventory(
        items=[_dict_to_item_instance(it) for it in (d.get("items") or [])],
        coins_gp=int(d.get("coins_gp", 0) or 0),
        coins_sp=int(d.get("coins_sp", 0) or 0),
        coins_cp=int(d.get("coins_cp", 0) or 0),
        torches=int(d.get("torches", 0) or 0),
        rations=int(d.get("rations", 0) or 0),
        ammo=dict(d.get("ammo") or {}),
    )


def _equipment_to_dict(eq: Any) -> dict:
    if isinstance(eq, CharacterEquipment):
        return {
            "armor": eq.armor,
            "shield": eq.shield,
            "main_hand": eq.main_hand,
            "off_hand": eq.off_hand,
            "missile_weapon": eq.missile_weapon,
            "ammo_kind": eq.ammo_kind,
            "worn_misc": list(eq.worn_misc or []),
        }
    if isinstance(eq, dict):
        return {
            "armor": eq.get("armor"),
            "shield": eq.get("shield"),
            "main_hand": eq.get("main_hand"),
            "off_hand": eq.get("off_hand"),
            "missile_weapon": eq.get("missile_weapon"),
            "ammo_kind": eq.get("ammo_kind") or eq.get("ammo"),
            "worn_misc": list(eq.get("worn_misc") or []),
        }
    return {
        "armor": None, "shield": None, "main_hand": None, "off_hand": None,
        "missile_weapon": None, "ammo_kind": None, "worn_misc": []
    }


def _dict_to_equipment(d: Any) -> CharacterEquipment:
    if not isinstance(d, dict):
        return CharacterEquipment()
    return CharacterEquipment(
        armor=d.get("armor"),
        shield=d.get("shield"),
        main_hand=d.get("main_hand"),
        off_hand=d.get("off_hand"),
        missile_weapon=d.get("missile_weapon"),
        ammo_kind=d.get("ammo_kind") or d.get("ammo"),
        worn_misc=list(d.get("worn_misc") or []),
    )

def actor_to_dict(a: Any) -> dict:
    """Serialize an Actor/PC/retainer into JSON-friendly dict.

    TODO(inventory-migration): keep writing legacy weapon/armor/shield fields
    until all runtime systems read CharacterEquipment directly.

    We store core Actor fields plus any extra attributes present (e.g., PC cls/level/xp/stats).
    """
    if hasattr(a, "ensure_inventory_initialized"):
        a.ensure_inventory_initialized()
    if hasattr(a, "sync_legacy_equipment_to_new"):
        a.sync_legacy_equipment_to_new()

    base = {
        "monster_id": getattr(a, "monster_id", None),
        "name": getattr(a, "name", ""),
        "hp": getattr(a, "hp", 0),
        "hp_max": getattr(a, "hp_max", 0),
        "ac_desc": getattr(a, "ac_desc", 9),
        "hd": getattr(a, "hd", 1),
        "save": getattr(a, "save", 16),
        "morale": getattr(a, "morale", None),
        "alignment": getattr(a, "alignment", "Neutrality"),
        "is_pc": bool(getattr(a, "is_pc", False)),
        "is_retainer": bool(getattr(a, "is_retainer", False)),
        "effects": list(getattr(a, "effects", []) or []),
        "special_text": getattr(a, "special_text", ""),
        "tags": list(getattr(a, "tags", []) or []),
        "spells_known": list(getattr(a, "spells_known", []) or []),
        "spells_prepared": list(getattr(a, "spells_prepared", []) or []),
        "status": dict(getattr(a, "status", {}) or {}),
        "effect_instances": dict(getattr(a, "effect_instances", {}) or {}),
        "loyalty": getattr(a, "loyalty", 7),
        "wage_gp": getattr(a, "wage_gp", 0),
        "treasure_share": getattr(a, "treasure_share", 1),
        "on_expedition": bool(getattr(a, "on_expedition", False)),
        "weapon": getattr(a, "weapon", None),
        "armor": getattr(a, "armor", None),
        "shield": bool(getattr(a, "shield", False)),
        "shield_name": getattr(a, "shield_name", None),
        "inventory": _inventory_to_dict(getattr(a, "inventory", None)),
        "equipment": _equipment_to_dict(getattr(a, "equipment", None)),
    }

    # PC extras (best-effort)
    if getattr(a, "is_pc", False):
        base["cls"] = getattr(a, "cls", "Fighter")
        base["level"] = int(getattr(a, "level", 1))
        base["xp"] = int(getattr(a, "xp", 0))
        base["stats"] = _stats_to_dict(getattr(a, "stats", None))

    return base



def migrate_legacy_actor_payload(payload: Any) -> dict:
    """Normalize legacy/new actor payloads into the transitional actor schema.

    Backward compatibility behavior:
    - Old saves missing `inventory`/`equipment` are initialized to empty defaults.
    - Legacy equipment fields are mirrored into `equipment` when possible.

    Temporary migration note:
    - Once all systems use `Actor.inventory` and `Actor.equipment`, this helper
      (and legacy field mirroring) should be removed.
    """
    p = dict(payload or {}) if isinstance(payload, dict) else {}

    inv = p.get("inventory") if isinstance(p.get("inventory"), dict) else {}
    inv = {
        "items": list(inv.get("items") or []),
        "coins_gp": int(inv.get("coins_gp", 0) or 0),
        "coins_sp": int(inv.get("coins_sp", 0) or 0),
        "coins_cp": int(inv.get("coins_cp", 0) or 0),
        "torches": int(inv.get("torches", 0) or 0),
        "rations": int(inv.get("rations", 0) or 0),
        "ammo": dict(inv.get("ammo") or {}),
    }

    eq = p.get("equipment") if isinstance(p.get("equipment"), dict) else {}
    if "ammo_kind" not in eq and "ammo" in eq:
        eq["ammo_kind"] = eq.get("ammo")
    eq = {
        "armor": eq.get("armor") if eq.get("armor") is not None else p.get("armor"),
        "shield": eq.get("shield") if eq.get("shield") is not None else (p.get("shield_name") if p.get("shield") else None),
        "main_hand": eq.get("main_hand") if eq.get("main_hand") is not None else p.get("weapon"),
        "off_hand": eq.get("off_hand"),
        "missile_weapon": eq.get("missile_weapon"),
        "ammo_kind": eq.get("ammo_kind"),
        "worn_misc": list(eq.get("worn_misc") or []),
    }

    p["inventory"] = inv
    p["equipment"] = eq
    return p


def actor_from_dict(d: dict) -> Actor:
    """Rehydrate an Actor/PC from payload (compat alias around dict_to_actor)."""
    return dict_to_actor(d)

def dict_to_actor(d: dict) -> Actor:
    """Rehydrate an Actor/PC.

    Migration note: both new inventory/equipment payloads and legacy weapon/armor
    fields are accepted in this transitional phase.

    If the project defines a PC class (e.g., sww.game.PC), we will instantiate it for PCs.
    Otherwise we fall back to Actor and attach extra attributes.
    """
    payload = migrate_legacy_actor_payload(d)
    is_pc = bool(payload.get("is_pc"))

    PC_cls = None
    if is_pc:
        try:
            # Import lazily to avoid circular imports
            from .game import PC as PC_cls  # type: ignore
        except Exception:
            PC_cls = None

    cls_ = PC_cls if (is_pc and PC_cls is not None) else Actor

    a = cls_(  # type: ignore[call-arg]
        name=payload.get("name", ""),
        hp=int(payload.get("hp", 0)),
        hp_max=int(payload.get("hp_max", 0)),
        ac_desc=int(payload.get("ac_desc", 9)),
        hd=int(payload.get("hd", 1)),
        save=int(payload.get("save", 16)),
        morale=payload.get("morale", None),
        alignment=payload.get("alignment", "Neutrality"),
        is_pc=is_pc,
        effects=list(payload.get("effects", []) or []),
        special_text=payload.get("special_text", ""),
        tags=list(payload.get("tags", []) or []),
        spells_known=list(payload.get("spells_known", []) or []),
        spells_prepared=list(payload.get("spells_prepared", []) or []),
        status=dict(payload.get("status", {}) or {}),
        effect_instances=dict(payload.get("effect_instances", {}) or {}),
        is_retainer=bool(payload.get("is_retainer", False)),
        loyalty=int(payload.get("loyalty", 7)),
        wage_gp=int(payload.get("wage_gp", 0)),
        treasure_share=int(payload.get("treasure_share", 1)),
        on_expedition=bool(payload.get("on_expedition", False)),
        weapon=payload.get("weapon", None),
        armor=payload.get("armor", None),
        shield=bool(payload.get("shield", False)),
        shield_name=payload.get("shield_name", None),
        inventory=_dict_to_inventory(payload.get("inventory")),
        equipment=_dict_to_equipment(payload.get("equipment")),
    )

    if hasattr(a, "ensure_inventory_initialized"):
        a.ensure_inventory_initialized()
    if hasattr(a, "ensure_equipment_initialized"):
        a.ensure_equipment_initialized()
    if hasattr(a, "sync_legacy_equipment_to_new"):
        a.sync_legacy_equipment_to_new()
    if hasattr(a, "sync_new_equipment_to_legacy"):
        a.sync_new_equipment_to_legacy()

    # Attach PC extras if needed
    if is_pc:
        setattr(a, "cls", payload.get("cls", "Fighter"))
        setattr(a, "level", int(payload.get("level", 1)))
        setattr(a, "xp", int(payload.get("xp", 0)))
        setattr(a, "stats", _dict_to_stats(payload.get("stats")))

    # P3.1.15: preserve stable monster identifier for non-PC actors
    if payload.get("monster_id") is not None:
        try:
            setattr(a, "monster_id", payload.get("monster_id"))
        except Exception:
            pass

    return a


def party_to_dict(p: Party) -> dict:
    return {"members": [actor_to_dict(m) for m in (p.members or [])]}


def dict_to_party(d: dict) -> Party:
    p = Party()
    p.members = [actor_from_dict(x) for x in (d.get("members") or [])]
    return p


def game_to_dict(game: Any) -> Dict[str, Any]:
    """Extract only the *persistent* campaign state.

    We intentionally do NOT store UI, RNG, or content DBs; they are reconstructed on load.
    """
    def _serialize_dungeon_rooms(rooms: Any) -> dict:
        """Dungeon rooms may contain live Actor objects (e.g. room['foes']).

        We persist them as plain dicts so saves are deterministic.
        """
        if not isinstance(rooms, dict):
            return {}
        out: dict = {}
        for rid, room in rooms.items():
            if not isinstance(room, dict):
                continue
            r = dict(room)
            foes = r.get("foes")
            if isinstance(foes, list):
                new_foes = []
                for f in foes:
                    if isinstance(f, Actor):
                        new_foes.append(actor_to_dict(f))
                    elif isinstance(f, dict):
                        new_foes.append(f)
                    else:
                        # best-effort: store repr
                        new_foes.append({"name": str(getattr(f, "name", "?")), "hp": int(getattr(f, "hp", 0))})
                r["foes"] = new_foes
            out[int(rid) if str(rid).isdigit() else rid] = r
        return out

    # Replay + content-lock information (P3.6). These are safe to persist.
    try:
        replay_dict = getattr(getattr(game, "replay", None), "to_dict", lambda: None)()
    except Exception:
        replay_dict = None
    try:
        content = getattr(game, "content", None)
        content_hashes = dict(getattr(content, "content_hashes", {}) or {}) if content is not None else {}
        engine_version = getattr(content, "engine_version", None) if content is not None else None
    except Exception:
        content_hashes = {}
        engine_version = None

    return {
        # Preferred key
        "save_version": SAVE_VERSION,
        # Legacy key kept for backward compatibility with earlier patches
        "version": SAVE_VERSION,
        "meta": build_save_metadata(game),
        "content": {
            "engine_version": engine_version,
            "hashes": content_hashes,
        },
        "replay": replay_dict or {
            "dice_seed": int(getattr(game, "dice_seed", 0) or 0),
            "wilderness_seed": int(getattr(game, "wilderness_seed", 0) or 0),
            "rng_states": _serialize_rng_states(game),
            "commands": [],
            "rng_events": [],
            "ai_events": [],
        },
        "campaign": {
            "campaign_day": int(getattr(game, "campaign_day", 1)),
            "day_watch": int(getattr(game, "day_watch", 0)),
            "gold": int(getattr(game, "gold", 0)),
            "party": party_to_dict(getattr(game, "party", Party())),
            "party_items": list(getattr(game, "party_items", []) or []),
            "torches": int(getattr(game, "torches", 0)),
            "rations": int(getattr(game, "rations", 0)),
            "arrows": int(getattr(game, "arrows", 0)),
            "bolts": int(getattr(game, "bolts", 0)),
            "stones": int(getattr(game, "stones", 0)),
            "hand_axes": int(getattr(game, "hand_axes", 0)),
            "throwing_daggers": int(getattr(game, "throwing_daggers", 0)),
            "darts": int(getattr(game, "darts", 0)),
            "javelins": int(getattr(game, "javelins", 0)),
            "throwing_spears": int(getattr(game, "throwing_spears", 0)),
            "terrain": getattr(game, "terrain", "clear"),
            "expeditions": int(getattr(game, "expeditions", 0)),
        },
        "retainers": {
            "retainer_board": [actor_to_dict(r) for r in (getattr(game, "retainer_board", []) or [])],
            "retainer_roster": [actor_to_dict(r) for r in (getattr(game, "retainer_roster", []) or [])],
            "active_retainers": [actor_to_dict(r) for r in (getattr(game, "active_retainers", []) or [])],
            "hired_retainers": [actor_to_dict(r) for r in (getattr(game, "hired_retainers", []) or [])],
            "last_board_day": int(getattr(game, "last_board_day", 0)),
        },
        "dungeon": {
            "dungeon_turn": int(getattr(game, "dungeon_turn", 0)),
            "torch_turns_left": int(getattr(game, "torch_turns_left", 0)),
            "magical_light_turns": int(getattr(game, "magical_light_turns", 0)),
            "current_room_id": int(getattr(game, "current_room_id", 0)),
            "dungeon_rooms": _serialize_dungeon_rooms(getattr(game, "dungeon_rooms", {}) or {}),
            "wandering_chance": int(getattr(game, "wandering_chance", 1)),
            "dungeon_depth": int(getattr(game, "dungeon_depth", 1)),
            "dungeon_level": int(getattr(game, "dungeon_level", 1)),
            "noise_level": int(getattr(game, "noise_level", 0)),
            "dungeon_alarm_by_level": dict(getattr(game, "dungeon_alarm_by_level", {}) or {}),
            "dungeon_instance": (getattr(game, "dungeon_instance", None).to_dict() if hasattr(getattr(game, "dungeon_instance", None), "to_dict") else getattr(game, "dungeon_instance", None)),
            "dungeon_clues": list(getattr(game, "dungeon_clues", []) or []),
            "dungeon_monster_groups": list(getattr(game, "dungeon_monster_groups", []) or []),
            "pending_dungeon_ecology_group_ids": list(getattr(game, "pending_dungeon_ecology_group_ids", []) or []),
            "dungeon_room_aftermath": dict(getattr(game, "dungeon_room_aftermath", {}) or {}),
            "next_dungeon_group_id": int(getattr(game, "next_dungeon_group_id", 1) or 1),
        },
        "wilderness": {
            "party_hex": list(getattr(game, "party_hex", (0, 0))),
            "world_hexes": getattr(game, "world_hexes", {}) or {},
            "travel_mode": str(getattr(game, "wilderness_travel_mode", "normal") or "normal"),
            "forward_anchor": getattr(game, "forward_anchor", None),
            "travel_state": (getattr(game, "travel_state", TravelState()).to_dict() if hasattr(getattr(game, "travel_state", None), "to_dict") else TravelState().to_dict()),
        },
        "journal": {
            "discoveries": list(getattr(game, "discovery_log", []) or []),
            "rumors": list(getattr(game, "rumors", []) or []),
            "dungeon_clues": list(getattr(game, "dungeon_clues", []) or []),
            "district_notes": list(getattr(game, "district_notes", []) or []),
        },
        "events": {
            "next_eid": int(getattr(game, "next_event_eid", len(getattr(game, "event_history", []) or [])) or 0),
            "event_history": [
                e for e in (
                    normalize_player_event(x)
                    for x in (getattr(game, "event_history", []) or [])
                )
                if isinstance(e, dict)
            ],
        },
        "factions": {
            "factions": list(getattr(game, "factions", {}).values()) if isinstance(getattr(game, "factions", {}), dict) else [],
            # Store as list of dicts. game is expected to keep factions as {fid: dict}.
        },
        "clocks": {
            "last_clock_day": int(getattr(game, "last_clock_day", 0)),
            "conflict_clocks": list(getattr(game, "conflict_clocks", {}).values()) if isinstance(getattr(game, "conflict_clocks", {}), dict) else [],
        },
        "contracts": {
            "last_contract_day": int(getattr(game, "last_contract_day", 0)),
            "offers": list(getattr(game, "contract_offers", []) or []),
            "active": list(getattr(game, "active_contracts", []) or []),
        },
        "boons": {
            "safe_passage": getattr(game, "safe_passage", {}) or {},
            "blessed_until_day": int(getattr(game, "blessed_until_day", 0)),
            "guild_favor_until_day": int(getattr(game, "guild_favor_until_day", 0)),
        },
    }


def read_save_metadata(filepath: str) -> Dict[str, Any]:
    """Read only metadata from a save file (fast slot listing)."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    meta = data.get("meta") or {}
    # Back-compat: v1 saves had no meta.
    if not meta:
        meta = {
            "created_utc": None,
            "engine": "SWW Text CRPG",
            "summary": {
                "day": _safe_int((data.get("campaign") or {}).get("campaign_day", 1), 1),
                "watch": _safe_int((data.get("campaign") or {}).get("day_watch", 0), 0),
                "gold": _safe_int((data.get("campaign") or {}).get("gold", 0), 0),
                "pcs": [
                    (m.get("name") or "?")
                    for m in (((data.get("campaign") or {}).get("party") or {}).get("members") or [])
                    if m.get("is_pc")
                ],
                "dungeon_depth": _safe_int((data.get("dungeon") or {}).get("dungeon_depth", 1), 1),
                "dungeon_level": _safe_int((data.get("dungeon") or {}).get("dungeon_level", 1), 1),
                "hex": list(((data.get("wilderness") or {}).get("party_hex") or [0, 0])),
            },
        }
    return meta


def _migrate_1_to_2(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add metadata header."""
    if "meta" not in data or not data.get("meta"):
        # fabricate minimal meta from existing data
        data["meta"] = read_save_metadata_from_dict(data)
    data["version"] = 2
    data["save_version"] = 2
    return data


def _migrate_2_to_3(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add wilderness state + day_watch."""
    camp = data.get("campaign") or {}
    if "day_watch" not in camp:
        camp["day_watch"] = 0
    data["campaign"] = camp

    if "wilderness" not in data or not data.get("wilderness"):
        data["wilderness"] = {"party_hex": [0, 0], "world_hexes": {}}

    # Refresh meta summary to include new fields
    data["meta"] = read_save_metadata_from_dict(data)
    data["version"] = 3
    data["save_version"] = 3
    return data


def _migrate_3_to_4(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add journal state (discoveries/rumors)."""
    if "journal" not in data or not data.get("journal"):
        data["journal"] = {"discoveries": [], "rumors": []}
    else:
        j = data.get("journal") or {}
        if "discoveries" not in j:
            j["discoveries"] = []
        if "rumors" not in j:
            j["rumors"] = []
        data["journal"] = j

    # Refresh meta summary (still same fields, but keeps consistency)
    data["meta"] = read_save_metadata_from_dict(data)
    data["version"] = 4
    data["save_version"] = 4
    return data


def _migrate_4_to_5(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add factions state."""
    if "factions" not in data or not data.get("factions"):
        data["factions"] = {"factions": []}
    else:
        f = data.get("factions") or {}
        if "factions" not in f:
            f["factions"] = []
        data["factions"] = f

    data["meta"] = read_save_metadata_from_dict(data)
    data["version"] = 5
    data["save_version"] = 5
    return data


def _migrate_5_to_6(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add conflict clocks and faction contracts state."""
    if "clocks" not in data or not data.get("clocks"):
        data["clocks"] = {"last_clock_day": 0, "conflict_clocks": []}
    else:
        c = data.get("clocks") or {}
        if "last_clock_day" not in c:
            c["last_clock_day"] = 0
        if "conflict_clocks" not in c:
            c["conflict_clocks"] = []
        data["clocks"] = c

    if "contracts" not in data or not data.get("contracts"):
        data["contracts"] = {"last_contract_day": 0, "offers": [], "active": []}
    else:
        k = data.get("contracts") or {}
        if "last_contract_day" not in k:
            k["last_contract_day"] = 0
        if "offers" not in k:
            k["offers"] = []
        if "active" not in k:
            k["active"] = []
        data["contracts"] = k

    data["meta"] = read_save_metadata_from_dict(data)
    data["version"] = 6
    data["save_version"] = 6
    return data


def read_save_metadata_from_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Internal helper: derive metadata from already-parsed save dict."""
    camp = data.get("campaign") or {}
    party = (camp.get("party") or {}).get("members") or []
    pcs = [(m.get("name") or "?") for m in party if m.get("is_pc")]
    return {
        "created_utc": None,
        "engine": "SWW Text CRPG",
        "summary": {
            "day": _safe_int(camp.get("campaign_day", 1), 1),
            "watch": _safe_int(camp.get("day_watch", 0), 0),
            "gold": _safe_int(camp.get("gold", 0), 0),
            "pcs": pcs,
            "dungeon_depth": _safe_int((data.get("dungeon") or {}).get("dungeon_depth", 1), 1),
            "dungeon_level": _safe_int((data.get("dungeon") or {}).get("dungeon_level", 1), 1),
            "hex": list(((data.get("wilderness") or {}).get("party_hex") or [0, 0])),
        },
    }



def _migrate_6_to_7(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add faction boons/services state and allow extended contract fields."""
    if "boons" not in data or not data.get("boons"):
        data["boons"] = {"safe_passage": {}, "blessed_until_day": 0, "guild_favor_until_day": 0}
    else:
        b = data.get("boons") or {}
        b.setdefault("safe_passage", {})
        b.setdefault("blessed_until_day", 0)
        b.setdefault("guild_favor_until_day", 0)
        data["boons"] = b
    # Keep version in sync
    data["save_version"] = 7
    data["version"] = 7
    return data

def _migrate_7_to_8(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add Actor.effect_instances and migrate legacy poison status blobs (P2.1)."""
    camp = data.get("campaign", {}) or {}
    party = (camp.get("party") or {}).get("members") or []
    if isinstance(party, list):
        for m in party:
            if not isinstance(m, dict):
                continue
            m.setdefault("effect_instances", {})
            st = m.get("status")
            if isinstance(st, dict) and "poison" in st:
                p = st.get("poison")
                if isinstance(p, dict) and isinstance(p.get("effects"), dict):
                    ei = m.get("effect_instances")
                    if not isinstance(ei, dict):
                        ei = {}
                        m["effect_instances"] = ei
                    for key, eff in list((p.get("effects") or {}).items()):
                        if not isinstance(eff, dict):
                            continue
                        pid = str(eff.get("id") or key)
                        mode = str(eff.get("mode") or "dot")
                        src = str(eff.get("source") or "poison")
                        instance_id = f"poison:{pid}:{key}"
                        if instance_id in ei:
                            continue
                        ei[instance_id] = {
                            "instance_id": instance_id,
                            "kind": "poison",
                            "source": {"name": src},
                            "created_round": 1,
                            "duration": {"type": "rounds", "remaining": int(eff.get("rounds", 0) or 0)} if str(mode).lower()=="dot" else {"type": "none"},
                            "tags": ["harmful", "poison"],
                            "params": {
                                "id": pid,
                                "mode": str(mode).lower(),
                                "dot_damage": str(eff.get("dot_damage") or "1d6"),
                                "save_tags": ["poison"],
                                "save_each_tick": bool(eff.get("save_each_tick", False)),
                            },
                            "state": {"resolved": bool(eff.get("resolved", False))},
                        }
                # Remove legacy poison status blob
                try:
                    st.pop("poison", None)
                    m["status"] = st
                except Exception:
                    pass
    # Also migrate persisted dungeon foes if present
    dung = data.get("dungeon", {}) or {}
    rooms = dung.get("dungeon_rooms", {}) or {}
    if isinstance(rooms, dict):
        for _, room in rooms.items():
            if not isinstance(room, dict):
                continue
            foes = room.get("foes")
            if isinstance(foes, list):
                for f in foes:
                    if isinstance(f, dict):
                        f.setdefault("effect_instances", {})

    data["save_version"] = 8
    data["version"] = 8
    return data


def _migrate_8_to_9(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add replay header + content hashes (P3.6)."""
    # Replay block is optional; if absent we create an empty one.
    if "replay" not in data or not data.get("replay"):
        data["replay"] = {
            "dice_seed": None,
            "wilderness_seed": None,
            "commands": [],
            "rng_events": [],
            "ai_events": [],
        }
    else:
        r = data.get("replay") or {}
        r.setdefault("dice_seed", None)
        r.setdefault("wilderness_seed", None)
        r.setdefault("rng_states", {})
        r.setdefault("commands", [])
        r.setdefault("rng_events", [])
        r.setdefault("ai_events", [])
        data["replay"] = r

    # Content hashes are used for compatibility warnings/locks.
    if "content" not in data or not data.get("content"):
        data["content"] = {"hashes": {}, "engine_version": None}
    else:
        c = data.get("content") or {}
        c.setdefault("hashes", {})
        c.setdefault("engine_version", None)
        data["content"] = c

    data["save_version"] = 9
    data["version"] = 9
    return data


def _migrate_9_to_10(data: Dict[str, Any]) -> Dict[str, Any]:
    """P6.1.0.1: introduce dungeon_instance container under data['dungeon']."""
    dung = data.get("dungeon") or {}
    if not isinstance(dung, dict):
        dung = {}
    # Ensure key exists; leave None if not present.
    if "dungeon_instance" not in dung:
        dung["dungeon_instance"] = None
    data["dungeon"] = dung
    data["save_version"] = 10
    data["version"] = 10
    return data



def _migrate_10_to_11(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add canonical persisted player-facing event history container."""
    ev = data.get("events") or {}
    if not isinstance(ev, dict):
        ev = {}
    if not isinstance(ev.get("event_history", []), list):
        ev["event_history"] = []
    ev["next_eid"] = int(ev.get("next_eid", len(ev.get("event_history", []) or [])) or 0)
    data["events"] = ev
    data["save_version"] = 11
    data["version"] = 11
    return data


def _migrate_11_to_12(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add per-character inventory/equipment containers with safe defaults."""
    camp = data.get("campaign") or {}
    party = (camp.get("party") or {}).get("members") or []

    def _seed_actor(m: dict) -> None:
        if not isinstance(m, dict):
            return
        m.setdefault("inventory", {"items": [], "coins_gp": 0, "coins_sp": 0, "coins_cp": 0, "torches": 0, "rations": 0, "ammo": {}})
        if not isinstance(m.get("inventory"), dict):
            m["inventory"] = {"items": [], "coins_gp": 0, "coins_sp": 0, "coins_cp": 0, "torches": 0, "rations": 0, "ammo": {}}
        m.setdefault("equipment", {
            "armor": m.get("armor"),
            "shield": m.get("shield_name") if m.get("shield") else None,
            "main_hand": m.get("weapon"),
            "off_hand": None,
            "missile_weapon": None,
            "ammo_kind": None,
            "worn_misc": [],
        })
        if not isinstance(m.get("equipment"), dict):
            m["equipment"] = {
                "armor": m.get("armor"),
                "shield": m.get("shield_name") if m.get("shield") else None,
                "main_hand": m.get("weapon"),
                "off_hand": None,
                "missile_weapon": None,
                "ammo_kind": None,
                "worn_misc": [],
            }

    if isinstance(party, list):
        for m in party:
            _seed_actor(m)

    ret = data.get("retainers") or {}
    if isinstance(ret, dict):
        for key in ("retainer_board", "retainer_roster", "active_retainers", "hired_retainers"):
            arr = ret.get(key) or []
            if isinstance(arr, list):
                for m in arr:
                    _seed_actor(m)

    dung = data.get("dungeon") or {}
    rooms = dung.get("dungeon_rooms") if isinstance(dung, dict) else None
    if isinstance(rooms, dict):
        for room in rooms.values():
            if not isinstance(room, dict):
                continue
            foes = room.get("foes")
            if isinstance(foes, list):
                for f in foes:
                    _seed_actor(f)

    data["save_version"] = 12
    data["version"] = 12
    return data



def _migrate_12_to_13(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize new inventory/equipment payload keys to the current schema."""
    camp = data.get("campaign") or {}
    party = (camp.get("party") or {}).get("members") or []

    def _normalize_actor(m: dict) -> None:
        if not isinstance(m, dict):
            return
        inv = m.get("inventory") if isinstance(m.get("inventory"), dict) else {}
        inv.setdefault("items", [])
        inv["coins_gp"] = int(inv.get("coins_gp", 0) or 0)
        inv["coins_sp"] = int(inv.get("coins_sp", 0) or 0)
        inv["coins_cp"] = int(inv.get("coins_cp", 0) or 0)
        inv["torches"] = int(inv.get("torches", 0) or 0)
        inv["rations"] = int(inv.get("rations", 0) or 0)
        inv["ammo"] = dict(inv.get("ammo") or {})
        norm_items = []
        for it in (inv.get("items") or []):
            if not isinstance(it, dict):
                continue
            norm_items.append({
                "instance_id": str(it.get("instance_id") or ""),
                "template_id": str(it.get("template_id") or it.get("item_id") or ""),
                "name": str(it.get("name") or ""),
                "category": str(it.get("category") or it.get("kind") or "misc"),
                "quantity": int(it.get("quantity", 1) or 1),
                "identified": bool(it.get("identified", True)),
                "equipped": bool(it.get("equipped", False)),
                "magic_bonus": int(it.get("magic_bonus", 0) or 0),
                "metadata": dict(it.get("metadata") or it.get("data") or {}),
            })
        inv["items"] = norm_items
        m["inventory"] = inv

        eq = m.get("equipment") if isinstance(m.get("equipment"), dict) else {}
        if "ammo_kind" not in eq and "ammo" in eq:
            eq["ammo_kind"] = eq.get("ammo")
        eq.setdefault("armor", m.get("armor"))
        eq.setdefault("shield", m.get("shield_name") if m.get("shield") else None)
        eq.setdefault("main_hand", m.get("weapon"))
        eq.setdefault("off_hand", None)
        eq.setdefault("missile_weapon", None)
        eq.setdefault("ammo_kind", None)
        eq["worn_misc"] = list(eq.get("worn_misc") or [])
        m["equipment"] = eq

    if isinstance(party, list):
        for m in party:
            _normalize_actor(m)

    ret = data.get("retainers") or {}
    if isinstance(ret, dict):
        for key in ("retainer_board", "retainer_roster", "active_retainers", "hired_retainers"):
            for m in (ret.get(key) or []):
                _normalize_actor(m)

    dung = data.get("dungeon") or {}
    rooms = dung.get("dungeon_rooms") if isinstance(dung, dict) else None
    if isinstance(rooms, dict):
        for room in rooms.values():
            if not isinstance(room, dict):
                continue
            for m in (room.get("foes") or []):
                _normalize_actor(m)

    data["save_version"] = 13
    data["version"] = 13
    return data


def _migrate_13_to_14(data: Dict[str, Any]) -> Dict[str, Any]:
    """Seed identification knowledge fields for item instances."""

    camp = data.get("campaign") or {}
    party = (camp.get("party") or {}).get("members") or []

    def _normalize_actor(m: dict) -> None:
        if not isinstance(m, dict):
            return
        inv = m.get("inventory") if isinstance(m.get("inventory"), dict) else {}
        items = inv.get("items") if isinstance(inv.get("items"), list) else []
        norm_items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out = dict(it)
            out["identified"] = bool(out.get("identified", True))
            out["cursed_known"] = bool(out.get("cursed_known", False))
            out["custom_label"] = None if out.get("custom_label") is None else str(out.get("custom_label") or "")
            norm_items.append(out)
        inv["items"] = norm_items
        m["inventory"] = inv

    if isinstance(party, list):
        for m in party:
            _normalize_actor(m)

    ret = data.get("retainers") or {}
    if isinstance(ret, dict):
        for key in ("retainer_board", "retainer_roster", "active_retainers", "hired_retainers"):
            for m in (ret.get(key) or []):
                _normalize_actor(m)

    dung = data.get("dungeon") or {}
    rooms = dung.get("dungeon_rooms") if isinstance(dung, dict) else None
    if isinstance(rooms, dict):
        for room in rooms.values():
            if not isinstance(room, dict):
                continue
            for m in (room.get("foes") or []):
                _normalize_actor(m)

    data["save_version"] = 14
    data["version"] = 14
    return data

MIGRATIONS = {
    1: _migrate_1_to_2,
    2: _migrate_2_to_3,
    3: _migrate_3_to_4,
    4: _migrate_4_to_5,
    5: _migrate_5_to_6,
    6: _migrate_6_to_7,
    7: _migrate_7_to_8,
    8: _migrate_8_to_9,
    9: _migrate_9_to_10,
    10: _migrate_10_to_11,
    11: _migrate_11_to_12,
    12: _migrate_12_to_13,
    13: _migrate_13_to_14,
}


def migrate_save_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Upgrade older save dicts to current SAVE_VERSION."""
    # Accept either key.
    v = _safe_int(data.get("save_version", data.get("version", 0)), 0)
    if v == 0:
        # Oldest possible saves might have no version at all.
        # Treat them as v1 (the earliest version used in this project).
        v = 1
        data["save_version"] = 1
        data["version"] = 1
    while v < SAVE_VERSION:
        fn = MIGRATIONS.get(v)
        if fn is None:
            raise ValueError(f"No migration path from version {v} to {v+1}.")
        data = fn(data)
        v = _safe_int(data.get("save_version", data.get("version", v + 1)), v + 1)
    return data


def apply_game_dict(game: Any, data: Dict[str, Any]) -> None:
    """Apply loaded state onto an *existing* Game instance."""
    strict = _is_strict_content()

    # Guard against malformed payloads with clearer messages (and safe defaults when not strict).
    data = validate_and_guard_save_data(data, strict=strict)

    try:
        data = migrate_save_data(data)
    except Exception as e:
        v = _safe_int(data.get("save_version", data.get("version", 0)), 0)
        raise SaveSchemaError(
            f"Could not migrate save from version {v} to {SAVE_VERSION}: {e}"
        ) from e

    version = int(data.get("save_version", data.get("version", 0)))
    if version != SAVE_VERSION:
        if version > SAVE_VERSION:
            raise SaveSchemaError(
                f"Save was created with a newer engine (save_version={version}). "
                f"This build supports saves up to version {SAVE_VERSION}. Please update the game."
            )
        raise SaveSchemaError(
            f"Unsupported/unknown save version {version} (engine expects {SAVE_VERSION})."
        )

    # ---- P3.6: content hash / engine version compatibility checks ----
    try:
        strict = str(os.environ.get("STRICT_CONTENT", "")).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        strict = False
    try:
        saved_content = data.get("content") or {}
        saved_hashes = dict(saved_content.get("hashes") or {})
        current_hashes = dict(getattr(getattr(game, "content", None), "content_hashes", {}) or {})
        mismatches = []
        for k, v in saved_hashes.items():
            if k in current_hashes and str(current_hashes.get(k)) != str(v):
                mismatches.append(k)
        # In strict mode, refuse to load if hashes mismatch.
        if mismatches and strict:
            raise ValueError(
                "Save content hashes do not match current data files (STRICT_CONTENT). Mismatched: "
                + ", ".join(sorted(mismatches))
            )
        # Otherwise: stash warning flag.
        if mismatches:
            setattr(game, "content_hash_mismatch", sorted(mismatches))
    except Exception as e:
        if strict:
            raise
        setattr(game, "content_hash_mismatch", ["unknown"])

    # ---- P3.6: restore replay seeds and logs (deterministic replay) ----
    try:
        rep = data.get("replay") or {}
        dice_seed = rep.get("dice_seed")
        wild_seed = rep.get("wilderness_seed")
        if dice_seed is not None:
            setattr(game, "dice_seed", int(dice_seed))
        if wild_seed is not None:
            setattr(game, "wilderness_seed", int(wild_seed))

        # Rebuild RNGs and dice using restored seeds.
        try:
            from .rng import LoggedRandom
            from .dice import Dice

            game.dice_rng = LoggedRandom(int(getattr(game, "dice_seed", 0) or 0), channel="dice", log_fn=getattr(game, "_log_rng", None))
            game.levelup_rng = LoggedRandom(int(getattr(game, "dice_seed", 0) or 0) + 1, channel="levelup", log_fn=getattr(game, "_log_rng", None))
            game.temple_rng = LoggedRandom(int(getattr(game, "dice_seed", 0) or 0) + 2, channel="temple", log_fn=getattr(game, "_log_rng", None))
            game.wilderness_rng = LoggedRandom(int(getattr(game, "wilderness_seed", 0) or 0), channel="wilderness", log_fn=getattr(game, "_log_rng", None))
            game.wilderness_encounter_seed = int(getattr(game, "wilderness_seed", 0) or 0) + 17
            game.wilderness_encounter_rng = LoggedRandom(int(getattr(game, "wilderness_encounter_seed", 0) or 0), channel="wilderness_encounter", log_fn=getattr(game, "_log_rng", None))
            _restore_rng_states(game, rep.get("rng_states") or {})
            # IMPORTANT: bind Dice to LoggedRandom (not the raw Random) so
            # RNG events remain loggable and STRICT_REPLAY can verify streams.
            game.dice = Dice(rng=game.dice_rng)
            game.wilderness_dice = Dice(rng=getattr(game, "wilderness_encounter_rng", game.wilderness_rng))
            try:
                game.levelup_dice = Dice(rng=game.levelup_rng)
            except Exception:
                pass
            try:
                game.temple_dice = Dice(rng=game.temple_rng)
            except Exception:
                pass

            try:
                from .encounters import EncounterGenerator
                content = getattr(game, "content", None)
                monsters = getattr(content, "monsters", None)
                spells_data = getattr(content, "spells_data", None)
                game.wilderness_encounters = EncounterGenerator(
                    game.wilderness_dice,
                    monsters,
                    spells_data,
                    monster_specials=getattr(content, "monster_specials", None),
                    monster_damage_profiles=getattr(content, "monster_damage_profiles", None),
                )
            except Exception:
                pass
        except Exception:
            pass

        # Restore replay log payloads.
        try:
            from .replay import ReplayLog

            rl = ReplayLog(
                dice_seed=int(getattr(game, "dice_seed", 0) or 0),
                wilderness_seed=int(getattr(game, "wilderness_seed", 0) or 0),
            )
            rl.commands = list(rep.get("commands") or [])
            rl.rng_events = list(rep.get("rng_events") or [])
            rl.ai_events = list(rep.get("ai_events") or [])
            game.replay = rl
        except Exception:
            pass
    except Exception:
        pass

    camp = data.get("campaign", {})
    setattr(game, "campaign_day", int(camp.get("campaign_day", 1)))
    setattr(game, "day_watch", int(camp.get("day_watch", 0)))
    setattr(game, "gold", int(camp.get("gold", 0)))
    setattr(game, "party", dict_to_party(camp.get("party", {}) or {}))

    # P3.1.14: Normalize stored spell identifiers on load.
    # Older saves may store spell names; newer saves prefer spell_id.
    # We canonicalize both spells_known and spells_prepared to spell_id in-memory
    # while preserving unknown entries verbatim.
    def _canonicalize_actor_spells(a: Any) -> None:
        try:
            content = getattr(game, "content", None)
            if content is None:
                return
            sk = list(getattr(a, "spells_known", []) or [])
            if sk:
                setattr(a, "spells_known", [content.canonical_spell_id(str(x)) for x in sk])
            sp = list(getattr(a, "spells_prepared", []) or [])
            if sp:
                setattr(a, "spells_prepared", [content.canonical_spell_id(str(x)) for x in sp])
        except Exception:
            return

    # P3.1.15: Normalize stored monster identifiers on load.
    # Older saves store only Actor.name for monsters; newer saves prefer monster_id.
    def _canonicalize_actor_monster_id(a: Any) -> None:
        try:
            if bool(getattr(a, 'is_pc', False)) or bool(getattr(a, 'is_retainer', False)):
                return
            content = getattr(game, 'content', None)
            if content is None:
                return
            mid = getattr(a, 'monster_id', None)
            if mid and content.get_monster(str(mid)):
                return
            nm = str(getattr(a, 'name', '') or '').strip()
            if not nm:
                return
            canon = content.canonical_monster_id(nm)
            if canon and canon != nm:
                setattr(a, 'monster_id', canon)
        except Exception:
            return


    try:
        for m in (getattr(getattr(game, "party", None), "members", []) or []):
            _canonicalize_actor_spells(m)
            _canonicalize_actor_monster_id(m)
    except Exception:
        pass
    setattr(game, "party_items", list(camp.get("party_items", []) or []))
    setattr(game, "torches", int(camp.get("torches", 0)))
    setattr(game, "rations", int(camp.get("rations", 0)))
    setattr(game, "arrows", int(camp.get("arrows", 0)))
    setattr(game, "bolts", int(camp.get("bolts", 0)))
    setattr(game, "stones", int(camp.get("stones", 0)))
    setattr(game, "hand_axes", int(camp.get("hand_axes", 0)))
    setattr(game, "throwing_daggers", int(camp.get("throwing_daggers", 0)))
    setattr(game, "darts", int(camp.get("darts", 0)))
    setattr(game, "javelins", int(camp.get("javelins", 0)))
    setattr(game, "throwing_spears", int(camp.get("throwing_spears", 0)))
    setattr(game, "terrain", camp.get("terrain", "clear"))
    setattr(game, "expeditions", int(camp.get("expeditions", 0)))

    ret = data.get("retainers", {})
    setattr(game, "retainer_board", [dict_to_actor(r) for r in (ret.get("retainer_board") or [])])
    setattr(game, "retainer_roster", [dict_to_actor(r) for r in (ret.get("retainer_roster") or [])])
    setattr(game, "active_retainers", [dict_to_actor(r) for r in (ret.get("active_retainers") or [])])
    setattr(game, "hired_retainers", [dict_to_actor(r) for r in (ret.get("hired_retainers") or [])])
    setattr(game, "last_board_day", int(ret.get("last_board_day", 0)))
    # ---- P6.0.0.6: Retainer list interning / no-dup hardening ----
    # Retainers appear in multiple lists (roster/hired/active). At runtime these
    # lists typically share the same Actor object references. A naive load
    # recreates separate Actor instances per list, which can desync flags like
    # on_expedition and cause subtle duplication behavior. We "intern" by name
    # to restore shared references and enforce subset invariants.
    try:
        roster = list(getattr(game, "retainer_roster", []) or [])
        hired = list(getattr(game, "hired_retainers", []) or [])
        active = list(getattr(game, "active_retainers", []) or [])

        pool: dict[str, Any] = {}
        new_roster: list[Any] = []
        def _add_to_roster(a):
            nm = str(getattr(a, "name", ""))
            if not nm:
                return
            if nm in pool:
                return
            pool[nm] = a
            new_roster.append(a)

        for a in roster:
            _add_to_roster(a)

        # Hired is a subset of roster.
        new_hired: list[Any] = []
        for a in hired:
            nm = str(getattr(a, "name", ""))
            if not nm:
                continue
            if nm not in pool:
                _add_to_roster(a)
            new_hired.append(pool[nm])
        # Active is a subset of hired.
        new_active: list[Any] = []
        for a in active:
            nm = str(getattr(a, "name", ""))
            if not nm:
                continue
            if nm not in pool:
                _add_to_roster(a)
            if nm not in {str(getattr(x, "name", "")) for x in new_hired}:
                new_hired.append(pool[nm])
            pool[nm].on_expedition = True
            new_active.append(pool[nm])

        # De-dup while preserving order
        def _uniq_by_name(lst):
            seen=set()
            out=[]
            for a in lst:
                nm=str(getattr(a,"name",""))
                if nm in seen:
                    continue
                seen.add(nm)
                out.append(a)
            return out

        setattr(game, "retainer_roster", _uniq_by_name(new_roster))
        setattr(game, "hired_retainers", _uniq_by_name(new_hired))
        setattr(game, "active_retainers", _uniq_by_name(new_active))
    except Exception:
        pass

    # Canonicalize spell identifiers for retainers as well.
    try:
        for grp in ("retainer_board", "retainer_roster", "active_retainers", "hired_retainers"):
            for a in (getattr(game, grp, []) or []):
                _canonicalize_actor_spells(a)
                _canonicalize_actor_monster_id(a)
    except Exception:
        pass

    dung = data.get("dungeon", {})
    setattr(game, "dungeon_turn", int(dung.get("dungeon_turn", 0)))
    setattr(game, "torch_turns_left", int(dung.get("torch_turns_left", 0)))
    setattr(game, "magical_light_turns", int(dung.get("magical_light_turns", 0)))
    setattr(game, "current_room_id", int(dung.get("current_room_id", 0)))
    rooms = dung.get("dungeon_rooms", {}) or {}
    # Normalize JSON object keys back to ints when possible.
    # JSON forces dict keys to strings; if we keep them as strings, room lookups
    # like `_ensure_room(2)` will treat an existing room as missing and
    # accidentally regenerate it.
    if isinstance(rooms, dict):
        norm_rooms: dict = {}
        for k, v in rooms.items():
            nk = int(k) if str(k).isdigit() else k
            norm_rooms[nk] = v
        rooms = norm_rooms
    # Rehydrate any persisted foes back into Actor objects.
    if isinstance(rooms, dict):
        for _, room in rooms.items():
            if not isinstance(room, dict):
                continue
            foes = room.get("foes")
            if isinstance(foes, list) and (not foes or isinstance(foes[0], dict)):
                try:
                    room["foes"] = [dict_to_actor(fd) if isinstance(fd, dict) else fd for fd in foes]
                except Exception:
                    pass

            # Canonicalize spells for any persisted foes that happen to have spell lists.
            try:
                for f in (room.get("foes") or []):
                    _canonicalize_actor_spells(f)
                    _canonicalize_actor_monster_id(f)
            except Exception:
                pass
    setattr(game, "dungeon_rooms", rooms)
    setattr(game, "wandering_chance", int(dung.get("wandering_chance", 1)))
    setattr(game, "dungeon_depth", int(dung.get("dungeon_depth", 1)))
    setattr(game, "dungeon_level", int(dung.get("dungeon_level", 1)))
    setattr(game, "noise_level", int(dung.get("noise_level", 0)))
    alarm_by_level = dung.get("dungeon_alarm_by_level", {}) or {}
    if isinstance(alarm_by_level, dict):
        try:
            alarm_by_level = {int(k): int(v) for k, v in alarm_by_level.items()}
        except Exception:
            alarm_by_level = {1: int(alarm_by_level.get(1, 0) or 0)}
    else:
        alarm_by_level = {1: 0}
    setattr(game, "dungeon_alarm_by_level", alarm_by_level)
    setattr(game, "dungeon_clues", list(dung.get("dungeon_clues", []) or []))
    setattr(game, "dungeon_monster_groups", list(dung.get("dungeon_monster_groups", []) or []))
    setattr(game, "pending_dungeon_ecology_group_ids", [int(x) for x in (dung.get("pending_dungeon_ecology_group_ids", []) or [])])
    setattr(game, "dungeon_room_aftermath", dict(dung.get("dungeon_room_aftermath", {}) or {}))
    setattr(game, "next_dungeon_group_id", int(dung.get("next_dungeon_group_id", 1) or 1))

    # P6.1: dungeon instance (blueprint+stocking+delta) container (not yet used by crawler)
    try:
        from .dungeon_instance import coerce_dungeon_instance
        setattr(game, "dungeon_instance", coerce_dungeon_instance(dung.get("dungeon_instance")) or dung.get("dungeon_instance"))
    except Exception:
        setattr(game, "dungeon_instance", dung.get("dungeon_instance"))

    wild = data.get("wilderness", {})
    ph = wild.get("party_hex") or [0, 0]
    try:
        setattr(game, "party_hex", (int(ph[0]), int(ph[1])))
    except Exception:
        setattr(game, "party_hex", (0, 0))
    setattr(game, "world_hexes", wild.get("world_hexes", {}) or {})
    fa = wild.get("forward_anchor", None)
    setattr(game, "forward_anchor", fa if isinstance(fa, dict) else None)
    setattr(game, "travel_state", TravelState.from_dict(wild.get("travel_state", {})))

    j = data.get("journal", {}) or {}
    ev = data.get("events", {}) or {}
    raw_event_history = ev.get("event_history", []) or []

    event_history: list[dict[str, Any]] = []
    needs_reassign = False
    seen_eids: set[int] = set()
    last_eid: int | None = None
    for raw in raw_event_history:
        row = normalize_player_event(raw)
        if not isinstance(row, dict):
            continue
        eid_raw = row.get("eid", None)
        try:
            eid_val = int(eid_raw)
        except Exception:
            eid_val = -1
        if eid_val < 0:
            needs_reassign = True
        row["eid"] = int(eid_val)
        if int(row["eid"]) in seen_eids:
            needs_reassign = True
        if last_eid is not None and int(row["eid"]) <= int(last_eid):
            needs_reassign = True
        seen_eids.add(int(row["eid"]))
        last_eid = int(row["eid"])
        event_history.append(row)

    if needs_reassign:
        for idx, row in enumerate(event_history):
            row["eid"] = idx
        next_eid = len(event_history)
    elif event_history:
        max_eid = max(int((x or {}).get("eid", 0) or 0) for x in event_history)
        next_eid = _safe_int(ev.get("next_eid", max_eid + 1), max_eid + 1)
        if next_eid <= max_eid:
            next_eid = max_eid + 1
    else:
        next_eid = 0

    if not event_history:
        for d in (j.get("discoveries", []) or []):
            if isinstance(d, dict):
                append_player_event(
                    event_history,
                    eid=next_eid,
                    event_type="discovery.recorded",
                    category="discovery",
                    day=int(d.get("day", 1) or 1),
                    watch=int(d.get("watch", 0) or 0),
                    title=f"Discovery recorded: {str(d.get('name') or 'Unknown')}",
                    payload=dict(d),
                    refs={"hex": [int(d.get("q", 0) or 0), int(d.get("r", 0) or 0)]},
                )
                next_eid += 1
        for r in (j.get("rumors", []) or []):
            if isinstance(r, dict):
                append_player_event(
                    event_history,
                    eid=next_eid,
                    event_type="rumor.learned",
                    category="rumor",
                    day=int(r.get("day", 1) or 1),
                    watch=int(r.get("watch", 0) or 0),
                    title="Rumor learned",
                    payload=dict(r),
                    refs={"poi_id": str(r.get("poi_id") or ""), "hex": [int(r.get("q", 0) or 0), int(r.get("r", 0) or 0)]},
                )
                next_eid += 1
        for c in (j.get("dungeon_clues", []) or []):
            if isinstance(c, dict):
                append_player_event(
                    event_history,
                    eid=next_eid,
                    event_type="clue.found",
                    category="clue",
                    day=int(c.get("day", 1) or 1),
                    watch=int(c.get("watch", 0) or 0),
                    title=f"Clue noted: {str(c.get('text') or '')}",
                    payload=dict(c),
                    refs={"room_id": int(c.get("room_id", 0) or 0), "level": int(c.get("level", 0) or 0)},
                )
                next_eid += 1
        for n in (j.get("district_notes", []) or []):
            if isinstance(n, dict):
                append_player_event(
                    event_history,
                    eid=next_eid,
                    event_type="district.noted",
                    category="district",
                    day=int(n.get("day", 1) or 1),
                    watch=int(n.get("watch", 0) or 0),
                    title=str(n.get("text") or "District note"),
                    payload=dict(n),
                    refs={"cid": str(n.get("cid") or "")},
                )
                next_eid += 1

    setattr(game, "event_history", event_history)
    setattr(game, "next_event_eid", int(next_eid))
    setattr(game, "discovery_log", project_discoveries_from_events(event_history))
    setattr(game, "rumors", project_rumors_from_events(event_history))
    setattr(game, "dungeon_clues", project_clues_from_events(event_history))
    setattr(game, "district_notes", project_district_notes_from_events(event_history))
    try:
        if hasattr(game, "_ensure_minimal_rumor_surface"):
            game._ensure_minimal_rumor_surface()
    except Exception:
        pass

    fac = data.get("factions", {}) or {}
    fac_list = list(fac.get("factions", []) or [])
    # Store factions as a dict keyed by fid for convenience.
    out: dict[str, dict[str, Any]] = {}
    for fd in fac_list:
        if isinstance(fd, dict):
            fid = str(fd.get("fid", fd.get("id", "")) or "")
            if fid:
                out[fid] = fd
    setattr(game, "factions", out)

    clocks = data.get("clocks", {}) or {}
    setattr(game, "last_clock_day", int(clocks.get("last_clock_day", 0)))
    cc_list = list(clocks.get("conflict_clocks", []) or [])
    cc_out: dict[str, dict[str, Any]] = {}
    for cd in cc_list:
        if isinstance(cd, dict):
            cid = str(cd.get("cid", "") or "")
            if cid:
                cc_out[cid] = cd
    setattr(game, "conflict_clocks", cc_out)

    contracts = data.get("contracts", {}) or {}
    setattr(game, "last_contract_day", int(contracts.get("last_contract_day", 0)))
    setattr(game, "contract_offers", list(contracts.get("offers", []) or []))
    setattr(game, "active_contracts", list(contracts.get("active", []) or []))
    # ---- P6.0.0.6: Contract list de-dup by cid ----
    try:
        def _dedup_contracts(lst):
            out=[]
            seen=set()
            for c in (lst or []):
                if not isinstance(c, dict):
                    continue
                cid=str(c.get("cid",""))
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                out.append(c)
            return out
        setattr(game, "contract_offers", _dedup_contracts(getattr(game, "contract_offers", []) or []))
        setattr(game, "active_contracts", _dedup_contracts(getattr(game, "active_contracts", []) or []))
    except Exception:
        pass

    boons = data.get("boons", {}) or {}
    setattr(game, "safe_passage", dict(boons.get("safe_passage", {}) or {}))
    setattr(game, "blessed_until_day", int(boons.get("blessed_until_day", 0)))
    setattr(game, "guild_favor_until_day", int(boons.get("guild_favor_until_day", 0)))

    # ---- P6.0.0.1: post-load state validation (hard fail only in STRICT_CONTENT) ----
    try:
        from .validation import validate_state

        issues = validate_state(game) or []
        errs = [x for x in issues if getattr(x, "severity", "") == "error"]
        warns = [x for x in issues if getattr(x, "severity", "") != "error"]

        # Attach for UI/debug purposes.
        setattr(
            game,
            "validation_warnings",
            [getattr(x, "message", str(x)) for x in (errs + warns)],
        )

        # STRICT_CONTENT acts as our "fail-fast" toggle for load integrity.
        strict = str(os.environ.get("STRICT_CONTENT", "")).strip().lower() in ("1", "true", "yes", "on")
        if strict and errs:
            raise ValueError(
                "Save validation failed (STRICT_CONTENT):\n" + "\n".join(getattr(x, "message", str(x)) for x in errs)
            )
    except Exception:
        # Be conservative: only fail hard if STRICT_CONTENT is set.
        strict = str(os.environ.get("STRICT_CONTENT", "")).strip().lower() in ("1", "true", "yes", "on")
        if strict:
            raise


def save_game(game: Any, filepath: str) -> str:
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    data = game_to_dict(game)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
    return filepath


def load_game(game: Any, filepath: str) -> None:
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    apply_game_dict(game, data)