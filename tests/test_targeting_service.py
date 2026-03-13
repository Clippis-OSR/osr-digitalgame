from types import SimpleNamespace

from sww.grid_map import GridMap
from sww.targeting_service import TargetingService
from sww.spellcasting import apply_spell_in_combat


class _DummyUI:
    def __init__(self):
        self.logs: list[str] = []

    def choose(self, prompt, options):
        return 0

    def log(self, msg):
        self.logs.append(str(msg))


class _DummyDice:
    def d(self, _n):
        return 3


class _DummyGame:
    def __init__(self):
        self.ui = _DummyUI()
        self.dice = _DummyDice()
        self.party = SimpleNamespace(living=lambda: [])


def _actor(name: str, hp: int, *, effects=None):
    return SimpleNamespace(name=name, hp=hp, hp_max=max(1, hp), effects=list(effects or []), status={})


def test_theater_target_legality_and_existence():
    live = _actor("Bandit", 4)
    fled = _actor("Runner", 2, effects=["fled"])
    down = _actor("Corpse", 0)
    enemies = [live, fled, down]

    assert TargetingService.theater_target_is_valid(live, enemies)
    assert not TargetingService.theater_target_is_valid(fled, enemies)
    assert not TargetingService.theater_target_is_valid(down, enemies)
    assert TargetingService.legal_target_exists(enemies=enemies)


def test_grid_melee_and_missile_legality_unchanged():
    gm = GridMap.empty(5, 5)
    living = {
        "pc1": ("pc", (1, 1)),
        "pc2": ("pc", (1, 2)),
        "foe_adj": ("foe", (2, 1)),
        "foe_far": ("foe", (4, 4)),
    }

    melee = TargetingService.melee_target_ids(
        attacker_id="pc1",
        attacker_pos=(1, 1),
        attacker_side="pc",
        living=living,
    )
    assert melee == {"foe_adj"}

    missile = TargetingService.missile_target_ids(
        gm=gm,
        attacker_id="pc1",
        attacker_pos=(1, 1),
        attacker_side="pc",
        living=living,
    )
    assert missile == {"foe_adj", "foe_far"}


def test_spell_target_legality_tracks_existing_grid_rules():
    gm = GridMap.empty(7, 7)
    assert TargetingService.spell_target_is_legal(gm=gm, caster_pos=(1, 1), target_pos=(5, 1), spell_name="Magic Missile")
    gm.set(3, 1, "wall")
    assert not TargetingService.spell_target_is_legal(gm=gm, caster_pos=(1, 1), target_pos=(5, 1), spell_name="Magic Missile")


def test_spell_combat_targeting_keeps_no_target_behavior_for_fled_foes():
    game = _DummyGame()
    caster = _actor("Mage", 5)
    fled_foe = _actor("Bandit", 3, effects=["fled"])

    result = apply_spell_in_combat(game=game, caster=caster, spell_name="Magic Missile", foes=[fled_foe])

    assert result is None
    assert any("No target." in msg for msg in game.ui.logs)
