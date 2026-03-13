import hashlib
import json

from sww.dungeon_instance import DungeonInstance, DungeonDelta, ensure_instance


def _stable_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def test_ensure_instance_is_deterministic_for_fixed_seed():
    a = ensure_instance(None, "town_dungeon_01", 1337)
    b = ensure_instance(None, "town_dungeon_01", 1337)

    assert _stable_hash(a.blueprint.data) == _stable_hash(b.blueprint.data)
    assert _stable_hash(a.stocking.data) == _stable_hash(b.stocking.data)


def test_ensure_instance_produces_valid_legacy_runtime_shape():
    inst = ensure_instance(None, "town_dungeon_01", 2024)
    bp = inst.blueprint.data

    rooms = bp.get("rooms") or {}
    edges = bp.get("edges") or []
    assert rooms
    assert edges
    assert int(bp.get("start_room_id") or 0) in {int(k) for k in rooms.keys()}

    valid_ids = {int(k) for k in rooms.keys()}
    for e in edges:
        assert int(e.get("a") or 0) in valid_ids
        assert int(e.get("b") or 0) in valid_ids


def test_dungeon_instance_roundtrip_preserves_blueprint_and_delta():
    inst = ensure_instance(None, "town_dungeon_01", 404)
    rid = sorted(int(k) for k in (inst.blueprint.data.get("rooms") or {}).keys())[0]
    inst.delta = DungeonDelta(data={"version": 1, "rooms": {str(rid): {"cleared": True, "doors": {"n": "open"}}}})

    loaded = DungeonInstance.from_dict(inst.to_dict())
    assert loaded.blueprint.data == inst.blueprint.data
    assert loaded.stocking.data == inst.stocking.data
    assert loaded.delta.data == inst.delta.data


def test_legacy_adapter_multifloor_stairs_invariants_are_stable():
    inst_a = ensure_instance(None, "town_dungeon_01", 333)
    inst_b = ensure_instance(None, "town_dungeon_01", 333)

    bp_a = inst_a.blueprint.data
    bp_b = inst_b.blueprint.data
    assert _stable_hash(bp_a) == _stable_hash(bp_b)

    floors = int(bp_a.get("floors") or 1)
    assert floors > 1

    rooms = bp_a.get("rooms") or {}
    floor0_stairs_down = []
    floor1_stairs_up = []
    for rid, rec in rooms.items():
        if not isinstance(rec, dict):
            continue
        tags = {str(t).lower() for t in (rec.get("tags") or [])}
        floor = int(rec.get("floor", 0) or 0)
        if floor == 0 and "stairs_down" in tags:
            floor0_stairs_down.append(int(rid))
        if floor == 1 and "stairs_up" in tags:
            floor1_stairs_up.append(int(rid))

    assert floor0_stairs_down, "Expected at least one floor-0 room tagged stairs_down"
    assert floor1_stairs_up, "Expected at least one floor-1 room tagged stairs_up"
