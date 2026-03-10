from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .blueprint import DungeonBlueprint, stable_hash_dict
from .cl_compile import compile_cl_lists
from .rng_streams import RNGStream


def _repo_root() -> Path:
    # p44/sww/dungeon_gen -> p44
    return Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _pick_table_for_depth(stock_tables: dict[str, Any], depth: int) -> dict[str, Any]:
    depth = int(depth)
    for band in stock_tables.get("stocking_by_depth", []):
        if int(band.get("depth_min", 0)) <= depth <= int(band.get("depth_max", 0)):
            return band
    bands = stock_tables.get("stocking_by_depth", [])
    return bands[0] if bands else {}


def _roll_table(rng: RNGStream, table: dict[str, Any]) -> tuple[int, Any]:
    dice = str(table.get("dice", "1d6"))
    r = int(rng.roll(dice))
    for ent in table.get("entries", []):
        lo, hi = ent.get("range", [None, None])
        if lo is None or hi is None:
            continue
        if int(lo) <= r <= int(hi):
            return r, ent.get("result")
    entries = table.get("entries", [])
    return r, entries[-1].get("result") if entries else None


def _roll_expr(rng: RNGStream, expr: str) -> int:
    """Roll minimal expressions like '1d6', '1d6*10', '2d4+3', and plain integers."""
    s = str(expr).replace(" ", "")
    if "+" in s:
        return sum(_roll_expr(rng, p) for p in s.split("+"))
    if "*" in s:
        left, right = s.split("*", 1)
        base = _roll_expr(rng, left)
        try:
            mul = int(right)
        except ValueError:
            mul = _roll_expr(rng, right)
        return int(base) * int(mul)
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    return int(rng.roll(s))


def _load_monsters(root: Path) -> list[dict[str, Any]]:
    return _load_json(root / "data" / "monsters.json")


def _encounter_table_for_depth(enc: dict[str, Any], depth: int) -> dict[str, Any]:
    d = max(1, int(depth))
    if d <= 5:
        return (enc.get("tables", {}).get("1_5", {}) or {}).get(str(d), {}) or {}
    return (enc.get("tables", {}).get("6_10", {}) or {}).get(str(min(d, 10)), {}) or {}


def _choose_monster_id_for_numeric_cl(*, rng: RNGStream, monsters: list[dict[str, Any]], cl: str, exclude_ids: set[str]) -> str:
    # numeric CL can be "12+" in deeper tables; normalize by stripping '+'
    try:
        target = int(str(cl).replace("+", ""))
    except Exception:
        target = 1

    pool: list[dict[str, Any]] = []
    for m in monsters:
        # Prefer explicit integer CL when present, but allow "derived" monsters to participate
        # in CL buckets via their declared cl_range (e.g., dragons, hydra variants).
        mcl_raw = m.get("challenge_level", "")
        mcl = None
        if mcl_raw not in (None, "", "—"):
            try:
                mcl = int(str(mcl_raw).replace("+", ""))
            except Exception:
                mcl = None

        if mcl is not None:
            if mcl == target:
                pool.append(m)
            continue

        # Derived / ranged CL support
        rng = m.get("cl_range") or {}
        try:
            rmin = int(rng.get("min"))
            rmax = int(rng.get("max"))
        except Exception:
            continue
        if rmin <= target <= rmax:
            pool.append(m)

    if not pool:
        pool = monsters

    pool = sorted(pool, key=lambda m: str(m.get("monster_id", "")))
    candidates = [m for m in pool if str(m.get("monster_id")) not in exclude_ids] or pool
    idx = int(rng.roll(f"1d{len(candidates)}")) - 1
    idx = max(0, min(idx, len(candidates) - 1))
    return str(candidates[idx].get("monster_id"))


