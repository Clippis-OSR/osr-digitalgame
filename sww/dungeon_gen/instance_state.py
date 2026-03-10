from __future__ import annotations

"""Dungeon instance-state initialization (P4.9.2.6.4).

Creates a mutable save-game delta layer on top of blueprint+stocking.

Key addition in P4.9.2.6.4:
  - resolved_spawns: deterministic resolution payloads for variant/derived-CL monsters
    (e.g., dragons), so replays remain stable.
"""

from typing import Any, Dict, List

from .blueprint import DungeonBlueprint, stable_hash_dict
from .rng_streams import RNGStreams
from ..data import load_json
from ..monster_variants import resolve_variant_monster


def resolve_variant_spawns(
    *,
    stocking: dict[str, Any],
    seed: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Resolve any variant/derived-CL monster refs into concrete deterministic payloads.

    Returns (resolved_spawns, rng_streams).

    This is used both for the instance_state artifact (authoritative for replay) and
    optionally mirrored into the stocking artifact as a human-inspection convenience.
    """

    monsters: list[dict[str, Any]] = load_json("monsters.json")
    by_id: dict[str, dict[str, Any]] = {m.get("monster_id"): m for m in monsters if m.get("monster_id")}

    streams = RNGStreams(str(seed))
    rng = streams.stream("gen.encounter_instances")

    resolved_spawns: list[dict[str, Any]] = []
    spawn_idx = 0

    room_contents: dict[str, Any] = dict(stocking.get("room_contents") or {})
    for room_id in sorted(room_contents.keys()):
        rc = room_contents.get(room_id) or {}
        contents = (rc.get("contents") or {})
        monsters_list = list((contents.get("monsters") or []))
        for mref in monsters_list:
            monster_id = str(mref.get("monster_id") or "").strip()
            if not monster_id:
                continue
            mdef = by_id.get(monster_id)
            if not mdef:
                continue
            resolved = resolve_variant_monster(mdef, rng=rng, desired_cl=int(mref.get('target_cl')) if str(mref.get('target_cl','')).strip().replace('+','').isdigit() else None)
            if resolved is None:
                continue
            resolved_spawns.append(
                {
                    "spawn_id": f"S{spawn_idx:04d}",
                    "room_id": room_id,
                    "monster_id": monster_id,
                    "count": int(mref.get("count", 1) or 1),
                    "resolved": resolved,
                    "source_tags": list(mref.get("tags") or []),
                }
            )
            spawn_idx += 1

    return resolved_spawns, {"gen.encounter_instances": str(rng.seed)}


def initialize_instance_state(
    *,
    blueprint: DungeonBlueprint,
    stocking: dict[str, Any],
    seed: str,
    resolved_spawns: list[dict[str, Any]] | None = None,
    rng_streams: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a fresh dungeon_instance_state.json."""

    if resolved_spawns is None or rng_streams is None:
        resolved_spawns2, rng_streams2 = resolve_variant_spawns(stocking=stocking, seed=seed)
        if resolved_spawns is None:
            resolved_spawns = resolved_spawns2
        if rng_streams is None:
            rng_streams = rng_streams2

    inst = {
        "schema_version": "P4.9.2.6.5",
        "level_id": blueprint.level_id,
        "base_blueprint_hash": blueprint.sha256(),
        "base_stocking_hash": stable_hash_dict(stocking),
        "turn_counter": 0,
        "explored": {"cells_seen": [], "rooms_seen": []},
        "doors": {d.door_id: {"state": "closed"} for d in blueprint.doors},
        "rooms": {r.room_id: {"cleared": False, "looted": False, "alertness_override": None} for r in blueprint.rooms},
        "factions": {},
        "entities": [],
        "resolved_spawns": resolved_spawns,
        "rng_streams": rng_streams,
    }
    return inst
