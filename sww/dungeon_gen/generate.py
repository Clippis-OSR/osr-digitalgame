from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

from .blueprint import (
    ConnectivityGraph,
    Door,
    DoorLock,
    DoorSecret,
    DungeonBlueprint,
    Feature,
    Point,
    stable_hash_dict,
)
from .connectivity import ConnectivityBuildResult, build_connectivity
from .doors import place_doors
from .layout import generate_rooms_bsp
from .rng_streams import RNGStreams
from .stairs import inject_stairs_into_connectivity, place_stairs
from .stocking import generate_stocking
from .instance_state import initialize_instance_state, resolve_variant_spawns
from .validate import validate_blueprint
from ..dungeon_themes import dungeon_role_defaults, pick_theme_by_index


def _pick_theme(streams: RNGStreams) -> dict[str, Any]:
    stream = streams.stream("gen.theme")
    return pick_theme_by_index(stream.rng.randrange(1024))


def _room_adjacency(conn: ConnectivityBuildResult) -> dict[str, set[str]]:
    node_to_room = {node_id: room_id for room_id, node_id in conn.room_node.items()}
    corridor_links: dict[str, list[str]] = {}
    for e in conn.graph.edges:
        if e.via != "corridor" or not e.via_id:
            continue
        for node_id in (e.a, e.b):
            rid = node_to_room.get(node_id)
            if rid:
                corridor_links.setdefault(str(e.via_id), []).append(rid)
    adj: dict[str, set[str]] = {rid: set() for rid in conn.room_node}
    for ids in corridor_links.values():
        uniq = sorted(set(ids))
        if len(uniq) == 2:
            a, b = uniq
            adj[a].add(b)
            adj[b].add(a)
    return adj


def _distance_by_room(conn: ConnectivityBuildResult, start_room_id: str) -> dict[str, int]:
    adj = _room_adjacency(conn)
    q = [start_room_id]
    dist = {start_room_id: 0}
    while q:
        cur = q.pop(0)
        for nb in sorted(adj.get(cur, set())):
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                q.append(nb)
    return dist




def _bfs_path(adj: dict[str, set[str]], start: str, goal: str) -> list[str]:
    if start == goal:
        return [start]
    q = [start]
    prev: dict[str, Optional[str]] = {start: None}
    while q:
        cur = q.pop(0)
        for nb in sorted(adj.get(cur, set())):
            if nb in prev:
                continue
            prev[nb] = cur
            if nb == goal:
                out = [goal]
                while out[-1] != start:
                    parent = prev.get(out[-1])
                    if parent is None:
                        return []
                    out.append(parent)
                out.reverse()
                return out
            q.append(nb)
    return []


def _pick_best_unassigned(candidates: list[str], assignments: dict[str, str], dist: dict[str, int], adj: dict[str, set[str]]) -> Optional[str]:
    available = [rid for rid in candidates if rid not in assignments]
    if not available:
        return None
    available.sort(key=lambda rid: (dist.get(rid, 0), len(adj.get(rid, set())), rid), reverse=True)
    return available[0]


