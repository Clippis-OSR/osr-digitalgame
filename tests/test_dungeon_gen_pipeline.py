from sww.dungeon_gen.blueprint import stable_hash_dict
from sww.dungeon_gen.pipeline import (
    run_canonical_blueprint_stage,
    run_canonical_level_pipeline,
    validate_canonical_blueprint,
)


def test_canonical_blueprint_stage_validates_connectivity_and_basic_invariants():
    art = run_canonical_blueprint_stage(level_id="level_1", seed="4242", width=60, height=45, depth=1)
    v = validate_canonical_blueprint(art.blueprint)

    assert v.passed is True
    by_id = {str(c.get("check_id")): bool(c.get("passed")) for c in (v.checks or [])}
    assert by_id.get("all_rooms_reachable") is True
    assert by_id.get("room_count_min3") is True
    assert by_id.get("stairs_present") is True


def test_canonical_level_pipeline_is_deterministic_for_same_seed():
    a = run_canonical_level_pipeline(level_id="level_1", seed="9901", width=60, height=45, depth=1)
    b = run_canonical_level_pipeline(level_id="level_1", seed="9901", width=60, height=45, depth=1)

    assert a.blueprint.sha256() == b.blueprint.sha256()
    assert stable_hash_dict(a.stocking) == stable_hash_dict(b.stocking)
    assert stable_hash_dict(a.instance_state) == stable_hash_dict(b.instance_state)
