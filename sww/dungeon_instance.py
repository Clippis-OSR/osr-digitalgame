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


def generate_blueprint(dungeon_id: str, seed: int, room_count: int = 18) -> DungeonBlueprint:
    """Generate a minimal deterministic dungeon blueprint with stairs + depth.

    P6.1.1: adds multi-floor support:
    - Rooms are identified by integer ids starting at 1.
    - Edges are undirected connections between rooms.
    - Each room has:
        - depth: BFS distance from the start
        - floor: 0..(floors-1) derived from depth bands
    - Stairs are represented as normal edges plus tags:
        - stairs_down on the higher floor room
        - stairs_up on the lower floor room
    """
    rng = random.Random(int(seed))
    room_count = int(max(6, min(80, room_count)))
    start = 1
    rooms = {i: {"id": i, "tags": [], "depth": 0, "floor": 0} for i in range(1, room_count + 1)}
    rooms[start]["tags"].append("start")

    # Base connectivity: a randomized tree (connect each room i to some prior room).
    edges: list[tuple[int, int]] = []
    tree_edges: set[tuple[int, int]] = set()
    extra_edges: list[tuple[int, int]] = []

    adj: dict[int, set[int]] = {i: set() for i in rooms}
    for i in range(2, room_count + 1):
        j = rng.randint(1, i - 1)
        e = (min(j, i), max(j, i))
        edges.append((j, i))
        tree_edges.add(e)
        adj[j].add(i); adj[i].add(j)

    # Add a few extra loops to avoid pure tree feel.
    extra = max(1, room_count // 6)
    attempts = 0
    while extra > 0 and attempts < room_count * 12:
        a = rng.randint(1, room_count)
        b = rng.randint(1, room_count)
        attempts += 1
        if a == b:
            continue
        if b in adj[a]:
            continue
        if a == start and len(adj[a]) >= 4:
            continue
        edges.append((a, b))
        extra_edges.append((a, b))
        adj[a].add(b); adj[b].add(a)
        extra -= 1

    # Compute depth via BFS from start.
    depth: dict[int, int] = {start: 0}
    q = deque([start])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in depth:
                depth[v] = depth[u] + 1
                q.append(v)
    for i in rooms:
        rooms[i]["depth"] = int(depth.get(i, 0))

    max_depth = max(depth.values()) if depth else 0

    # Determine number of floors.
    # (MVP heuristic: 2 floors for small dungeons, 3 for mid, 4 for large.)
    if room_count < 14:
        floors = 2
    elif room_count < 32:
        floors = 3
    else:
        floors = 4

    # Assign floor by depth bands.
    depth_band = max(1, int(math.ceil((max_depth + 1) / max(1, floors))))
    for i in rooms:
        d = int(rooms[i]["depth"])
        f = int(min(floors - 1, d // depth_band))
        rooms[i]["floor"] = f

    # Add stairs edges between consecutive floors.
    # Ensure at least one connection between each floor band.
    def _pick_room_on_floor(ff: int, exclude: set[int]) -> Optional[int]:
        cand = [rid for rid, rr in rooms.items() if int(rr.get("floor", 0)) == ff and rid not in exclude]
        if not cand:
            return None
        # Prefer deeper rooms within the floor for stairs down.
        cand.sort(key=lambda rid: int(rooms[rid].get("depth", 0)), reverse=True)
        topk = cand[: max(1, len(cand) // 2)]
        return rng.choice(topk)

    used: set[int] = set()
    for f in range(0, floors - 1):
        a = _pick_room_on_floor(f, used)
        b = _pick_room_on_floor(f + 1, used)
        if a is None or b is None:
            continue
        if b not in adj[a]:
            edges.append((a, b))
            adj[a].add(b); adj[b].add(a)
        if "stairs_down" not in rooms[a]["tags"]:
            rooms[a]["tags"].append("stairs_down")
        if "stairs_up" not in rooms[b]["tags"]:
            rooms[b]["tags"].append("stairs_up")
        used.add(a); used.add(b)

    # Recompute depth (stairs can shorten paths).
    depth = {start: 0}
    q = deque([start])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in depth:
                depth[v] = depth[u] + 1
                q.append(v)
    for i in rooms:
        rooms[i]["depth"] = int(depth.get(i, 0))
    max_depth = max(depth.values()) if depth else 0

    # Tag rooms with a dungeon-wide context first, then derive legacy content tags from roles.
    theme = _pick_dungeon_theme(rng)
    furthest = max(depth.items(), key=lambda kv: kv[1])[0] if depth else start
    rooms[furthest]["tags"].append("boss")

    # Pick a treasury room near the boss (deep and not boss/start).
    deep_rooms = [i for i in rooms if i not in (start, furthest) and rooms[i]["depth"] >= max_depth - 1]
    treasury_room: Optional[int] = None
    if deep_rooms and rng.random() < 0.6:
        treasury_room = rng.choice(deep_rooms)
        rooms[treasury_room]["tags"].append("treasury")
    elif deep_rooms:
        treasury_room = rng.choice(deep_rooms)
        rooms[treasury_room]["tags"].append("treasure")

    defaults = dungeon_role_defaults()
    assignments, role_counts = _quota_assignments(rooms=rooms, adj=adj, depth=depth, start=start, boss_room=furthest, treasury_room=treasury_room, defaults=defaults)
    target_fracs = dict(defaults.get("target_fractions") or {})
    max_fracs = dict(defaults.get("max_fraction") or {})
    theme_mods = {str(k): float(v) for k, v in (theme.get("role_weight_modifiers") or {}).items()}
    preferred_neighbors = {str(k): [str(v) for v in vals] for k, vals in (theme.get("adjacency_bias") or {}).items() if isinstance(vals, list)}

    for i in sorted(rooms):
        if i in assignments:
            role = assignments[i]
        else:
            depth_i = int(rooms[i].get("depth", 0) or 0)
            degree = _room_neighbors(adj, i)
            frac = 0.0 if max_depth <= 0 else min(1.0, float(depth_i) / float(max_depth))
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
            for role_name, mod in theme_mods.items():
                if role_name in weights:
                    weights[role_name] += float(mod)
            neighbor_roles = [assignments.get(nb) for nb in sorted(adj.get(i, set())) if assignments.get(nb)]
            for role_name, wanted in preferred_neighbors.items():
                if role_name in weights and any(nb in wanted for nb in neighbor_roles):
                    weights[role_name] += 1.15
            if neighbor_roles:
                if "treasury" in neighbor_roles or "boss" in neighbor_roles:
                    weights["guard"] += 1.2
                    weights["clue"] += 0.4
                if "hazard" in neighbor_roles:
                    weights["lair"] += 0.5
                    weights["hazard"] += 0.4
            for role_name, target_frac in target_fracs.items():
                target = max(1, int(round(len(rooms) * float(target_frac))))
                current = int(role_counts.get(role_name, 0) or 0)
                if role_name in weights and current < target:
                    weights[role_name] += 0.75 * (target - current)
                elif role_name in weights and current > target:
                    weights[role_name] = max(0.2, weights[role_name] - 0.5 * (current - target))
            for role_name, frac_cap in max_fracs.items():
                cap = max(1, int(round(len(rooms) * float(frac_cap))))
                if role_name in weights and int(role_counts.get(role_name, 0) or 0) >= cap:
                    weights[role_name] = 0.0
            total = sum(max(0.0, float(v)) for v in weights.values())
            if total <= 0:
                role = "transit"
            else:
                roll = rng.random() * total
                upto = 0.0
                role = "transit"
                for role_name, weight in sorted(weights.items()):
                    upto += max(0.0, float(weight))
                    if roll <= upto:
                        role = str(role_name)
                        break
            assignments[i] = role
            role_counts[role] = int(role_counts.get(role, 0) or 0) + 1

        rooms[i]["role"] = role
        tags = list(rooms[i].get("tags") or [])
        for tag in _role_to_legacy_tags(role):
            if tag not in tags:
                tags.append(tag)
        role_tag = f"role:{role}"
        if role_tag not in tags:
            tags.append(role_tag)
        theme_tag = f"theme:{theme.get('theme_id')}"
        if theme_tag not in tags:
            tags.append(theme_tag)
        faction_tag = f"faction:{theme.get('faction')}"
        if faction_tag not in tags:
            tags.append(faction_tag)
        rooms[i]["tags"] = tags
        rooms[i]["theme_id"] = str(theme.get("theme_id") or "")
        rooms[i]["faction"] = str(theme.get("faction") or "")

    # Early breadth-first rooms should read as transit/guard more often; force the first
    # non-start room into a transit path if the weighted picker produced no transit at all.
    if role_counts.get("transit", 0) <= 0:
        fallback = next((rid for rid in sorted(rooms) if rid not in (start, furthest, treasury_room)), None)
        if fallback is not None:
            rooms[fallback]["role"] = "transit"
            rooms[fallback]["tags"] = [t for t in rooms[fallback]["tags"] if t not in {"monster", "trap", "treasure", "role:guard", "role:lair", "role:hazard", "role:treasure", "role:clue", "role:shrine"}]
            rooms[fallback]["tags"].append("empty")
            rooms[fallback]["tags"].append("role:transit")
            role_counts["transit"] = 1

    def _best_path(target: int) -> list[int]:
        q = deque([start])
        prev = {int(start): None}
        while q:
            cur = q.popleft()
            if cur == target:
                break
            for nb in sorted(adj.get(cur, set())):
                if nb in prev:
                    continue
                prev[nb] = cur
                q.append(nb)
        if target not in prev:
            return [int(start)]
        out = [int(target)]
        while out[-1] != int(start):
            parent = prev.get(out[-1])
            if parent is None:
                break
            out.append(parent)
        out.reverse()
        return out

    boss_path = _best_path(furthest)
    treasury_path = _best_path(treasury_room) if treasury_room is not None else []
    boss_gate_room = int(boss_path[-2]) if len(boss_path) >= 2 else int(furthest)
    treasury_gate_room = int(treasury_path[-2]) if len(treasury_path) >= 2 else (int(treasury_room) if treasury_room is not None else int(furthest))

    def _choose_key_room(path: list[int], gate_room: int | None) -> int | None:
        pool = [rid for rid in path[:-1] if rid not in {start, furthest, treasury_room, gate_room}]
        if not pool:
            pool = [rid for rid in path if rid not in {start, furthest, treasury_room, gate_room}]
        return pool[max(0, len(pool)//2 - 1)] if pool else None

    def _choose_bypass_room(path: list[int], anchors: list[int]) -> int | None:
        path_set = set(path)
        side = []
        for anchor in anchors:
            for nb in sorted(adj.get(anchor, set())):
                if nb not in path_set and nb != start:
                    side.append(nb)
        if not side:
            for rid in path[max(1, len(path)//2 - 1):]:
                for nb in sorted(adj.get(rid, set())):
                    if nb not in path_set and nb != start:
                        side.append(nb)
        side = sorted(set(side), key=lambda rid: (depth.get(rid, 0), len(adj.get(rid, set())), rid), reverse=True)
        return side[0] if side else None

    boss_key_room = _choose_key_room(boss_path, boss_gate_room)
    treasury_key_room = _choose_key_room(treasury_path, treasury_gate_room) if treasury_room is not None else None
    boss_bypass_room = _choose_bypass_room(boss_path, [boss_gate_room, furthest])
    treasury_bypass_room = _choose_bypass_room(treasury_path, [treasury_gate_room, treasury_room]) if treasury_room is not None else None

    # Build edge records with deterministic locked-door flags (P6.1.4.3).
    edge_recs = []
    eligible_idx = []
    for (a, b) in edges:
        a = int(a); b = int(b)
        stairs_edge = int(rooms[a].get("floor", 0)) != int(rooms[b].get("floor", 0))
        # Avoid locking stairs and doors connected directly to the start for MVP fairness.
        locked = False
        if not stairs_edge and a != start and b != start:
            tags_a = {t.lower() for t in (rooms[a].get("tags") or [])}
            tags_b = {t.lower() for t in (rooms[b].get("tags") or [])}
            high_value = bool({"treasure","treasury","boss"} & (tags_a | tags_b))
            p_lock = 0.20 if high_value else 0.10
            if rng.random() < p_lock:
                locked = True
            eligible_idx.append(len(edge_recs))
        edge_recs.append({"a": a, "b": b, "locked": bool(locked), "secret": False})

    # Ensure at least one locked door exists when possible so lockplay is testable.
    if eligible_idx and not any(er.get("locked") for er in edge_recs):
        pick = rng.choice(eligible_idx)
        edge_recs[pick]["locked"] = True


    
    # Mark a few extra-loop edges as secret doors (P6.1.4.5).
    # We ONLY pick from extra loop edges so secrets never block required progress.
    try:
        extra_norm = {(min(a, b), max(a, b)) for (a, b) in (extra_edges or [])}
    except Exception:
        extra_norm = set()

    secret_candidates: list[int] = []
    for idx, er in enumerate(edge_recs):
        try:
            a = int(er.get("a")); b = int(er.get("b"))
            e = (min(a, b), max(a, b))
            if e not in extra_norm:
                continue
            # Avoid stairs and doors connected directly to the start (fairness).
            if a == start or b == start:
                continue
            if int(rooms[a].get("floor", 0)) != int(rooms[b].get("floor", 0)):
                continue
            secret_candidates.append(idx)
        except Exception:
            continue

    # Choose 1..floors secret doors if possible (small but meaningful).
    if secret_candidates:
        want = max(1, min(len(secret_candidates), int(max(1, floors))))
        picks = rng.sample(secret_candidates, k=want) if len(secret_candidates) >= want else list(secret_candidates)
        for pi in picks:
            edge_recs[pi]["secret"] = True
            # Secret doors are hidden, not barred: do not also lock them.
            edge_recs[pi]["locked"] = False

    gates: dict[str, dict[str, Any]] = {}
    used_gate_edges: set[int] = set()

    def _pick_edge_for_room(room_id: int | None, *, require_secret: bool = False) -> int | None:
        if room_id is None:
            return None
        cand: list[int] = []
        for idx, er in enumerate(edge_recs):
            if idx in used_gate_edges:
                continue
            if int(er.get("a", -1)) != int(room_id) and int(er.get("b", -1)) != int(room_id):
                continue
            if require_secret and not bool(er.get("secret")):
                continue
            cand.append(idx)
        if not cand and require_secret:
            cand = _pick_edge_for_room(room_id, require_secret=False) or []
            if isinstance(cand, int):
                cand = [cand]
        if isinstance(cand, list) and cand:
            cand = sorted(cand, key=lambda i: (not bool(edge_recs[i].get("secret")), not bool(edge_recs[i].get("locked")), i))
            return cand[0]
        return None

    for name, room_id, key_room, bypass_room in (
        ("treasury_gate", (treasury_gate_room if treasury_room is not None else None), treasury_key_room, treasury_bypass_room),
        ("boss_gate", boss_gate_room, boss_key_room, boss_bypass_room),
    ):
        idx = _pick_edge_for_room(room_id)
        if idx is not None:
            edge_recs[idx]["locked"] = True
            edge_recs[idx]["gate"] = name
            edge_recs[idx]["key_id"] = f"{name}_key"
            used_gate_edges.add(idx)
            gates[name] = {"edge_index": int(idx), "room_id": int(room_id or 0), "key_room_id": int(key_room or 0), "key_id": f"{name}_key"}
        if bypass_room is not None:
            bidx = _pick_edge_for_room(bypass_room, require_secret=True)
            if bidx is not None:
                edge_recs[bidx]["secret"] = True
                edge_recs[bidx]["locked"] = False
                edge_recs[bidx]["bypass_for"] = name
                used_gate_edges.add(bidx)
                gates[f"{name}_bypass"] = {"edge_index": int(bidx), "room_id": int(bypass_room), "bypass_for": name}

    bp = {
        "version": 3,
        "dungeon_id": str(dungeon_id),
        "seed": int(seed),
        "start_room_id": int(start),
        "floors": int(floors),
        "theme": {
            "theme_id": str(theme.get("theme_id") or ""),
            "name": str(theme.get("name") or "Dungeon"),
            "faction": str(theme.get("faction") or "unknown"),
            "secondary_faction": str(theme.get("secondary_faction") or "unknown"),
            "monster_keywords": list(theme.get("monster_keywords") or []),
            "hazards": list(theme.get("hazards") or []),
            "shrines": list(theme.get("shrines") or []),
        },
        "role_counts": role_counts,
        "boss_path": boss_path,
        "treasury_path": treasury_path,
        "gates": gates,
        "rooms": {str(i): rooms[i] for i in rooms},
        "edges": edge_recs,
    }
    return DungeonBlueprint(data=bp)



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

