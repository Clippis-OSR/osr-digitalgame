from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from sww.game import Game
from sww.models import Actor, Party, Stats
from sww.ui_headless import HeadlessUI

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / 'tests' / 'fixtures' / 'combat_spell_coherence'


def mk_pc(name: str, *, hp: int = 6, hp_max: int = 10, spells: list[str] | None = None) -> Actor:
    a = Actor(name=name, hp=hp, hp_max=hp_max, ac_desc=7, hd=1, save=14, is_pc=True, weapon='staff', spells_prepared=list(spells or []))
    a.stats = Stats(STR=10, DEX=10, CON=10, INT=12, WIS=12, CHA=10)
    a.cls = 'Cleric'
    return a


def mk_foe(name: str, *, hp: int = 3, hp_max: int = 8, spells: list[str] | None = None, effects: list[str] | None = None) -> Actor:
    a = Actor(name=name, hp=hp, hp_max=hp_max, ac_desc=7, hd=1, save=14, is_pc=False, monster_id='orc_shaman', morale=8, weapon='staff', spells_prepared=list(spells or []), effects=list(effects or []))
    a.stats = Stats(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=8)
    a.cls = 'Cleric'
    return a


def _mods(side_override: str | None = None):
    return SimpleNamespace(side_override=side_override, can_act=True, forced_retreat=False, blocks_magical_heal=False)


def _install_mods(game: Game, overrides: dict[int, str | None]):
    def query_modifiers(actor):
        return _mods(overrides.get(id(actor)))
    game.effects_mgr.query_modifiers = query_modifiers


def _battle_kinds(game: Game) -> list[str]:
    out: list[str] = []
    for ev in game.events.events:
        if ev.name != 'battle_event':
            continue
        payload = (ev.data or {}).get('data')
        kind = None
        if isinstance(payload, dict):
            kind = payload.get('kind')
        else:
            kind = getattr(payload, 'kind', None)
        if kind:
            out.append(str(kind))
    return out


def run_fixture(path: Path) -> tuple[bool, str]:
    fx = json.loads(path.read_text(encoding='utf-8'))
    ui = HeadlessUI()
    game = Game(ui, dice_seed=int(fx.get('dice_seed', 12345)), wilderness_seed=999)

    hero = mk_pc('Hero', hp=4, hp_max=10)
    ally = mk_pc('Acolyte', hp=5, hp_max=10)
    foe_caster = mk_foe('Orc Shaman', hp=2, hp_max=8, spells=[str(fx.get('spell') or 'cure light wounds')], effects=list(fx.get('caster_effects') or []))
    foe_other = mk_foe('Orc Guard', hp=6, hp_max=8)
    game.party = Party(members=[hero, ally])

    overrides: dict[int, str | None] = {}
    caster_side = str(fx.get('caster_side') or 'foe')
    side_override = fx.get('side_override')
    if caster_side == 'party':
        caster = mk_pc('Priest', hp=2, hp_max=8, spells=[str(fx.get('spell') or 'cure light wounds')])
        caster.effects = list(fx.get('caster_effects') or [])
        game.party.members.append(caster)
        party = [hero, ally, caster]
        foes = [foe_caster, foe_other]
    else:
        caster = foe_caster
        party = [hero, ally]
        foes = [foe_caster, foe_other]
    if side_override is not None:
        overrides[id(caster)] = str(side_override)
    _install_mods(game, overrides)

    entry = {'caster': caster, 'spell': str(fx.get('spell') or 'cure light wounds'), 'side': caster_side}
    kind = str(fx.get('kind') or '')

    caster.status['casting'] = {'rounds': 1, 'spell': entry['spell'], 'disrupted': False}
    coherent = game.debug_cohere_pending_spell_entry(entry, party, foes)
    if kind == 'drop_invalid':
        if coherent is not None:
            return False, f'{path.name}: expected pending spell to drop, got {coherent!r}'
        return True, f'{path.name}: dropped invalid pending spell for fled caster'

    if coherent is None:
        return False, f'{path.name}: coherent entry unexpectedly missing'

    expected_healed = str(fx.get('expect_healed') or '')
    expect_party_unchanged = str(fx.get('expect_party_unchanged') or '')
    pre = {a.name: a.hp for a in [hero, ally, foe_caster, foe_other]}
    game._resolve_pending_spells([coherent], list(coherent.get('foes') or []), allies=list(coherent.get('allies') or []))
    post = {a.name: a.hp for a in [hero, ally, foe_caster, foe_other]}
    healed = [name for name, hp in post.items() if hp > pre[name]]
    if expected_healed not in healed:
        return False, f'{path.name}: expected {expected_healed} to be healed, got healed={healed} pre={pre} post={post}'
    if expect_party_unchanged and post.get(expect_party_unchanged) != pre.get(expect_party_unchanged):
        return False, f'{path.name}: party target {expect_party_unchanged} changed unexpectedly pre={pre} post={post}'
    kinds = _battle_kinds(game)
    if fx.get('side_override') is not None and 'PENDING_SPELL_SIDE_COHERED' not in kinds:
        return False, f'{path.name}: missing PENDING_SPELL_SIDE_COHERED event'
    return True, f'{path.name}: healed={healed} kinds={kinds}'


def main(argv: list[str]) -> int:
    paths = sorted(FIXTURE_DIR.glob('*.json'))
    if not paths:
        print('No combat spell coherence fixtures found.')
        return 1
    ok = True
    for path in paths:
        passed, msg = run_fixture(path)
        print(('OK   ' if passed else 'FAIL ') + msg)
        ok = ok and passed
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
