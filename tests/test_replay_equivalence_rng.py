from pathlib import Path

from sww.replay_equivalence import run_equivalence_fixture_dir


def test_save_load_replay_equivalence_fixture_rng_continuity() -> None:
    fx = Path('tests/fixtures/equivalence/save_load_replay_equivalence')
    res = run_equivalence_fixture_dir(fx)
    assert res.ok, res.message
    assert res.rng_equal, res.message
    assert res.snapshot_equal, res.message