def _norm_name(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _pick_from_cl_list(
    *,
    rng: RNGStream,
    cl_lists: dict[str, Any],
    cl: str,
    monsters: list[dict[str, Any]],
    exclude_ids: set[str],
    strict: bool,
) -> tuple[str, str, int]:
    """Returns (monster_id, source, roll). Source describes which table was used."""

    cl_key = str(cl).replace("+", "")
    tables = cl_lists.get("tables", {}) or {}
    tbl = tables.get(cl_key)
    if tbl:
        die = str(tbl.get("die", "1d20"))
        r = int(rng.roll(die))
        picked: dict[str, Any] | None = None
        for e in tbl.get("entries", []):
            if int(e.get("lo", 0)) <= r <= int(e.get("hi", 0)):
                picked = e
                break
        if picked is None and tbl.get("entries"):
            picked = tbl["entries"][-1]

        # Optional sub-choice (e.g., 'Succubus or Erinyes', elementals by type)
        if picked and "choose" in picked:
            ch = picked["choose"]
            die2 = str(ch.get("die", "1d2"))
            r2 = int(rng.roll(die2))
            opts = list(ch.get("options", []))
            if not opts:
                if strict:
                    raise RuntimeError(f"CL{cl_key} choose entry had no options (STRICT_CONTENT)")
                mid = _choose_monster_id_for_numeric_cl(rng=rng, monsters=monsters, cl=cl_key, exclude_ids=exclude_ids)
                return mid, f"CL{cl_key}.choose_empty_pool", r

            idx = max(1, min(r2, len(opts))) - 1
            opt = opts[idx]

            if isinstance(opt, dict) and opt.get("monster_id"):
                mid = str(opt["monster_id"])
                if mid in exclude_ids:
                    if strict:
                        raise RuntimeError(f"CL{cl_key} choose selected excluded monster_id={mid} (STRICT_CONTENT)")
                    mid = _choose_monster_id_for_numeric_cl(rng=rng, monsters=monsters, cl=cl_key, exclude_ids=exclude_ids)
                    return mid, f"CL{cl_key}.choose_fallback_pool", r
                return mid, f"CL{cl_key}.choose", r

            # Legacy string option: resolve by name/alias
            chosen_name = str(opt)
            target = _norm_name(chosen_name)
            for m in monsters:
                if _norm_name(str(m.get("name", ""))) == target:
                    mid = str(m.get("monster_id"))
                    if mid in exclude_ids:
                        if strict:
                            raise RuntimeError(f"CL{cl_key} choose selected excluded {mid} (STRICT_CONTENT)")
                        mid = _choose_monster_id_for_numeric_cl(rng=rng, monsters=monsters, cl=cl_key, exclude_ids=exclude_ids)
                        return mid, f"CL{cl_key}.choose_fallback_pool", r
                    return mid, f"CL{cl_key}.choose", r
                for a in m.get("aliases", []) or []:
                    if _norm_name(str(a)) == target:
                        mid = str(m.get("monster_id"))
                        if mid in exclude_ids:
                            if strict:
                                raise RuntimeError(f"CL{cl_key} choose selected excluded {mid} (STRICT_CONTENT)")
                            mid = _choose_monster_id_for_numeric_cl(rng=rng, monsters=monsters, cl=cl_key, exclude_ids=exclude_ids)
                            return mid, f"CL{cl_key}.choose_fallback_pool", r
                        return mid, f"CL{cl_key}.choose_alias", r

            if strict:
                raise RuntimeError(f"CL{cl_key} choose name '{chosen_name}' could not be resolved (STRICT_CONTENT)")
            mid = _choose_monster_id_for_numeric_cl(rng=rng, monsters=monsters, cl=cl_key, exclude_ids=exclude_ids)
            return mid, f"CL{cl_key}.choose_unresolved_pool:{chosen_name}", r

        if picked and "monster_id" in picked:
            mid = str(picked["monster_id"])
            if mid in exclude_ids:
                if strict:
                    raise RuntimeError(f"CL{cl_key} selected excluded monster_id={mid} (STRICT_CONTENT)")
                mid = _choose_monster_id_for_numeric_cl(rng=rng, monsters=monsters, cl=cl_key, exclude_ids=exclude_ids)
                return mid, f"CL{cl_key}.fallback_pool", r
            return mid, f"CL{cl_key}.list", r

        if picked and "name" in picked:
            name = str(picked["name"])
            target = _norm_name(name)
            for m in monsters:
                if _norm_name(str(m.get("name", ""))) == target:
                    mid = str(m.get("monster_id"))
                    if mid in exclude_ids:
                        if strict:
                            raise RuntimeError(f"CL{cl_key} name '{name}' resolved to excluded {mid} (STRICT_CONTENT)")
                        mid = _choose_monster_id_for_numeric_cl(rng=rng, monsters=monsters, cl=cl_key, exclude_ids=exclude_ids)
                        return mid, f"CL{cl_key}.fallback_pool", r
                    return mid, f"CL{cl_key}.name", r
                for a in m.get("aliases", []) or []:
                    if _norm_name(str(a)) == target:
                        mid = str(m.get("monster_id"))
                        if mid in exclude_ids:
                            if strict:
                                raise RuntimeError(f"CL{cl_key} alias '{a}' resolved to excluded {mid} (STRICT_CONTENT)")
                            mid = _choose_monster_id_for_numeric_cl(rng=rng, monsters=monsters, cl=cl_key, exclude_ids=exclude_ids)
                            return mid, f"CL{cl_key}.fallback_pool", r
                        return mid, f"CL{cl_key}.alias", r

            if strict:
                raise RuntimeError(f"CL{cl_key} name '{name}' could not be resolved (STRICT_CONTENT)")

    # No explicit CL list: numeric pool selection
    if strict:
        raise RuntimeError(f"No explicit CL list for CL{cl_key} (STRICT_CONTENT). Run scripts/compile_cl_lists.py")
    mid = _choose_monster_id_for_numeric_cl(rng=rng, monsters=monsters, cl=cl_key, exclude_ids=exclude_ids)
    return mid, f"CL{cl_key}.pool", 0




_THEME_CLUE_TEXT = {
    "goblin_warrens": {
        "boss": "Crude arrows and boasts point toward the chieftain's deeper hall.",
        "treasury": "Scuffed tracks and hidden tally marks hint at a guarded goblin stash.",
    },
    "cult_shrine": {
        "boss": "Soot-black prayers and blood sigils point toward the inner ritual chamber.",
        "treasury": "Half-burned ledgers hint that offerings are stored behind a sanctified ward.",
    },
    "lost_garrison": {
        "boss": "Faded duty rosters mark the command post farther in.",
        "treasury": "Quartermaster signs and locked inventory marks hint at a secured armory cache.",
    },
    "fungal_caverns": {
        "boss": "Dense spore trails and gnawed shells point toward a brood nexus below.",
        "treasury": "Clusters of disturbed fungus hide a path toward a dry chamber with old salvage.",
    },
}


def _themed_clue_special(blueprint: DungeonBlueprint, room: Any) -> dict[str, Any]:
    ctx = dict(blueprint.context or {})
    theme_id = str(ctx.get("theme_id") or "")
    clue_targets = dict(ctx.get("clue_targets") or {})
    target = str(clue_targets.get(getattr(room, "room_id", "")) or "boss")
    notes = _THEME_CLUE_TEXT.get(theme_id, {})
    text = notes.get(target) or (
        "Old signs and half-buried marks point toward the floor's deepest danger."
        if target == "boss"
        else "A hidden pattern in the room points toward valuables kept off the main traffic."
    )
    return {
        "special_id": "SP0000",
        "special_type": "themed_clue",
        "params": {
            "target": target,
            "theme_id": theme_id,
            "text": text,
        },
    }

def _ctx_theme_packs(blueprint: DungeonBlueprint) -> dict[str, Any]:
    ctx = dict(blueprint.context or {})
    packs = ctx.get("special_packs") or {}
    return dict(packs) if isinstance(packs, dict) else {}


def _pick_weightless(rng: RNGStream, options: list[Any]) -> Any:
    if not options:
        return None
    idx = max(1, min(len(options), int(rng.roll(f"1d{len(options)}")))) - 1
    return options[idx]


def _make_trap_from_theme(blueprint: DungeonBlueprint, room: Any, rng: RNGStream) -> dict[str, Any]:
    packs = _ctx_theme_packs(blueprint)
    options = list(packs.get("hazard_traps") or [])
    picked = dict(_pick_weightless(rng, options) or {})
    if not picked:
        return {"trap_id": "TR0000", "trap_type": "simple_pit", "dc": 12, "trigger": "step_on"}
    trap = {
        "trap_id": "TR0000",
        "trap_type": str(picked.get("trap_type") or "simple_pit"),
        "dc": int(picked.get("dc", 12) or 12),
        "trigger": str(picked.get("trigger") or "step_on"),
    }
    dmg = picked.get("damage") or {}
    if isinstance(dmg, dict) and dmg:
        trap["damage_packet"] = {
            "amount": int(dmg.get("amount", 0) or 0),
            "damage_type": str(dmg.get("damage_type") or "bludgeoning"),
            "magical": bool(dmg.get("magical", False)),
            "enchantment_bonus": int(dmg.get("enchantment_bonus", 0) or 0),
            "source_tags": ["theme_hazard", str((blueprint.context or {}).get("theme_id") or "unknown")],
        }
    effect_ref = str(picked.get("effect_ref") or "").strip()
    if effect_ref:
        trap["effect_ref"] = effect_ref
    return trap


def _make_hazard_special(blueprint: DungeonBlueprint, room: Any, rng: RNGStream) -> dict[str, Any]:
    packs = _ctx_theme_packs(blueprint)
    options = list(packs.get("hazard_specials") or [])
    picked = dict(_pick_weightless(rng, options) or {})
    text = str(picked.get("text") or "The room shows clear signs of engineered danger.")
    return {
        "special_id": "SP0000",
        "special_type": str(picked.get("special_type") or "hazard_sign"),
        "params": {
            "theme_id": str((blueprint.context or {}).get("theme_id") or ""),
            "role": "hazard",
            "text": text,
        },
    }


def _make_shrine_special(blueprint: DungeonBlueprint, room: Any, rng: RNGStream) -> dict[str, Any]:
    packs = _ctx_theme_packs(blueprint)
    options = list(packs.get("shrine_specials") or [])
    picked = dict(_pick_weightless(rng, options) or {})
    shrine_name = str(picked.get("shrine") or (_pick_weightless(rng, list(((blueprint.context or {}).get("shrines") or []))) or "forgotten shrine"))
    return {
        "special_id": "SP0000",
        "special_type": str(picked.get("special_type") or "themed_shrine"),
        "params": {
            "role": "shrine",
            "shrine": shrine_name,
            "effect": str(picked.get("effect") or ""),
            "text": str(picked.get("text") or f"A {shrine_name} dominates the chamber."),
            "theme_id": str((blueprint.context or {}).get("theme_id") or ""),
        },
    }


def _make_role_special(blueprint: DungeonBlueprint, room: Any, rng: RNGStream) -> dict[str, Any]:
    role = str(getattr(room, "role", "") or "")
    packs = _ctx_theme_packs(blueprint)
    room_packs = packs.get("room_specials") or {}
    options = list((room_packs.get(role) or []))
    picked = dict(_pick_weightless(rng, options) or {})
    if role == "clue":
        clue = _themed_clue_special(blueprint, room)
        surfaces = list(packs.get("clue_surfaces") or [])
        surface = _pick_weightless(rng, surfaces)
        if surface:
            clue.setdefault("params", {})["surface"] = str(surface)
        return clue
    if role == "shrine":
        return _make_shrine_special(blueprint, room, rng)
    detail = str(picked.get("detail") or f"This {role or 'special'} room bears signs of its purpose.")
    return {
        "special_id": "SP0000",
        "special_type": str(picked.get("special_type") or "room_dressing"),
        "params": {
            "role": role,
            "theme_id": str((blueprint.context or {}).get("theme_id") or ""),
            "detail": detail,
        },
    }


def _index_by_monster_id(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        mid = str(r.get("monster_id", "")).strip()
        if mid:
            out[mid] = r
    return out


@dataclass(frozen=True)
class StockingArtifacts:
    stocking: dict[str, Any]
    roll_log: list[dict[str, Any]]
    content_hash: str
    cl_lists_hash: str


def generate_stocking(*, blueprint: DungeonBlueprint, seed: str, depth: int, rng: RNGStream) -> StockingArtifacts:
    """Generate stocking and embed deterministic encounter resolution metadata.

    P4.9.2.6 adds:
    - Stub guard in STRICT_CONTENT
    - Encounter resolution that always uses compiled CL lists when available
    - Combat readiness checks (logged; optionally strict)
    - cl_lists_hash exposed for replay invariants
    """

    root = _repo_root()

    stock_tables = _load_json(root / "data" / "dungeon_gen" / "dungeon_tables.json")
    treasure_tables = _load_json(root / "data" / "dungeon_gen" / "treasure_tables.json")
    enc_tables = _load_json(root / "data" / "dungeon_gen" / "encounter_generation_tables.json")

    strict = str(os.getenv("STRICT_CONTENT", "")).lower() in ("1", "true", "yes")
    readiness_strict = str(os.getenv("COMBAT_READINESS_STRICT", "")).lower() in ("1", "true", "yes")

    # Prefer compiled monster_id CL lists; compile on-demand if missing.
    compiled_path = root / "data" / "dungeon_gen" / "dungeon_monster_lists_compiled.json"
    if not compiled_path.exists():
        compile_cl_lists(project_root=root, strict=strict)
    if compiled_path.exists():
        cl_lists = _load_json(compiled_path)
        cl_lists_source = str(compiled_path)
    else:
        cl_lists = _load_json(root / "data" / "dungeon_gen" / "dungeon_monster_lists.json")
        cl_lists_source = str(root / "data" / "dungeon_gen" / "dungeon_monster_lists.json")

    cl_lists_hash = stable_hash_dict({"cl_lists": cl_lists})

    # Stub IDs (from compiler output).
    stub_path = root / "data" / "dungeon_gen" / "auto_monster_stubs.json"
    stub_records: list[dict[str, Any]] = _load_json(stub_path) if stub_path.exists() else []
    stub_ids = {str(r.get("monster_id")) for r in stub_records if r.get("monster_id")}

    # P4.9.2.6 stub guard (STRICT_CONTENT): any stubs present means generation should fail.
    if strict and stub_records:
        raise RuntimeError(
            "STRICT_CONTENT=true but auto_monster_stubs.json is non-empty. "
            "Fill in missing monsters or set STRICT_CONTENT=false while iterating."
        )

    content_hash = stable_hash_dict(
        {
            "schema": "P4.9.2.6",
            "dungeon_tables": stock_tables,
            "treasure_tables": treasure_tables,
            "encounter_generation_tables": enc_tables,
            "cl_lists": cl_lists,
        }
    )

    band = _pick_table_for_depth(stock_tables, depth)
    room_table = band.get("room_contents_table", {})

    monsters = _load_monsters(root)
    monsters_by_id = _index_by_monster_id(monsters)

    # Optional profiles (may be incomplete in early data builds)
    ai_profiles = _load_json(root / "data" / "monster_ai_profiles.json")
    dmg_profiles = _load_json(root / "data" / "monster_damage_profiles.json")
    specials = _load_json(root / "data" / "monster_specials.json")

    treasure_table_id = "treasure.l1.coins" if depth <= 1 else "treasure.l1.coins"
    treasure_table = treasure_tables.get("tables", {}).get(treasure_table_id)

    roll_log: list[dict[str, Any]] = []
    room_contents: dict[str, Any] = {}

    def log(step: str, dice: str, result: int, table_id: str, choice: str, ref: str, notes: str = "") -> None:
        roll_log.append(
            {
                "step": step,
                "stream": rng.name,
                "dice": str(dice),
                "result": int(result),
                "table_id": str(table_id),
                "choice": str(choice),
                "ref": str(ref),
                "notes": str(notes),
            }
        )

    def base_alertness() -> str:
        r = int(rng.roll("1d6"))
        if r <= 4:
            return "calm"
        if r == 5:
            return "wary"
        return "alert"

    def roll_encounter_groups() -> tuple[int, list[dict[str, Any]]]:
        tbl = _encounter_table_for_depth(enc_tables, depth)
        r = int(rng.roll("1d10"))
        groups = list(tbl.get(str(r), []) or [])
        return r, groups

    def readiness_check(monster_id: str, room_id: str) -> None:
        # Stub reference (can happen if STRICT_CONTENT is off)
        if monster_id in stub_ids:
            log(
                "content.stub",
                "",
                0,
                "stub.monster",
                monster_id,
                room_id,
                notes="Stub monster_id from compiled CL lists — replace with full statblock.",
            )
            if readiness_strict:
                raise RuntimeError(f"Stub monster_id used in encounter resolution: {monster_id} (COMBAT_READINESS_STRICT)")
            return

        # Base monster entry is required.
        if monster_id not in monsters_by_id:
            log(
                "content.missing_monster",
                "",
                0,
                "content.monsters",
                monster_id,
                room_id,
                notes="monster_id not found in data/monsters.json",
            )
            if strict or readiness_strict:
                raise RuntimeError(f"Encounter resolved to missing monster_id={monster_id}")
            return

        # Optional per-monster profiles.
        if monster_id not in dmg_profiles:
            log(
                "content.missing_damage_profile",
                "",
                0,
                "content.monster_damage_profiles",
                monster_id,
                room_id,
                notes="No damage profile entry (logged; not fatal unless COMBAT_READINESS_STRICT=true)",
            )
            if readiness_strict:
                raise RuntimeError(f"Missing damage profile for monster_id={monster_id}")

        if monster_id not in ai_profiles:
            log(
                "content.missing_ai_profile",
                "",
                0,
                "content.monster_ai_profiles",
                monster_id,
                room_id,
                notes="No AI profile entry (logged; not fatal unless COMBAT_READINESS_STRICT=true)",
            )
            if readiness_strict:
                raise RuntimeError(f"Missing AI profile for monster_id={monster_id}")

        # Specials are only required if the monster references them (via monsters.json "special" field).
        sp = str(monsters_by_id[monster_id].get("special", "")).strip()
        if sp and monster_id not in specials:
            log(
                "content.missing_specials",
                "",
                0,
                "content.monster_specials",
                monster_id,
                room_id,
                notes=f"Monster has special text but no structured specials entry. special='{sp[:50]}'",
            )
            if readiness_strict:
                raise RuntimeError(f"Missing structured specials for monster_id={monster_id}")


    def room_role(room: Any) -> str:
        return str(getattr(room, "role", None) or "").strip().lower()

    def themed_outcome(room: Any) -> str:
        role = room_role(room)
        if role in {"entrance", "transit"}:
            return "empty"
        if role in {"guard", "lair", "boss"}:
            return "monster_treasure" if role in {"lair", "boss"} else "monster"
        if role in {"treasure", "treasury"}:
            return "treasure"
        if role == "hazard":
            return "trap_or_special"
        if role in {"shrine", "clue"}:
            return "special"
        r_roll, outcome = _roll_table(rng, room_table)
        log("stocking.room", room_table.get("dice", "1d6"), r_roll, room_table.get("table_id", "stocking"), str(outcome), room.room_id)
        return str(outcome)

    def choose_themed_monster_id(cl_val: str, exclude: set[str]) -> tuple[str, str, int]:
        keywords = [str(k).lower() for k in ((blueprint.context or {}).get("monster_keywords") or []) if str(k).strip()]
        themed = []
        if keywords:
            for m in monsters:
                mid = str(m.get("monster_id") or "").lower()
                if mid and any(k in mid for k in keywords):
                    themed.append(m)
        source_label = "cl_pool"
        mroll = 0
        if themed:
            try:
                mid = _choose_monster_id_for_numeric_cl(rng=rng, monsters=themed, cl=cl_val, exclude_ids=exclude)
                return mid, "theme_keywords", mroll
            except Exception:
                pass
        mid, source, mroll = _pick_from_cl_list(rng=rng, cl_lists=cl_lists, cl=cl_val, monsters=monsters, exclude_ids=exclude, strict=strict)
        return mid, source or source_label, mroll


    gates = dict((blueprint.context or {}).get("gates") or {})
    key_room_map: dict[str, dict[str, Any]] = {}
    for gate_name, gate_data in gates.items():
        if not isinstance(gate_data, dict):
            continue
        rid = str(gate_data.get("key_room_id") or "").strip()
        if rid:
            key_room_map[rid] = {
                "gate": str(gate_name),
                "key_id": str(gate_data.get("key_id") or f"{gate_name}_key"),
                "door_id": str(gate_data.get("door_id") or ""),
            }

    def add_key_special(room_id: str, specials_list: list[dict[str, Any]]) -> None:
        gate = key_room_map.get(str(room_id))
        if not gate:
            return
        label = "treasury" if "treasury" in gate.get("gate", "") else "boss"
        specials_list.append({
            "special_type": "key_cache",
            "key_id": gate.get("key_id"),
            "gate": gate.get("gate"),
            "door_id": gate.get("door_id"),
            "text": f"A hidden token or route clue here helps bypass the {label} gate.",
        })

    for room in blueprint.rooms:
        outcome = themed_outcome(room)

        monsters_list: list[dict[str, Any]] = []
        treasure_list: list[dict[str, Any]] = []
        traps_list: list[dict[str, Any]] = []
        specials_list: list[dict[str, Any]] = []

        if outcome in ("monster", "monster_treasure"):
            enc_roll, groups = roll_encounter_groups()
            log("encounter.table", "1d10", enc_roll, f"encounter.gen.dl{depth}", f"{len(groups)} groups", room.room_id)

            exclude: set[str] = set()
            for g in groups:
                # handle either A/B
                if g.get("either") is True:
                    coin = int(rng.roll("1d2"))
                    if coin == 1:
                        cl_val = str(g.get("cl"))
                        count_expr = str(g.get("count", "1"))
                    else:
                        alt = g.get("either_alt", {}) or {}
                        cl_val = str(alt.get("cl", g.get("cl")))
                        count_expr = str(alt.get("count", g.get("count", "1")))
                    log("encounter.either", "1d2", coin, f"encounter.either.dl{depth}", f"CL {cl_val}", room.room_id)
                else:
                    cl_val = str(g.get("cl"))
                    count_expr = str(g.get("count", "1"))

                # chance_50_or replacement
                if "chance_50_or" in g:
                    coin = int(rng.roll("1d2"))
                    if coin == 2:
                        alt = g.get("chance_50_or", {}) or {}
                        cl_val = str(alt.get("cl", cl_val))
                        count_expr = str(alt.get("count", count_expr))
                        log("encounter.chance50", "1d2", coin, f"encounter.50.dl{depth}", f"ALT CL {cl_val}", room.room_id)
                    else:
                        log("encounter.chance50", "1d2", coin, f"encounter.50.dl{depth}", f"PRIMARY CL {cl_val}", room.room_id)

                base_n = int(_roll_expr(rng, count_expr))
                mult = int(g.get("mult", 1))
                n = int(base_n) * int(mult)

                another = bool(g.get("another_type", False))
                mid, source, mroll = choose_themed_monster_id(cl_val, (exclude if another else set()))
                if another:
                    exclude.add(mid)

                readiness_check(mid, room.room_id)

                role = room_role(room)
                disposition = "lairing" if role in {"lair", "boss"} or outcome == "monster_treasure" else ("guarding" if role == "guard" else "patrolling")
                monsters_list.append({"monster_id": mid, "count": n, "disposition": disposition, "target_cl": cl_val})

                log(
                    "encounter.group.count",
                    count_expr,
                    base_n,
                    f"encounter.count.dl{depth}",
                    f"x{mult} => {n}",
                    room.room_id,
                    notes=f"CL {cl_val}",
                )
                log(
                    "encounter.group.monster",
                    "1d20" if mroll else "pool",
                    mroll,
                    source,
                    mid,
                    room.room_id,
                    notes=f"CL {cl_val}",
                )

        if outcome in ("treasure", "monster_treasure") and treasure_table:
            t_roll, t_res = _roll_table(rng, treasure_table)
            log("stocking.treasure", treasure_table.get("dice", ""), t_roll, treasure_table_id, str(t_res), room.room_id)
            tid = f"T{len(treasure_list):04d}"
            if isinstance(t_res, dict):
                kind = str(t_res.get("kind", "coins"))
                if kind == "coins":
                    if "amount_gp" in t_res:
                        amt = _roll_expr(rng, t_res["amount_gp"])
                        treasure_list.append({"treasure_id": tid, "kind": "coins", "amount_or_ref": int(amt), "tags": ["gp"]})
                    elif "amount_sp" in t_res:
                        amt = _roll_expr(rng, t_res["amount_sp"])
                        treasure_list.append({"treasure_id": tid, "kind": "coins", "amount_or_ref": int(amt), "tags": ["sp"]})
                    else:
                        treasure_list.append({"treasure_id": tid, "kind": "coins", "amount_or_ref": 0, "tags": []})
                elif kind == "gems":
                    cnt = _roll_expr(rng, t_res.get("count", "1d4"))
                    treasure_list.append({"treasure_id": tid, "kind": "gems", "amount_or_ref": int(cnt), "tags": []})
                else:
                    treasure_list.append({"treasure_id": tid, "kind": kind, "amount_or_ref": json.dumps(t_res, sort_keys=True), "tags": []})
            else:
                treasure_list.append({"treasure_id": tid, "kind": "item", "amount_or_ref": str(t_res), "tags": []})

        if outcome == "trap_or_special":
            coin = int(rng.roll("1d2"))
            if coin == 1:
                trap = _make_trap_from_theme(blueprint, room, rng)
                traps_list.append(trap)
                log("stocking.trap", "1d2", coin, "trap_or_special", str(trap.get("trap_type") or "trap"), room.room_id)
            else:
                special = _make_hazard_special(blueprint, room, rng)
                specials_list.append(special)
                log("stocking.special", "1d2", coin, "trap_or_special", str(special.get("special_type") or "special"), room.room_id)
        elif outcome == "special":
            role = room_role(room)
            special = _make_role_special(blueprint, room, rng)
            specials_list.append(special)
            log("stocking.special", "", 0, "role.special", str(special.get("special_type") or (role or "special")), room.room_id)

        add_key_special(room.room_id, specials_list)

        room_contents[room.room_id] = {
            "rolls": [],
            "contents": {
                "monsters": monsters_list,
                "treasure": treasure_list,
                "traps": traps_list,
                "specials": specials_list,
            },
            "alertness": ("fortified" if room_role(room) in {"guard", "boss"} else base_alertness()),
            "faction_id": str(getattr(room, "faction", None) or (blueprint.context or {}).get("faction") or ""),
        }

    wandering_entries: list[dict[str, Any]] = []
    try:
        target_cl = str(max(1, min(12, int(depth) + 1)))
    except Exception:
        target_cl = "2"
    patrol_mid, _, _ = choose_themed_monster_id(target_cl, set())
    secondary_keywords = [str((blueprint.context or {}).get("secondary_faction") or "").lower().replace(" ", "_")]
    secondary_pool = [m for m in monsters if any(tok and tok in str(m.get("monster_id") or "").lower() for tok in secondary_keywords)]
    if secondary_pool:
        side_mid = str(sorted(secondary_pool, key=lambda m: str(m.get("monster_id") or ""))[0].get("monster_id") or patrol_mid)
    else:
        side_mid = patrol_mid
    wandering_entries.extend([
        {"weight": 4, "monster_id": patrol_mid, "count_dice": "1d4", "tags": ["theme_patrol", str((blueprint.context or {}).get("faction") or "")], "preferred_states": ["patrol", "guarding"]},
        {"weight": 2, "monster_id": patrol_mid, "count_dice": "1d6", "tags": ["reinforcements", "alarm_sensitive"], "preferred_states": ["reinforcing", "hunt"]},
        {"weight": 1, "monster_id": side_mid, "count_dice": "1d3", "tags": ["scavenger", str((blueprint.context or {}).get("secondary_faction") or "")], "preferred_states": ["patrol"]},
    ])

    stocking = {
        "schema_version": "P4.9.2.6.7",
        "level_id": blueprint.level_id,
        "seed": str(seed),
        "rng_streams": {"gen.stocking": str(rng.seed), "gen.factions": "", "gen.wandering": ""},
        "room_contents": room_contents,
        "encounter_resolution": {
            "cl_lists_source": cl_lists_source,
            "cl_lists_hash": cl_lists_hash,
            "uses_compiled": bool(compiled_path.exists()),
            "stub_count": int(len(stub_records)),
        },
        "wandering": {
            "check_every_turns": 2,
            "chance_d6": 1,
            "behavior": {
                "base_state": "patrol",
                "near_guard_rooms": "patrol",
                "near_boss_ring": "reinforcing",
                "near_treasury_ring": "guarding",
            },
            "tables": [
                {
                    "table_id": f"wandering.{str((blueprint.context or {}).get('theme_id') or 'default')}",
                    "depth_min": 0,
                    "depth_max": max(1, int(depth)),
                    "entries": wandering_entries,
                }
            ],
        },
        "restock": {"enabled": False, "every_turns": 6, "policy": "none"},
        "theme": dict(blueprint.context or {}),
    }

    return StockingArtifacts(stocking=stocking, roll_log=roll_log, content_hash=content_hash, cl_lists_hash=cl_lists_hash)