def _shape_special_zones(
    rooms: tuple,
    conn: ConnectivityBuildResult,
    assignments: dict[str, str],
    role_counts: dict[str, int],
    start_room_id: str,
    boss_room_id: str,
    treasury_room_id: Optional[str],
) -> dict[str, Any]:
    adj = _room_adjacency(conn)
    dist = _distance_by_room(conn, start_room_id)
    room_ids = {r.room_id for r in rooms}
    boss_path = _bfs_path(adj, start_room_id, boss_room_id)
    treasury_path = _bfs_path(adj, start_room_id, treasury_room_id) if treasury_room_id else []

    boss_antechamber_id = None
    if len(boss_path) >= 2:
        cand = boss_path[-2]
        if cand in room_ids and cand not in {start_room_id, boss_room_id, treasury_room_id}:
            prior = assignments.get(cand)
            assignments[cand] = 'guard'
            role_counts['guard'] = int(role_counts.get('guard', 0) or 0) + (0 if prior == 'guard' else 1)
            if prior and prior != 'guard':
                role_counts[prior] = max(0, int(role_counts.get(prior, 0) or 0) - 1)
            boss_antechamber_id = cand

    treasury_guard_ids: list[str] = []
    if treasury_room_id:
        guard_pool = [rid for rid in sorted(adj.get(treasury_room_id, set())) if rid not in {start_room_id, boss_room_id, treasury_room_id}]
        best_guard = _pick_best_unassigned(guard_pool, assignments, dist, adj)
        if best_guard:
            prior = assignments.get(best_guard)
            assignments[best_guard] = 'guard'
            role_counts['guard'] = int(role_counts.get('guard', 0) or 0) + (0 if prior == 'guard' else 1)
            if prior and prior != 'guard':
                role_counts[prior] = max(0, int(role_counts.get(prior, 0) or 0) - 1)
            treasury_guard_ids.append(best_guard)

    clue_targets: dict[str, str] = {}
    clue_candidates: list[str] = []
    for path, target in ((boss_path, 'boss'), (treasury_path, 'treasury')):
        if len(path) < 3:
            continue
        idx = max(1, len(path) // 2 - 1)
        for offset in (0, -1, 1, -2, 2):
            j = idx + offset
            if 0 < j < len(path) - 1:
                rid = path[j]
                if rid in {start_room_id, boss_room_id, treasury_room_id}:
                    continue
                clue_candidates.append((rid, target))
                break
    for rid, target in clue_candidates:
        if rid in clue_targets:
            continue
        prior = assignments.get(rid)
        assignments[rid] = 'clue'
        role_counts['clue'] = int(role_counts.get('clue', 0) or 0) + (0 if prior == 'clue' else 1)
        if prior and prior != 'clue':
            role_counts[prior] = max(0, int(role_counts.get(prior, 0) or 0) - 1)
        clue_targets[rid] = target

    return {
        'boss_path_room_ids': boss_path,
        'treasury_path_room_ids': treasury_path,
        'boss_antechamber_id': boss_antechamber_id,
        'treasury_guard_ids': treasury_guard_ids,
        'clue_targets': clue_targets,
    }


def _shape_light_gating(
    rooms: tuple,
    conn: ConnectivityBuildResult,
    assignments: dict[str, str],
    start_room_id: str,
    boss_room_id: str,
    treasury_room_id: Optional[str],
    zone_context: dict[str, Any],
) -> dict[str, Any]:
    adj = _room_adjacency(conn)
    dist = _distance_by_room(conn, start_room_id)
    boss_path = list(zone_context.get("boss_path_room_ids") or [])
    treasury_path = list(zone_context.get("treasury_path_room_ids") or [])
    clue_targets = dict(zone_context.get("clue_targets") or {})

    def choose_key_room(path: list[str], gate_room_id: Optional[str]) -> str:
        if not path:
            return ""
        candidates = [rid for rid in path[:-1] if rid not in {start_room_id, boss_room_id, treasury_room_id, gate_room_id}]
        if not candidates:
            candidates = [rid for rid in path if rid not in {start_room_id, boss_room_id, treasury_room_id, gate_room_id}]
        if not candidates:
            return ""
        preferred = [rid for rid in candidates if assignments.get(rid) in {"guard", "clue", "transit", "lair"}]
        pool = preferred or candidates
        pool = sorted(pool, key=lambda rid: (assignments.get(rid) == "clue", dist.get(rid, 0), rid), reverse=True)
        return pool[0]

    def choose_secret_bypass(path: list[str], gate_room_id: Optional[str], target_room_id: Optional[str]) -> str:
        path_set = set(path)
        preferred = []
        anchors = [rid for rid in [gate_room_id, target_room_id] if rid]
        for anchor in anchors:
            for nb in sorted(adj.get(anchor, set())):
                if nb in path_set or nb == start_room_id:
                    continue
                preferred.append(nb)
        if preferred:
            preferred = sorted(set(preferred), key=lambda rid: (dist.get(rid, 0), len(adj.get(rid, set())), rid), reverse=True)
            return preferred[0]
        # fall back to any side room touching the latter half of the path
        tail = path[max(1, len(path)//2 - 1):]
        side = []
        for rid in tail:
            for nb in sorted(adj.get(rid, set())):
                if nb in path_set or nb == start_room_id:
                    continue
                side.append(nb)
        if side:
            side = sorted(set(side), key=lambda rid: (dist.get(rid, 0), len(adj.get(rid, set())), rid), reverse=True)
            return side[0]
        return ""

    treasury_gate_room_id = ""
    if treasury_room_id:
        treasury_gate_room_id = str((zone_context.get("treasury_guard_ids") or [""])[0] or "")
    boss_gate_room_id = str(zone_context.get("boss_antechamber_id") or "")

    treasury_key_room_id = choose_key_room(treasury_path, treasury_gate_room_id) if treasury_room_id else ""
    boss_key_room_id = choose_key_room(boss_path, boss_gate_room_id)
    treasury_secret_room_id = choose_secret_bypass(treasury_path, treasury_gate_room_id, treasury_room_id) if treasury_room_id else ""
    boss_secret_room_id = choose_secret_bypass(boss_path, boss_gate_room_id, boss_room_id)

    room_notes: dict[str, str] = {}
    room_tags: dict[str, list[str]] = {}
    for rid, label in ((treasury_key_room_id, "treasury"), (boss_key_room_id, "boss")):
        if rid:
            room_tags.setdefault(rid, []).append(f"key_room:{label}")
            room_notes[rid] = f"This chamber likely hides a {label} gate key or bypass clue."
            if assignments.get(rid) != "clue":
                clue_targets.setdefault(rid, label)
    for rid, label in ((treasury_secret_room_id, "treasury"), (boss_secret_room_id, "boss")):
        if rid:
            room_tags.setdefault(rid, []).append(f"secret_bypass:{label}")
            room_notes[rid] = f"A side route here could bypass the main {label} gate."

    return {
        "treasury_gate_room_id": treasury_gate_room_id,
        "boss_gate_room_id": boss_gate_room_id,
        "treasury_key_room_id": treasury_key_room_id,
        "boss_key_room_id": boss_key_room_id,
        "treasury_secret_room_id": treasury_secret_room_id,
        "boss_secret_room_id": boss_secret_room_id,
        "clue_targets": clue_targets,
        "room_notes": room_notes,
        "room_tags": room_tags,
    }


def _apply_gate_doors(
    doors: tuple[Door, ...],
    gate_context: dict[str, Any],
) -> tuple[tuple[Door, ...], dict[str, Any]]:
    room_to_indices: dict[str, list[int]] = {}
    for idx, door in enumerate(doors):
        for ep in door.connects:
            if ep.kind == "room":
                room_to_indices.setdefault(str(ep.id), []).append(idx)

    def pick_gate_idx(room_id: str, exclude: set[int]) -> Optional[int]:
        if not room_id:
            return None
        candidates = [i for i in room_to_indices.get(str(room_id), []) if i not in exclude]
        if not candidates:
            return None
        def rank(i: int):
            d = doors[i]
            return (
                d.door_type == "normal",
                d.door_type == "stuck",
                d.secret is None,
                d.lock is None,
                -len(d.tags),
                d.door_id,
            )
        candidates.sort(key=rank, reverse=True)
        return candidates[0]

    def pick_secret_idx(room_id: str, exclude: set[int]) -> Optional[int]:
        if not room_id:
            return None
        candidates = [i for i in room_to_indices.get(str(room_id), []) if i not in exclude]
        if not candidates:
            return None
        secret_first = [i for i in candidates if doors[i].secret is not None or doors[i].door_type == "secret"]
        if secret_first:
            return sorted(secret_first, key=lambda i: doors[i].door_id)[0]
        ranked = sorted(candidates, key=lambda i: (doors[i].door_type == "normal", doors[i].door_id), reverse=True)
        return ranked[0] if ranked else None

    updated = list(doors)
    used: set[int] = set()
    applied: dict[str, Any] = {}

    def force_locked(idx: int, tag: str):
        d = updated[idx]
        tags = tuple(sorted(set(d.tags + (tag,))))
        lock = d.lock if d.lock is not None else DoorLock(difficulty=7)
        updated[idx] = Door(
            door_id=d.door_id,
            pos=d.pos,
            orientation=d.orientation,
            connects=d.connects,
            door_type="locked" if d.door_type != "barred" else d.door_type,
            tags=tags,
            lock=lock,
            secret=None if d.door_type != "secret" else DoorSecret(dc=1),
        )

    def force_secret(idx: int, tag: str):
        d = updated[idx]
        tags = tuple(sorted(set(d.tags + (tag,))))
        updated[idx] = Door(
            door_id=d.door_id,
            pos=d.pos,
            orientation=d.orientation,
            connects=d.connects,
            door_type="secret",
            tags=tags,
            lock=None,
            secret=d.secret if d.secret is not None else DoorSecret(dc=1),
        )

    for name, room_key, key_key, tag in (
        ("treasury_gate", "treasury_gate_room_id", "treasury_key_room_id", "gate:treasury"),
        ("boss_gate", "boss_gate_room_id", "boss_key_room_id", "gate:boss"),
    ):
        idx = pick_gate_idx(str(gate_context.get(room_key) or ""), used)
        if idx is not None:
            force_locked(idx, tag)
            used.add(idx)
            applied[name] = {
                "door_id": updated[idx].door_id,
                "room_id": str(gate_context.get(room_key) or ""),
                "key_room_id": str(gate_context.get(key_key) or ""),
                "key_id": f"{name}_key",
            }

    for name, room_key, tag in (
        ("treasury_bypass", "treasury_secret_room_id", "bypass:treasury"),
        ("boss_bypass", "boss_secret_room_id", "bypass:boss"),
    ):
        idx = pick_secret_idx(str(gate_context.get(room_key) or ""), used)
        if idx is not None:
            force_secret(idx, tag)
            used.add(idx)
            applied[name] = {
                "door_id": updated[idx].door_id,
                "room_id": str(gate_context.get(room_key) or ""),
            }

    return tuple(updated), applied

def _weighted_choice(stream, weights: dict[str, float]) -> str:
    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return "transit"
    roll = stream.rng.random() * total
    upto = 0.0
    for key in sorted(weights):
        upto += max(0.0, float(weights[key]))
        if roll <= upto:
            return key
    return "transit"


def _assign_room_roles(rooms: tuple, conn: ConnectivityBuildResult, theme: dict[str, Any], streams: RNGStreams) -> tuple[tuple, dict[str, int], dict[str, Any]]:
    if not rooms:
        return tuple(), {}, {}
    defaults = dungeon_role_defaults()
    start_room_id = rooms[0].room_id
    dist = _distance_by_room(conn, start_room_id)
    max_depth = max(dist.values()) if dist else 0
    deep_sorted = sorted(rooms, key=lambda r: (dist.get(r.room_id, 0), r.room_id))
    boss_room_id = deep_sorted[-1].room_id
    treasury_candidates = [r.room_id for r in deep_sorted[:-1] if dist.get(r.room_id, 0) >= max(1, max_depth - 1)]
    treasury_room_id = treasury_candidates[-1] if treasury_candidates else None
    adj = _room_adjacency(conn)
    stream = streams.stream("gen.theme")
    dynamic_room_ids = [r.room_id for r in rooms if r.room_id not in {start_room_id, boss_room_id} and r.room_id != treasury_room_id]

    role_counts: dict[str, int] = {"entrance": 1, "boss": 1}
    assignments: dict[str, str] = {start_room_id: "entrance", boss_room_id: "boss"}
    if treasury_room_id:
        assignments[treasury_room_id] = "treasury"
        role_counts["treasury"] = 1

    mandatory = dict(defaults.get("mandatory_min") or {})
    conditional = dict(defaults.get("conditional_min") or {})
    target_fracs = dict(defaults.get("target_fractions") or {})
    max_fracs = dict(defaults.get("max_fraction") or {})
    mandatory.pop("entrance", None)
    mandatory.pop("boss", None)
    mandatory.pop("treasury", None)

    quota_roles: list[str] = []
    for role, count in sorted(mandatory.items()):
        quota_roles.extend([str(role)] * max(0, int(count or 0)))
    for role, meta in sorted(conditional.items()):
        if len(rooms) >= int((meta or {}).get("min_rooms", 9999) or 9999):
            quota_roles.extend([str(role)] * max(0, int((meta or {}).get("count", 0) or 0)))

    quota_candidates = sorted(dynamic_room_ids, key=lambda rid: (dist.get(rid, 0), len(adj.get(rid, set())), rid), reverse=True)
    for role in quota_roles:
        if not quota_candidates:
            break
        picked = quota_candidates.pop(0)
        assignments[picked] = role
        role_counts[role] = int(role_counts.get(role, 0) or 0) + 1

    max_allowed = {role: max(1, int(round(len(rooms) * float(frac)))) for role, frac in max_fracs.items()}
    preferred_neighbors = {str(k): [str(v) for v in vals] for k, vals in (theme.get("adjacency_bias") or {}).items() if isinstance(vals, list)}
    theme_mods = {str(k): float(v) for k, v in (theme.get("role_weight_modifiers") or {}).items()}

    for room in rooms:
        if room.room_id in assignments:
            continue
        degree = len(adj.get(room.room_id, set()))
        frac = 0.0 if max_depth <= 0 else min(1.0, float(dist.get(room.room_id, 0)) / float(max_depth))
        weights = {"transit": 3.0, "guard": 2.0, "lair": 1.6, "treasure": 1.0, "shrine": 0.7, "hazard": 0.9, "clue": 0.8}
        if degree >= 3:
            weights["transit"] += 1.8
            weights["guard"] += 0.8
        if degree <= 1:
            weights["lair"] += 0.8
            weights["treasure"] += 0.6
            weights["clue"] += 0.4
        if frac >= 0.6:
            weights["guard"] += 0.7
            weights["lair"] += 0.8
            weights["hazard"] += 0.8
            weights["treasure"] += 0.5
            weights["transit"] = max(0.8, weights["transit"] - 0.7)
        if frac <= 0.25:
            weights["transit"] += 1.0
            weights["clue"] += 0.3

        for role, mod in theme_mods.items():
            if role in weights:
                weights[role] += float(mod)

        neighbor_roles = [assignments.get(nb) for nb in sorted(adj.get(room.room_id, set())) if assignments.get(nb)]
        for role, wanted in preferred_neighbors.items():
            if role in weights and any(nb in wanted for nb in neighbor_roles):
                weights[role] += 1.15
        if neighbor_roles:
            if "treasury" in neighbor_roles or "boss" in neighbor_roles:
                weights["guard"] += 1.2
                weights["clue"] += 0.4
            if "hazard" in neighbor_roles:
                weights["lair"] += 0.5
                weights["hazard"] += 0.4

        for role, target_frac in target_fracs.items():
            target = max(1, int(round(len(rooms) * float(target_frac))))
            current = int(role_counts.get(role, 0) or 0)
            if current < target and role in weights:
                weights[role] += 0.75 * (target - current)
            elif current > target and role in weights:
                weights[role] = max(0.2, weights[role] - 0.5 * (current - target))
        for role, cap in max_allowed.items():
            if role in weights and int(role_counts.get(role, 0) or 0) >= int(cap):
                weights[role] = 0.0

        role = _weighted_choice(stream, weights)
        assignments[room.room_id] = role
        role_counts[role] = int(role_counts.get(role, 0) or 0) + 1

    zone_context = _shape_special_zones(rooms, conn, assignments, role_counts, start_room_id, boss_room_id, treasury_room_id)
    gate_context = _shape_light_gating(rooms, conn, assignments, start_room_id, boss_room_id, treasury_room_id, zone_context)
    clue_targets = dict(gate_context.get('clue_targets') or zone_context.get('clue_targets') or {})
    boss_ante = str(zone_context.get('boss_antechamber_id') or '')
    treasury_guards = {str(rid) for rid in (zone_context.get('treasury_guard_ids') or [])}

    out = []
    for room in rooms:
        role = assignments.get(room.room_id, "transit")
        tags = list(room.tags)
        extra_tags = [f"role:{role}", f"theme:{theme['theme_id']}", f"faction:{theme['faction']}"]
        if room.room_id == boss_ante:
            extra_tags.append('zone:boss_ring')
        if room.room_id in treasury_guards:
            extra_tags.append('zone:treasury_ring')
        clue_target = clue_targets.get(room.room_id)
        for tag in gate_context.get("room_tags", {}).get(room.room_id, []):
            if tag not in tags:
                tags.append(tag)
        if clue_target:
            extra_tags.append(f'clue_target:{clue_target}')
        for tag in extra_tags:
            if tag not in tags:
                tags.append(tag)

        note_parts: list[str] = []
        if role == 'boss':
            note_parts.append('Deepest chamber anchoring the floor threat.')
        elif room.room_id == boss_ante:
            note_parts.append('Antechamber guarding the approach to the deepest chamber.')
        elif room.room_id in treasury_guards:
            note_parts.append('Guarded approach to hidden valuables.')
        if clue_target == 'boss':
            note_parts.append(f'Clues here foreshadow the {theme["faction"]} leader deeper within.')
        elif clue_target == 'treasury':
            note_parts.append('Clues here point toward a secured stash deeper on the floor.')
        extra_note = str((gate_context.get('room_notes') or {}).get(room.room_id) or '').strip()
        if extra_note:
            note_parts.append(extra_note)
        notes = ' '.join(note_parts) if note_parts else room.notes

        out.append(type(room)(room_id=room.room_id, bounds=room.bounds, centroid=room.centroid, tags=tuple(tags), depth_hint=room.depth_hint, floor_type=room.floor_type, notes=notes, role=role, theme_id=str(theme["theme_id"]), faction=str(theme["faction"])))
    zone_context.update(gate_context)
    return tuple(out), role_counts, zone_context


@dataclass(frozen=True)
class GenerationArtifacts:
    blueprint: DungeonBlueprint
    report: dict[str, Any]


@dataclass(frozen=True)
class GenerationLevelArtifacts:
    blueprint: DungeonBlueprint
    stocking: dict[str, Any]
    instance_state: dict[str, Any]
    report: dict[str, Any]


def generate_blueprint(
    *,
    level_id: str,
    seed: str,
    width: int,
    height: int,
    depth: int,
    cell_size: int = 1,
    origin: tuple[int, int] = (0, 0),
    max_retries: int = 5,
) -> GenerationArtifacts:
    """Generate P4.9.1 blueprint + generation report."""

    level_id = str(level_id)
    seed = str(seed)
    width = int(width)
    height = int(height)
    depth = int(depth)

    for attempt in range(max_retries + 1):
        attempt_seed = seed if attempt == 0 else f"{seed}:retry{attempt}"
        streams = RNGStreams(attempt_seed)
        geom = streams.stream("gen.geometry")
        conn_rng = streams.stream("gen.connectivity")
        stairs_rng = streams.stream("gen.stairs")
        doors_rng = streams.stream("gen.doors")

        # Rooms
        rooms = generate_rooms_bsp(width=width, height=height, depth=depth, rng=geom)

        # Corridors + base connectivity
        conn = build_connectivity(rooms, rng=conn_rng)

        # Theme + room roles
        theme = _pick_theme(streams)
        rooms, role_counts, zone_context = _assign_room_roles(rooms, conn, theme, streams)

        # Stairs
        stairs = place_stairs(rooms, level_id=level_id, depth=depth, rng=stairs_rng)
        connectivity_with_stairs = inject_stairs_into_connectivity(
            conn.graph, rooms, room_node=conn.room_node, stairs=stairs
        )

        conn2 = ConnectivityBuildResult(
            corridors=conn.corridors,
            graph=connectivity_with_stairs,
            room_node=conn.room_node,
            corridor_node=conn.corridor_node,
        )

        # Doors + secret safety + connectivity weights
        doors_res = place_doors(rooms, conn2, rng=doors_rng, depth=depth)

        shaped_doors, applied_gates = _apply_gate_doors(doors_res.doors, zone_context)

        # Validate
        v = validate_blueprint(rooms=rooms, doors=shaped_doors, stairs=stairs, connectivity=doors_res.connectivity)
        if v.passed:
            rng_streams: Dict[str, str] = {
                "gen.geometry": str(geom.seed),
                "gen.connectivity": str(conn_rng.seed),
                "gen.stairs": str(stairs_rng.seed),
                "gen.doors": str(doors_rng.seed),
                "gen.theme": str(streams.stream("gen.theme").seed),
                "gen.features": "",
            }
            bp = DungeonBlueprint(
                schema_version="P4.9.1",
                level_id=level_id,
                seed=attempt_seed,
                rng_streams=rng_streams,
                width=width,
                height=height,
                cell_size=int(cell_size),
                origin=Point(int(origin[0]), int(origin[1])),
                rooms=rooms,
                corridors=conn.corridors,
                doors=shaped_doors,
                stairs=stairs,
                features=tuple(),
                connectivity=doors_res.connectivity,
                context={
                    "theme_id": str(theme["theme_id"]),
                    "theme_name": str(theme["name"]),
                    "faction": str(theme["faction"]),
                    "secondary_faction": str(theme.get("secondary_faction") or ""),
                    "monster_keywords": list(theme.get("monster_keywords", [])),
                    "hazards": list(theme.get("hazards", [])),
                    "shrines": list(theme.get("shrines", [])),
                    "features": list(theme.get("features", [])),
                    "special_packs": dict(theme.get("special_packs") or {}),
                    "role_counts": role_counts,
                    "boss_path_room_ids": list(zone_context.get("boss_path_room_ids") or []),
                    "treasury_path_room_ids": list(zone_context.get("treasury_path_room_ids") or []),
                    "boss_antechamber_id": str(zone_context.get("boss_antechamber_id") or ""),
                    "treasury_guard_ids": list(zone_context.get("treasury_guard_ids") or []),
                    "clue_targets": dict(zone_context.get("clue_targets") or {}),
                    "gates": dict(applied_gates or {}),
                    "features": list(theme.get("features", [])),
                    "special_packs": dict(theme.get("special_packs") or {}),
                },
            )

            report = {
                "schema_version": "P4.9.1",
                "level_id": level_id,
                "seed": attempt_seed,
                "content_hash": stable_hash_dict({"schemas": "P4.9.1"}),
                "rng_streams": rng_streams,
                "parameters": {
                    "width": width,
                    "height": height,
                    "depth": depth,
                    "cell_size": int(cell_size),
                },
                "roll_log": doors_res.roll_log,
                "validation": v.to_dict(),
                "summary": {
                    "rooms": len(rooms),
                    "doors": len(doors_res.doors),
                    "stairs": len(stairs),
                    "features": 0,
                    "loops_added": int(doors_res.connectivity.loops_added),
                    "theme_id": str(theme["theme_id"]),
                    "faction": str(theme["faction"]),
                    "role_counts": role_counts,
                    "boss_path_room_ids": list(zone_context.get("boss_path_room_ids") or []),
                    "treasury_path_room_ids": list(zone_context.get("treasury_path_room_ids") or []),
                    "boss_antechamber_id": str(zone_context.get("boss_antechamber_id") or ""),
                    "treasury_guard_ids": list(zone_context.get("treasury_guard_ids") or []),
                    "clue_targets": dict(zone_context.get("clue_targets") or {}),
                    "gates": dict(applied_gates or {}),
                    "features": list(theme.get("features", [])),
                    "special_packs": dict(theme.get("special_packs") or {}),
                },
            }
            return GenerationArtifacts(blueprint=bp, report=report)

    # If we get here, last attempt failed; return last artifacts with failed validation.
    # (Callers can decide how to handle.)
    return GenerationArtifacts(
        blueprint=DungeonBlueprint(
            schema_version="P4.9.1",
            level_id=level_id,
            seed=seed,
            rng_streams={"gen.features": ""},
            width=width,
            height=height,
            cell_size=int(cell_size),
            origin=Point(int(origin[0]), int(origin[1])),
            rooms=tuple(),
            corridors=tuple(),
            doors=tuple(),
            stairs=tuple(),
            features=tuple(),
            connectivity=ConnectivityGraph(nodes=tuple(), edges=tuple(), loops_added=0),
        ),
        report={
            "schema_version": "P4.9.1",
            "level_id": level_id,
            "seed": seed,
            "content_hash": stable_hash_dict({"schemas": "P4.9.1"}),
            "rng_streams": {},
            "parameters": {"width": width, "height": height, "depth": depth},
            "roll_log": [],
            "validation": {"passed": False, "checks": [{"check_id": "generation_failed", "passed": False, "details": {}}]},
            "summary": {"rooms": 0, "doors": 0, "stairs": 0, "features": 0, "loops_added": 0},
        },
    )


def generate_level(
    *,
    level_id: str,
    seed: str,
    width: int,
    height: int,
    depth: int,
    cell_size: int = 1,
    origin: tuple[int, int] = (0, 0),
    max_retries: int = 5,
) -> GenerationLevelArtifacts:
    """Generate P4.9.2.6: blueprint + stocking + unified generation report."""

    bp_art = generate_blueprint(
        level_id=level_id,
        seed=seed,
        width=width,
        height=height,
        depth=depth,
        cell_size=cell_size,
        origin=origin,
        max_retries=max_retries,
    )

    # Stocking stream is isolated from geometry.
    streams = RNGStreams(str(bp_art.blueprint.seed))
    stock_rng = streams.stream("gen.stocking")
    stock = generate_stocking(blueprint=bp_art.blueprint, seed=str(bp_art.blueprint.seed), depth=depth, rng=stock_rng)

    merged_roll_log: list[dict[str, Any]] = []
    merged_roll_log.extend(list(bp_art.report.get("roll_log", [])))
    merged_roll_log.extend(list(stock.roll_log))

    rng_streams = dict(bp_art.blueprint.rng_streams)
    rng_streams["gen.stocking"] = str(stock_rng.seed)

    report = dict(bp_art.report)
    report["schema_version"] = "P4.9.2.6.5"
    report["content_hash"] = stable_hash_dict({"schemas": "P4.9.2.6.5", "stocking": stock.content_hash})
    report["rng_streams"] = rng_streams
    report["roll_log"] = merged_roll_log

    # --- P4.9.2.6: content validation summary ---
    # Derive counts from the merged roll log so it stays deterministic and
    # doesn't require additional mutable state.
    cv_counts: dict[str, int] = {
        "stub_refs": 0,
        "missing_monster": 0,
        "missing_damage_profile": 0,
        "missing_ai_profile": 0,
        "missing_specials": 0,
    }
    for e in merged_roll_log:
        step = str(e.get("step", ""))
        if step == "content.stub":
            cv_counts["stub_refs"] += 1
        elif step == "content.missing_monster":
            cv_counts["missing_monster"] += 1
        elif step == "content.missing_damage_profile":
            cv_counts["missing_damage_profile"] += 1
        elif step == "content.missing_ai_profile":
            cv_counts["missing_ai_profile"] += 1
        elif step == "content.missing_specials":
            cv_counts["missing_specials"] += 1
    cv_counts["total_issues"] = sum(v for k, v in cv_counts.items() if k != "total_issues")

    summary = dict(report.get("summary", {}) or {})
    summary["content_validation"] = cv_counts
    report["summary"] = summary

    # Deterministic encounter resolution invariants
    params = dict(report.get("parameters", {}) or {})
    params["cl_lists_hash"] = str(stock.cl_lists_hash)
    report["parameters"] = params

    # --- P4.9.2.6.5: mirror resolved variant spawns into stocking for inspection ---
    resolved_spawns, inst_rng_streams = resolve_variant_spawns(stocking=stock.stocking, seed=str(bp_art.blueprint.seed))
    if resolved_spawns:
        stock.stocking["resolved_variants"] = resolved_spawns

    inst = initialize_instance_state(
        blueprint=bp_art.blueprint,
        stocking=stock.stocking,
        seed=str(bp_art.blueprint.seed),
        resolved_spawns=resolved_spawns,
        rng_streams=inst_rng_streams,
    )

    return GenerationLevelArtifacts(
        blueprint=bp_art.blueprint,
        stocking=stock.stocking,
        instance_state=inst,
        report=report,
    )


def write_artifacts(artifacts: GenerationArtifacts, *, out_dir: str | Path, basename: str) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bp_path = out / f"{basename}_blueprint.json"
    rep_path = out / f"{basename}_generation_report.json"
    bp_path.write_text(json.dumps(artifacts.blueprint.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rep_path.write_text(json.dumps(artifacts.report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return bp_path, rep_path


def write_level_artifacts(
    artifacts: GenerationLevelArtifacts, *, out_dir: str | Path, basename: str
) -> tuple[Path, Path, Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bp_path = out / f"{basename}_blueprint.json"
    st_path = out / f"{basename}_stocking.json"
    is_path = out / f"{basename}_instance_state.json"
    rep_path = out / f"{basename}_generation_report.json"
    bp_path.write_text(json.dumps(artifacts.blueprint.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    st_path.write_text(json.dumps(artifacts.stocking, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    is_path.write_text(json.dumps(artifacts.instance_state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rep_path.write_text(json.dumps(artifacts.report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return bp_path, st_path, is_path, rep_path
