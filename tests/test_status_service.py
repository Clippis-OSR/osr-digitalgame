from types import SimpleNamespace

from sww.status_service import StatusService
from sww.status_lifecycle import cleanup_actor_battle_status


def _actor():
    return SimpleNamespace(status={})


def test_apply_status_replace_and_stack_modes():
    a = _actor()

    assert StatusService.apply_status(a, "asleep", 2)
    assert a.status["asleep"] == 2
    assert StatusService.apply_status(a, "asleep", 6, mode="max")
    assert a.status["asleep"] == 6
    assert StatusService.apply_status(a, "burning", 1, mode="stack_int")
    assert StatusService.apply_status(a, "burning", 2, mode="stack_int")
    assert a.status["burning"] == 3


def test_expire_timed_statuses_keeps_casting_at_round_start():
    a = _actor()
    a.status = {
        "asleep": 1,
        "casting": {"rounds": 1, "spell": "sleep"},
        "bless": {"rounds": 1, "to_hit": 1},
    }

    StatusService.expire_timed_statuses([a], phase="start")

    assert "asleep" not in a.status
    assert "bless" not in a.status
    assert "casting" in a.status


def test_cleanup_on_death_or_exit_clears_transient_statuses():
    a = _actor()
    a.status = {
        "casting": {"rounds": 1},
        "cover": -4,
        "parry": {"rounds": 1, "penalty": 2},
        "flee_pending": 1,
        "asleep": 2,
    }

    cleanup_actor_battle_status(a)

    assert "casting" not in a.status
    assert "cover" not in a.status
    assert "parry" not in a.status
    assert "flee_pending" not in a.status
    assert a.status.get("asleep") == 2


def test_cleanup_hooks_run_once_no_duplicate_cleanup():
    calls: list[tuple[object, str]] = []

    def hook(actor, reason):
        calls.append((actor, reason))

    a = _actor()
    a.status_cleanup_hooks = [hook]
    a.status = {"cover": -2}

    StatusService.cleanup_on_death_or_exit(a)
    StatusService.cleanup_on_death_or_exit(a)

    assert len(calls) == 1
    assert calls[0][1] == "death_or_exit"
