from types import SimpleNamespace

from sww.game import Game


class _EffectsMgr:
    def __init__(self, calls):
        self.calls = calls

    def cleanup_on_death(self, actor, *, pool):
        self.calls.append(("cleanup_on_death", getattr(actor, "name", "?"), tuple(getattr(a, "name", "?") for a in pool)))


def _make_game(calls):
    g = Game.__new__(Game)
    g.party = SimpleNamespace(members=[])
    g.dungeon_rooms = {}
    g.current_room_id = 0
    g.emit = lambda event_name, **data: calls.append(("emit", event_name, data.get("name")))
    g.effects_mgr = _EffectsMgr(calls)
    return g


def _actor(name: str, hp: int, *, effects=None, status=None):
    return SimpleNamespace(name=name, hp=hp, monster_id="m1", effects=list(effects or []), status=dict(status or {}))


def test_dead_actor_cleanup_runs_once():
    calls = []
    g = _make_game(calls)
    dead = _actor("Bandit", 0, status={"cover": -2, "casting": {"rounds": 1}})
    ally = _actor("A", 3)
    foe = _actor("F", 2)
    g.party.members = [ally]

    g._notify_death(dead, ctx={"combat_foes": [foe]})
    g._notify_death(dead, ctx={"combat_foes": [foe]})

    assert "cover" not in dead.status
    assert "casting" not in dead.status
    assert [c for c in calls if c[0] == "emit"] == [("emit", "actor_died", "Bandit")]
    assert len([c for c in calls if c[0] == "cleanup_on_death"]) == 1


def test_living_actor_leave_cleanup_does_not_double_run():
    calls = []
    g = _make_game(calls)
    actor = _actor("Runner", 4, effects=["flee_pending"], status={"cover": -4, "parry": {"rounds": 1}})

    g._leave_combat_actor(actor, marker="fled", remove_effects=("flee_pending",))
    g._leave_combat_actor(actor, marker="fled", remove_effects=("flee_pending",))

    assert actor.effects.count("fled") == 1
    assert "flee_pending" not in actor.effects
    assert "cover" not in actor.status
    assert "parry" not in actor.status
    # Non-death leave should not emit actor_died or death cleanup hooks.
    assert not [c for c in calls if c[0] == "emit"]
    assert not [c for c in calls if c[0] == "cleanup_on_death"]


def test_death_event_order_is_deterministic():
    calls = []
    g = _make_game(calls)
    dead = _actor("Ghoul", -1)
    g.party.members = [_actor("Cleric", 5)]

    g._notify_death(dead, ctx={"combat_foes": [_actor("Orc", 2)]})

    kinds = [c[0] for c in calls]
    assert kinds == ["emit", "cleanup_on_death"]
