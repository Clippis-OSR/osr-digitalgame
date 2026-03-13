from types import SimpleNamespace

from sww.game import Game


class _StubCombatService:
    def __init__(self):
        self.calls = []

    def cleanup_pool(self, *, ctx=None):
        self.calls.append(("cleanup_pool", dict(ctx or {})))
        return ["ok"]

    def process_removal(self, actor, *, reason, marker="", remove_effects=(), ctx=None):
        self.calls.append(("process_removal", actor, reason, marker, tuple(remove_effects), dict(ctx or {})))

    def leave_combat_actor(self, actor, *, marker="fled", remove_effects=()):
        self.calls.append(("leave_combat_actor", actor, marker, tuple(remove_effects)))

    def notify_death(self, target, *, ctx=None):
        self.calls.append(("notify_death", target, dict(ctx or {})))


def test_game_combat_methods_delegate_to_combat_service():
    g = Game.__new__(Game)
    stub = _StubCombatService()
    g.combat_service = stub

    actor = SimpleNamespace(name="Bandit", hp=0)

    assert g._combat_cleanup_pool(ctx={"combat_foes": []}) == ["ok"]
    g._process_combat_removal(actor, reason="death", marker="", remove_effects=(), ctx={"combat_foes": []})
    g._leave_combat_actor(actor, marker="fled", remove_effects=("flee_pending",))
    g._notify_death(actor, ctx={"combat_foes": []})

    kinds = [c[0] for c in stub.calls]
    assert kinds == ["cleanup_pool", "process_removal", "leave_combat_actor", "notify_death"]
