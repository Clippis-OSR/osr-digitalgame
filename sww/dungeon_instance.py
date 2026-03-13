from __future__ import annotations

"""Dungeon instance model (P6.1).

This module defines a stable, save-game-friendly representation of a dungeon as:

- Blueprint (immutable): layout/connectivity + room tags + metadata
- Stocking (immutable): room content assignments generated deterministically once
- Delta (mutable): per-room state (cleared/looted/triggered/door states/etc.)

P6.1.0.1 introduces the structure and save/load plumbing only; gameplay systems
may still use the legacy `game.dungeon_rooms` map until later P6.1 patches.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .data import load_json
from .dungeon_gen.pipeline import run_canonical_level_pipeline
from .dungeon_themes import dungeon_role_defaults, pick_theme_by_index

import hashlib
import json
import random
import re
import math
from collections import deque


@dataclass
class DungeonBlueprint:
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DungeonStocking:
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DungeonDelta:
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DungeonInstance:
    dungeon_id: str
    seed: int
    blueprint: DungeonBlueprint = field(default_factory=DungeonBlueprint)
    stocking: DungeonStocking = field(default_factory=DungeonStocking)
    delta: DungeonDelta = field(default_factory=DungeonDelta)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dungeon_id": self.dungeon_id,
            "seed": int(self.seed),
            "blueprint": dict(self.blueprint.data),
            "stocking": dict(self.stocking.data),
            "delta": dict(self.delta.data),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "DungeonInstance":
        dungeon_id = str(d.get("dungeon_id") or "").strip()
        seed = int(d.get("seed", 0) or 0)
        return DungeonInstance(
            dungeon_id=dungeon_id,
            seed=seed,
            blueprint=DungeonBlueprint(data=dict(d.get("blueprint") or {})),
            stocking=DungeonStocking(data=dict(d.get("stocking") or {})),
            delta=DungeonDelta(data=dict(d.get("delta") or {})),
        )


def coerce_dungeon_instance(obj: Any) -> Optional[DungeonInstance]:
    if obj is None:
        return None
    if isinstance(obj, DungeonInstance):
        return obj
    if isinstance(obj, dict):
        try:
            inst = DungeonInstance.from_dict(obj)
            # Require at least a dungeon_id to consider it valid.
            if inst.dungeon_id:
                return inst
        except Exception:
            return None
    return None




def _load_room_content_pack() -> Dict[str, Any]:
    try:
        data = load_json("dungeon_gen/room_content_pack.json") or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _weighted_pick(rng: random.Random, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    pool = [r for r in (rows or []) if isinstance(r, dict)]
    if not pool:
        return None
    total = 0.0
    weights: list[float] = []
    for r in pool:
        try:
            w = float(r.get("weight", 1.0) or 1.0)
        except Exception:
            w = 1.0
        w = max(0.0, w)
        total += w
        weights.append(w)
    if total <= 0:
        return rng.choice(pool)
    roll = rng.random() * total
    upto = 0.0
    for row, w in zip(pool, weights):
        upto += w
        if roll <= upto:
            return row
    return pool[-1]


def _copy_minimal(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    return json.loads(json.dumps(row))



def _pick_dungeon_theme(rng: random.Random) -> dict[str, Any]:
    return pick_theme_by_index(rng.randrange(1024))



_ROLE_BASE_WEIGHTS: dict[str, float] = {
    "transit": 3.2,
    "guard": 2.2,
    "lair": 1.7,
    "treasure": 1.0,
    "shrine": 0.8,
    "hazard": 1.0,
    "clue": 0.8,
}


def _pick_dungeon_theme(rng: random.Random) -> dict[str, Any]:
    # Keep theme selection deterministic without relying on a module-global catalog.
    return pick_theme_by_index(rng.randrange(1024))


def _room_neighbors(adj: dict[int, set[int]], room_id: int) -> int:
    try:
        return len(adj.get(int(room_id), set()) or set())
    except Exception:
        return 0


def _classify_room_role(*, room_id: int, rooms: dict[int, dict[str, Any]], adj: dict[int, set[int]], max_depth: int, rng: random.Random) -> str:
    rec = rooms[room_id]
    tags = {str(t).lower() for t in (rec.get("tags") or [])}
    if "start" in tags:
        return "entrance"
    if "boss" in tags:
        return "boss"
    if "treasury" in tags:
        return "treasury"

    depth = int(rec.get("depth", 0) or 0)
    floor = int(rec.get("floor", 0) or 0)
    degree = _room_neighbors(adj, room_id)
    frac = 0.0 if max_depth <= 0 else min(1.0, float(depth) / float(max_depth))

    weights = dict(_ROLE_BASE_WEIGHTS)
    if degree >= 3:
        weights["transit"] += 2.0
        weights["guard"] += 0.8
    if degree <= 1:
        weights["lair"] += 1.0
        weights["treasure"] += 0.7
        weights["clue"] += 0.5
    if frac >= 0.55:
        weights["guard"] += 0.7
        weights["lair"] += 0.9
        weights["hazard"] += 0.9
        weights["treasure"] += 0.6
        weights["transit"] = max(0.9, weights["transit"] - 0.8)
    if frac <= 0.25:
        weights["transit"] += 1.2
        weights["clue"] += 0.4
        weights["hazard"] = max(0.5, weights["hazard"] - 0.3)
    if floor >= 1:
        weights["hazard"] += 0.4 * floor
        weights["lair"] += 0.2 * floor
        weights["treasure"] += 0.15 * floor

    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return "transit"
    roll = rng.random() * total
    upto = 0.0
    for role, weight in sorted(weights.items()):
        upto += max(0.0, float(weight))
        if roll <= upto:
            return str(role)
    return "transit"


def _role_to_legacy_tags(role: str) -> list[str]:
    role = str(role or "").strip().lower()
    mapping = {
        "entrance": ["empty"],
        "transit": ["empty"],
        "guard": ["monster"],
        "lair": ["monster"],
        "treasure": ["treasure"],
        "treasury": ["treasury", "treasure"],
        "boss": ["boss", "monster", "treasure"],
        "shrine": ["empty"],
        "hazard": ["trap"],
        "clue": ["empty"],
    }
    return list(mapping.get(role, ["empty"]))


def _quota_assignments(*, rooms: dict[int, dict[str, Any]], adj: dict[int, set[int]], depth: dict[int, int], start: int, boss_room: int, treasury_room: Optional[int], defaults: dict[str, Any]) -> tuple[dict[int, str], dict[str, int]]:
    assignments: dict[int, str] = {int(start): "entrance", int(boss_room): "boss"}
    role_counts: dict[str, int] = {"entrance": 1, "boss": 1}
    if treasury_room is not None:
        assignments[int(treasury_room)] = "treasury"
        role_counts["treasury"] = 1

    dynamic_room_ids = [rid for rid in sorted(rooms) if rid not in assignments]
    mandatory = dict(defaults.get("mandatory_min") or {})
    conditional = dict(defaults.get("conditional_min") or {})
    mandatory.pop("entrance", None)
    mandatory.pop("boss", None)
    mandatory.pop("treasury", None)
    conditional.pop("entrance", None)
    conditional.pop("boss", None)
    conditional.pop("treasury", None)

    quota_roles: list[str] = []
    for role, count in sorted(mandatory.items()):
        quota_roles.extend([str(role)] * max(0, int(count or 0)))
    for role, meta in sorted(conditional.items()):
        if len(rooms) >= int((meta or {}).get("min_rooms", 9999) or 9999):
            quota_roles.extend([str(role)] * max(0, int((meta or {}).get("count", 0) or 0)))

    quota_candidates = sorted(dynamic_room_ids, key=lambda rid: (int(depth.get(rid, 0)), len(adj.get(rid, set())), rid), reverse=True)
    for role in quota_roles:
        if not quota_candidates:
            break
        picked = quota_candidates.pop(0)
        assignments[picked] = role
        role_counts[role] = int(role_counts.get(role, 0) or 0) + 1
    return assignments, role_counts


def _choose_thematic_monster_id(*, rng: random.Random, monsters: list[dict[str, Any]], target_cl: int, theme: dict[str, Any]) -> str:
    keywords = [str(k).lower() for k in (theme.get("monster_keywords") or []) if str(k).strip()]
    if keywords:
        themed: list[dict[str, Any]] = []
        for m in monsters:
            mid = str(m.get("monster_id") or "").lower()
            if not mid:
                continue
            if any(k in mid for k in keywords):
                themed.append(m)
        if themed:
            return choose_monster_id_for_target_cl(rng=rng, monsters=themed, target_cl=target_cl)
    return choose_monster_id_for_target_cl(rng=rng, monsters=monsters, target_cl=target_cl)

def stable_int_seed(base_seed: int, dungeon_id: str) -> int:
    """Derive a stable per-dungeon seed from a base seed + dungeon_id.

    Uses sha256 to avoid Python hash randomization across processes.
    """
    s = f"{int(base_seed)}::{str(dungeon_id)}".encode("utf-8")
    h = hashlib.sha256(s).hexdigest()[:16]  # 64-bit
    return int(h, 16)


def _legacy_blueprint_from_canonical(*, dungeon_id: str, seed: int, room_count: int) -> dict[str, Any]:
    """Build legacy dungeon_instance blueprint shape from canonical pipeline output."""
    rc = int(max(6, min(80, room_count)))
    if rc < 14:
        width, height = 50, 36
    elif rc < 32:
        width, height = 60, 45
    else:
        width, height = 80, 60
    depth = max(1, min(4, int(math.ceil(rc / 12.0))))

    art = run_canonical_level_pipeline(
        level_id=str(dungeon_id),
        seed=str(int(seed)),
        width=width,
        height=height,
        depth=depth,
    )
    bp = art.blueprint

    ordered = sorted(tuple(bp.rooms), key=lambda r: str(r.room_id))
    room_num_by_id = {str(r.room_id): i + 1 for i, r in enumerate(ordered)}

    raw_depth = {str(r.room_id): int(getattr(r, "depth_hint", 0) or 0) for r in ordered}
    min_depth = min(raw_depth.values()) if raw_depth else 0

    rooms: dict[str, dict[str, Any]] = {}
    role_counts: dict[str, int] = {}
    for r in ordered:
        rid = room_num_by_id[str(r.room_id)]
        role = str(getattr(r, "role", "") or "transit").strip().lower() or "transit"
        role_counts[role] = int(role_counts.get(role, 0) or 0) + 1
        tags = [str(t) for t in (getattr(r, "tags", ()) or ()) if str(t).strip()]
        role_tag = f"role:{role}"
        if role_tag not in tags:
            tags.append(role_tag)
        if role == "entrance" and "start" not in tags:
            tags.append("start")
        if role == "boss" and "boss" not in tags:
            tags.append("boss")
        if role in {"treasury", "treasure"} and "treasury" not in tags:
            tags.append("treasury")
        tags.extend(t for t in _role_to_legacy_tags(role) if t not in tags)
        rooms[str(rid)] = {
            "id": int(rid),
            "tags": tags,
            "depth": max(0, int(raw_depth.get(str(r.room_id), 0) - min_depth)),
            "floor": 0,
            "role": role,
            "theme_id": str(getattr(r, "theme_id", "") or ""),
            "faction": str(getattr(r, "faction", "") or ""),
        }

    node_kind: dict[str, str] = {}
    node_to_room: dict[str, str] = {}
    for n in (bp.connectivity.nodes or ()):
        node_id = str(getattr(n, "node_id", ""))
        kind = str(getattr(n, "kind", ""))
        node_kind[node_id] = kind
        if kind == "room":
            node_to_room[node_id] = str(getattr(n, "ref_id", ""))

    graph: dict[str, set[str]] = {}
    for e in (bp.connectivity.edges or ()):
        via = str(getattr(e, "via", ""))
        if via not in {"door", "corridor"}:
            continue
        a = str(getattr(e, "a", ""))
        b = str(getattr(e, "b", ""))
        if not a or not b:
            continue
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)

    edge_pairs: set[tuple[int, int]] = set()
    adjacency: dict[int, set[int]] = {int(v): set() for v in room_num_by_id.values()}
    seen: set[str] = set()
    for nid, kind in node_kind.items():
        if nid in seen or kind != "corridor":
            continue
        comp = []
        q = deque([nid])
        seen.add(nid)
        while q:
            cur = q.popleft()
            comp.append(cur)
            for nb in graph.get(cur, set()):
                if nb in seen:
                    continue
                if node_kind.get(nb) == "corridor":
                    seen.add(nb)
                    q.append(nb)
        room_refs: set[str] = set()
        for c in comp:
            for nb in graph.get(c, set()):
                rr = node_to_room.get(nb)
                if rr:
                    room_refs.add(rr)
        refs = sorted(room_refs)
        for i in range(len(refs)):
            for j in range(i + 1, len(refs)):
                a = int(room_num_by_id.get(refs[i], 0) or 0)
                b = int(room_num_by_id.get(refs[j], 0) or 0)
                if a <= 0 or b <= 0 or a == b:
                    continue
                lo, hi = (a, b) if a < b else (b, a)
                edge_pairs.add((lo, hi))
                adjacency.setdefault(lo, set()).add(hi)
                adjacency.setdefault(hi, set()).add(lo)

    edges = [{"a": a, "b": b, "locked": False, "secret": False} for (a, b) in sorted(edge_pairs)]
    gates: dict[str, dict[str, Any]] = {}
    if len(edges) >= 1:
        gate_idx = min(len(edges) - 1, max(0, len(edges) // 2))
        edges[gate_idx]["locked"] = True
        gates["boss_gate"] = {
            "edge_index": int(gate_idx),
            "room_id": int(edges[gate_idx].get("b") or 0),
            "key_room_id": 0,
            "key_id": "boss_gate_key",
        }

    start_room_id = next((rid for rid, rr in rooms.items() if str(rr.get("role")) == "entrance"), None)
    if not start_room_id:
        start_room_id = str(min((int(k) for k in rooms.keys()), default=1))
    start = int(start_room_id)

    def _bfs_path(goal: int) -> list[int]:
        if goal <= 0 or goal == start:
            return [start]
        seen = {start}
        prev: dict[int, int | None] = {start: None}
        q = deque([start])
        while q:
            cur = q.popleft()
            for nb in sorted(adjacency.get(cur, set())):
                if nb in seen:
                    continue
                seen.add(nb)
                prev[nb] = cur
                if nb == goal:
                    out = [goal]
                    while out[-1] != start:
                        p = prev.get(out[-1])
                        if p is None:
                            return []
                        out.append(p)
                    out.reverse()
                    return out
                q.append(nb)
        return []

    boss_room = next((int(k) for k, rr in rooms.items() if str(rr.get("role")) == "boss"), 0)
    treasury_room = next((int(k) for k, rr in rooms.items() if str(rr.get("role")) == "treasury"), 0)
    if int(max(1, depth)) > 1:
        stairs_room = str(start)
        tags = list((rooms.get(stairs_room, {}) or {}).get("tags") or [])
        if "stairs_down" not in tags:
            tags.append("stairs_down")
            rooms[stairs_room]["tags"] = tags

    return {
        "version": 4,
        "dungeon_id": str(dungeon_id),
        "seed": int(seed),
        "start_room_id": int(start),
        "floors": int(max(1, depth)),
        "theme": dict(art.stocking.get("theme") or {}),
        "role_counts": role_counts,
        "boss_path": _bfs_path(boss_room) if boss_room else [],
        "treasury_path": _bfs_path(treasury_room) if treasury_room else [],
        "gates": gates,
        "rooms": rooms,
        "edges": edges,
    }


def generate_blueprint(dungeon_id: str, seed: int, room_count: int = 18) -> DungeonBlueprint:
    """Generate legacy-shaped blueprint by adapting canonical pipeline output."""
    return DungeonBlueprint(
        data=_legacy_blueprint_from_canonical(
            dungeon_id=str(dungeon_id),
            seed=int(seed),
            room_count=int(room_count),
        )
    )


def _stable_room_seed(seed: int, dungeon_id: str, purpose: str, room_id: int) -> int:
    s = f"{int(seed)}::{dungeon_id}::{purpose}::{int(room_id)}".encode("utf-8")
    h = hashlib.sha256(s).hexdigest()[:16]
    return int(h, 16)


def _parse_number_appearing(expr: str) -> str:
    """Normalize monster 'number_appearing' into a rollable expression string."""
    s = str(expr or "").strip().lower().replace("×", "x")
    if not s or s in {"—", "-", "varies"}:
        return "1"
    if "d" in s:
        s2 = re.sub(r"[^0-9d+\-*x ]", "", s)
        return s2.replace("x", "*").replace(" ", "") or "1"
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", s)
    if m:
        lo = int(m.group(1)); hi = int(m.group(2))
        if lo == 1 and hi in (4, 6, 8, 10, 12, 20):
            return f"1d{hi}"
        if lo == 2 and hi == 12:
            return "2d6"
        if lo == 3 and hi == 18:
            return "3d6"
        span = max(1, hi - lo + 1)
        if span <= 20:
            return f"1d{span}+{lo-1}"
        return str(lo)
    if s.isdigit():
        return s
    return "1"


def choose_monster_id_for_target_cl(*, rng: random.Random, monsters: list[dict[str, Any]], target_cl: int) -> str:
    """Choose a monster_id roughly matching target challenge level.

    Public helper used by both room stocking and wandering encounter selection.
    """
    target_cl = max(1, int(target_cl))
    pool: list[dict[str, Any]] = []
    for m in monsters:
        raw = m.get("challenge_level", None)
        if raw in (None, "", "—"):
            continue
        try:
            cl = int(str(raw).replace("+", ""))
        except Exception:
            continue
        if cl == target_cl:
            pool.append(m)
    if not pool:
        for m in monsters:
            raw = m.get("challenge_level", None)
            if raw in (None, "", "—"):
                continue
            try:
                cl = int(str(raw).replace("+", ""))
            except Exception:
                continue
            if abs(cl - target_cl) <= 1:
                pool.append(m)
    if not pool:
        pool = list(monsters)
    pool = sorted(pool, key=lambda m: str(m.get("monster_id") or m.get("name") or ""))
    if not pool:
        return "unknown"
    return str(rng.choice(pool).get("monster_id") or pool[0].get("name") or "unknown")

# Back-compat alias: older code referenced the private helper name.
_choose_monster_id_for_depth = choose_monster_id_for_target_cl



def generate_stocking(*, dungeon_id: str, seed: int, blueprint: DungeonBlueprint) -> DungeonStocking:
    """Generate a deterministic stocking plan for a blueprint.

    P6.1.4.83 adds room-role-aware stocking and dungeon-wide theme context so
    rooms no longer behave like isolated slot machines. Legacy fields remain in
    place so the runtime can keep using the existing room/type handlers.
    """
    monsters: list[dict[str, Any]] = load_json("monsters.json") or []
    bp_data = blueprint.data or {}
    rooms = bp_data.get("rooms") or {}
    theme = dict(bp_data.get("theme") or {})
    stocked: dict[str, Any] = {}

    max_eff_depth = 0
    for _rid, rr in rooms.items():
        try:
            d = int((rr or {}).get("depth", 0))
            f = int((rr or {}).get("floor", 0) or 0)
            eff = d + (f * 3)
            max_eff_depth = max(max_eff_depth, int(eff))
        except Exception:
            pass

    # Treasure generator (deterministic) uses a local Dice backed by a stable Random.
    from .dice import Dice
    from .treasure import TreasureGenerator

    content_pack = _load_room_content_pack()
    room_features = list(content_pack.get("room_features") or [])
    trap_templates = list(content_pack.get("traps") or [])
    event_templates = list(content_pack.get("events") or [])
    shrine_templates = list(content_pack.get("shrines") or [])
    puzzle_templates = list(content_pack.get("puzzles") or [])

    for rid, rr in rooms.items():
        try:
            room_id = int(rid)
        except Exception:
            continue
        tags = list((rr or {}).get("tags") or [])
        depth = int((rr or {}).get("depth", 0) or 0)
        floor = int((rr or {}).get("floor", 0) or 0)
        eff_depth = int(depth + (floor * 3))
        role = str((rr or {}).get("role") or "").strip().lower()
        if not role:
            if "boss" in tags:
                role = "boss"
            elif "treasury" in tags:
                role = "treasury"
            elif "trap" in tags:
                role = "hazard"
            elif "monster" in tags:
                role = "guard"
            elif "treasure" in tags:
                role = "treasure"
            else:
                role = "transit"

        kind_map = {
            "entrance": "start",
            "transit": "empty",
            "guard": "monster",
            "lair": "monster",
            "treasure": "treasure",
            "treasury": "treasury",
            "boss": "boss",
            "shrine": "empty",
            "hazard": "trap",
            "clue": "empty",
        }
        kind = kind_map.get(role, "empty")
        rs: dict[str, Any] = {
            "room_id": room_id,
            "depth": depth,
            "floor": floor,
            "eff_depth": eff_depth,
            "tags": tags,
            "kind": kind,
            "role": role,
            "theme_id": str(theme.get("theme_id") or (rr or {}).get("theme_id") or ""),
            "faction": str(theme.get("faction") or (rr or {}).get("faction") or ""),
            "title": role.replace("_", " ").title(),
        }

        if kind in {"monster", "boss"}:
            if max_eff_depth <= 0:
                target_cl = 1
            else:
                cap = min(12, max_eff_depth + 1)
                target_cl = 1 + int(round((cap - 1) * (eff_depth / max_eff_depth)))
                target_cl = max(1, min(12, target_cl))
            if role == "boss":
                target_cl = min(12, target_cl + 2)
            elif role == "guard":
                target_cl = max(1, target_cl)
            elif role == "lair":
                target_cl = min(12, target_cl + 1)

            rrng = random.Random(_stable_room_seed(seed, dungeon_id, "encounter", room_id))
            mid = _choose_thematic_monster_id(rng=rrng, monsters=monsters, target_cl=target_cl, theme=theme)
            mrow = next((m for m in monsters if str(m.get("monster_id")) == mid), None)
            count_expr = _parse_number_appearing((mrow or {}).get("number_appearing", "1"))
            if role == "boss":
                count_expr = "1"
            cnt = 1
            try:
                if isinstance(count_expr, str) and "d" in count_expr:
                    cnt = int(Dice(rng=rrng).roll(count_expr).total)
                else:
                    cnt = int(str(count_expr).strip() or "1")
            except Exception:
                cnt = 1
            if role == "guard":
                cnt = max(1, min(cnt, 6))
            elif role == "lair":
                cnt = max(2, min(cnt + 1, 12))
            rs["encounter"] = {
                "monster_id": mid,
                "count": int(max(1, cnt)),
                "target_cl": int(target_cl),
                "role": role,
            }

        if kind in {"treasure", "boss", "treasury"} or role in {"shrine", "clue"}:
            if eff_depth <= max(1, max_eff_depth // 3):
                table_id = "HOARD_A"
            elif eff_depth <= max(2, (2 * max_eff_depth) // 3):
                table_id = "HOARD_B"
            else:
                table_id = "HOARD_C"
            if kind in ("boss", "treasury"):
                table_id = "HOARD_C"
            elif role == "shrine" and table_id == "HOARD_C":
                table_id = "HOARD_B"
            elif role == "clue":
                table_id = "HOARD_A"

            trng = random.Random(_stable_room_seed(seed, dungeon_id, "treasure", room_id))
            if table_id != "HOARD_C" and trng.random() < 0.15:
                table_id = "HOARD_B" if table_id == "HOARD_A" else "HOARD_C"
            tier = "minor" if table_id == "HOARD_A" else ("medium" if table_id == "HOARD_B" else "major")
            tdice = Dice(rng=trng)
            tgen = TreasureGenerator(tdice)
            hoard = tgen.roll_hoard(tier)
            gp_total = 0
            items_out: list[dict[str, Any]] = []
            for it in hoard:
                if getattr(it, "kind", None) == "coins" and getattr(it, "gp_value", None) is not None:
                    gp_total += int(it.gp_value)
                    continue
                items_out.append({"name": it.name, "kind": it.kind, "gp_value": it.gp_value})
            if kind == "treasury":
                gp_total += 200
            elif role == "shrine":
                gp_total = int(gp_total * 0.7)
            elif role == "clue":
                gp_total = min(gp_total, 40)
                items_out = items_out[:1]

            rs["treasure"] = {
                "table_id": str(table_id),
                "major": bool(kind == "boss" or ("boss" in tags)),
                "gp": int(gp_total),
                "items": items_out,
            }

        if kind == "trap":
            trrng = random.Random(_stable_room_seed(seed, dungeon_id, "trap", room_id))
            hazard_names = [str(h) for h in (theme.get("hazards") or []) if str(h).strip()]
            trap_kind = trrng.choice(hazard_names or ["pit", "dart", "gas", "blade", "alarm"])
            rs["trap"] = {
                "trap_kind": trap_kind,
                "dc": int(10 + min(10, depth)),
                "damage": trrng.choice(["1d6", "2d6", "1d8", "1d10"]),
            }

        srng = random.Random(_stable_room_seed(seed, dungeon_id, "special", room_id))

        if kind == "trap" and trap_templates:
            preferred = [t for t in trap_templates if isinstance(t, dict) and str(t.get("id") or "") in {str(h) for h in (theme.get("hazards") or [])}]
            trow = _weighted_pick(srng, preferred or trap_templates)
            if isinstance(trow, dict):
                rs["trap"] = {
                    "trap_id": str(trow.get("id") or "trap"),
                    "trap_kind": str(trow.get("name") or trow.get("id") or "trap"),
                    "desc": str(trow.get("desc") or "A hidden trap triggers!"),
                    "dc": int(trow.get("dc") or rs.get("trap", {}).get("dc") or 10),
                    "damage": str(trow.get("damage") or rs.get("trap", {}).get("damage") or "1d6"),
                    "alarm": bool(trow.get("alarm", False)),
                }

        depth_bias = max(0.0, min(0.10, 0.02 * max(0, depth - 1)))
        role_feature_bias = {
            "entrance": 0.15,
            "transit": 0.32,
            "guard": 0.20,
            "lair": 0.24,
            "treasure": 0.34,
            "treasury": 0.38,
            "boss": 0.22,
            "shrine": 0.18,
            "hazard": 0.10,
            "clue": 0.16,
        }
        feature_chance = role_feature_bias.get(role, 0.15) + depth_bias
        added_major = False
        if room_features and srng.random() < feature_chance:
            themed_features = [rf for rf in room_features if isinstance(rf, dict) and any(tok in str(rf.get("name") or "").lower() for tok in [str(x).lower() for x in (theme.get("features") or [])])]
            spec = _copy_minimal(_weighted_pick(srng, themed_features or room_features))
            if spec:
                rs["special_room"] = spec
                added_major = True

        event_chance = 0.03 if role == "entrance" else (0.08 + depth_bias)
        if role in {"hazard", "boss"}:
            event_chance = max(0.04, event_chance - 0.02)
        if event_templates and srng.random() < event_chance:
            ev = _copy_minimal(_weighted_pick(srng, event_templates))
            if ev:
                rs["room_event"] = ev

        shrine_chance = 0.0
        if role == "shrine":
            shrine_chance = 0.95
        elif role in {"treasure", "clue"}:
            shrine_chance = 0.10 + (depth_bias * 0.2)
        if shrine_templates and not added_major and srng.random() < shrine_chance:
            preferred = [s for s in shrine_templates if isinstance(s, dict) and any(tok in str(s.get("name") or "").lower() for tok in [str(x).lower() for x in (theme.get("shrines") or [])])]
            shr = _copy_minimal(_weighted_pick(srng, preferred or shrine_templates))
            if shr:
                rs["shrine"] = shr
                added_major = True

        puzzle_chance = 0.0
        if role == "clue":
            puzzle_chance = 0.35
        elif role in {"treasure", "transit", "guard"}:
            puzzle_chance = 0.06 + (depth_bias * 0.2)
        if puzzle_templates and not added_major and srng.random() < puzzle_chance:
            pz = _copy_minimal(_weighted_pick(srng, puzzle_templates))
            if pz:
                rs["puzzle"] = pz
                added_major = True

        stocked[str(room_id)] = rs

    gates = dict(bp_data.get("gates") or {})
    key_room_lookup: dict[int, dict[str, Any]] = {}
    for gate_name, gate_meta in gates.items():
        if isinstance(gate_meta, dict) and int(gate_meta.get("key_room_id", 0) or 0) > 0:
            key_room_lookup[int(gate_meta.get("key_room_id") or 0)] = dict(gate_meta)
            key_room_lookup[int(gate_meta.get("key_room_id") or 0)]["gate_name"] = str(gate_name)
    for rid, rec in stocked.items():
        meta = key_room_lookup.get(int(rid))
        if not meta:
            continue
        rec.setdefault("special_room", {})
        rec["special_room"].update({
            "feature_type": "key_cache",
            "key_id": str(meta.get("key_id") or "gate_key"),
            "gate": str(meta.get("gate_name") or "gate"),
            "desc": "A hidden cache here contains a token, map, or route clue for a deeper gate.",
        })
    patrol_mid = "rat_giant"
    try:
        patrol_mid = _choose_thematic_monster_id(rng=random.Random(_stable_room_seed(seed, dungeon_id, "wandering", 0)), monsters=monsters, target_cl=max(1, min(12, max_eff_depth // 2 + 1)), theme=theme)
    except Exception:
        pass
    wandering = {
        "check_every_turns": 2,
        "chance_d6": 1,
        "behavior": {
            "base_state": "patrol",
            "near_guard_rooms": "patrol",
            "near_boss_ring": "reinforcing",
            "near_treasury_ring": "guarding",
            "alarm_states": {"0": "patrol", "2": "reinforcing", "4": "hunt"},
            "avoid_cleared_rooms": True,
            "respect_aftermath_turns": 2,
        },
        "entries": [
            {"weight": 4, "monster_id": patrol_mid, "count_dice": "1d4", "tags": ["theme_patrol", str(theme.get("faction") or "unknown")], "preferred_states": ["patrol", "guarding"]},
            {"weight": 2, "monster_id": patrol_mid, "count_dice": "1d6", "tags": ["reinforcements", "alarm_sensitive"], "preferred_states": ["reinforcing", "hunt"]},
        ],
    }
    return DungeonStocking(data={"version": 4, "theme": theme, "rooms": stocked, "gates": gates, "wandering": wandering})


def ensure_instance(existing: Optional[DungeonInstance], dungeon_id: str, base_seed: int, room_count: int = 18) -> DungeonInstance:
    """Return an instance for dungeon_id, creating/replacing if needed.

    P6.1.0.2: creates blueprint deterministically.
    P6.1.0.3: fills stocking deterministically *once* and persists it; existing room
    stocking entries are not overwritten.
    """
    dungeon_id = str(dungeon_id)
    base_seed = int(base_seed)

    if existing is not None and isinstance(existing, DungeonInstance) and existing.dungeon_id == dungeon_id:
        inst = existing
    else:
        seed = stable_int_seed(base_seed, dungeon_id)
        bp = generate_blueprint(dungeon_id=dungeon_id, seed=seed, room_count=room_count)
        inst = DungeonInstance(
            dungeon_id=dungeon_id,
            seed=int(seed),
            blueprint=bp,
            stocking=DungeonStocking(data={"version": 4, "rooms": {}}),
            delta=DungeonDelta(data={"version": 1, "rooms": {}}),
        )

    # Ensure blueprint exists
    if not (inst.blueprint and inst.blueprint.data and inst.blueprint.data.get("rooms")):
        seed = stable_int_seed(base_seed, dungeon_id)
        inst.seed = int(inst.seed or seed)
        inst.blueprint = generate_blueprint(dungeon_id=dungeon_id, seed=int(inst.seed), room_count=room_count)

    # Fill missing stocking entries deterministically (do not overwrite existing).
    rooms = (inst.blueprint.data or {}).get("rooms") or {}
    stocked_rooms = (inst.stocking.data or {}).setdefault("rooms", {})
    if not isinstance(stocked_rooms, dict):
        stocked_rooms = {}
        inst.stocking.data["rooms"] = stocked_rooms
    if int(inst.stocking.data.get("version", 4) or 4) != 4:
        inst.stocking.data["version"] = 4

    # Only generate once if empty or missing rooms
    missing = [rid for rid in rooms.keys() if str(rid) not in stocked_rooms]
    if missing:
        generated = generate_stocking(dungeon_id=dungeon_id, seed=int(inst.seed), blueprint=inst.blueprint)
        gen_rooms = (generated.data or {}).get("rooms") or {}
        for rid in rooms.keys():
            k = str(rid)
            if k not in stocked_rooms and k in gen_rooms:
                stocked_rooms[k] = gen_rooms[k]

    # Ensure delta container structure
    delta_rooms = (inst.delta.data or {}).setdefault("rooms", {})
    if not isinstance(delta_rooms, dict):
        inst.delta.data["rooms"] = {}
    if int(inst.delta.data.get("version", 1) or 1) != 1:
        inst.delta.data["version"] = 1

    return inst
