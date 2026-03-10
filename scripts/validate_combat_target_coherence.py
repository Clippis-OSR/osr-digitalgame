from __future__ import annotations

import json
import sys
from pathlib import Path

from sww.game import Game
from sww.models import Actor, Stats
from sww.ui_headless import HeadlessUI

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / 'tests' / 'fixtures' / 'combat_target_coherence'


def mk_pc(name: str, weapon: str) -> Actor:
    a = Actor(
        name=name,
        hp=10,
        hp_max=10,
        ac_desc=7,
        hd=1,
        save=14,
        is_pc=True,
        weapon=weapon,
    )
    a.stats = Stats(STR=13, DEX=13, CON=10, INT=10, WIS=10, CHA=10)
    a.cls = 'Fighter'
    return a


def mk_foe(name: str, *, hp: int = 4, effects: list[str] | None = None) -> Actor:
    a = Actor(
        name=name,
        hp=hp,
        hp_max=max(hp, 1),
        ac_desc=7,
        hd=1,
        save=14,
        is_pc=False,
        monster_id='bandit',
        morale=8,
        weapon='club',
        effects=list(effects or []),
    )
    a.stats = Stats(STR=10, DEX=10, CON=10, INT=8, WIS=8, CHA=8)
    return a


def _battle_kinds(game: Game) -> list[str]:
    out: list[str] = []
    for ev in game.events.events:
        if ev.name != 'battle_event':
            continue
        payload = (ev.data or {}).get('data')
        if payload is None:
            continue
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

    attacker = mk_pc('Hero', 'bow' if fx.get('action_type') == 'missile' else 'sword')
    stale_state = str(fx.get('stale_target_state') or 'dead')
    if stale_state == 'fled':
        stale = mk_foe('Bandit A', hp=4, effects=['fled'])
    else:
        stale = mk_foe('Bandit A', hp=0)
    enemies = [mk_foe(str(name)) for name in (fx.get('available_targets') or [])]

    plan = {id(attacker): {'type': str(fx.get('action_type') or 'melee'), 'actor': attacker, 'target': stale}}
    game._combat_cohere_targets_for_side(plan, [attacker], enemies, reason=path.stem)
    result = plan[id(attacker)]
    kinds = _battle_kinds(game)

    expect = str(fx.get('expect') or '')
    if expect == 'retarget':
        tgt = result.get('target')
        if result.get('type') not in {'melee', 'missile'}:
            return False, f'{path.name}: expected retained attack plan, got {result!r}'
        if tgt is stale or tgt is None:
            return False, f'{path.name}: target was not replaced'
        if not any(tgt is e for e in enemies):
            return False, f'{path.name}: retarget did not land on a living enemy'
        if 'ROUND_RETARGET' not in kinds:
            return False, f'{path.name}: missing ROUND_RETARGET event'
        return True, f'{path.name}: retargeted to {getattr(tgt, "name", "?")}'

    if expect == 'clear':
        if result.get('type') != 'none':
            return False, f'{path.name}: expected action to clear, got {result!r}'
        if 'STALE_TARGET_INVALIDATED' not in kinds:
            return False, f'{path.name}: missing STALE_TARGET_INVALIDATED event'
        return True, f'{path.name}: cleared stale target with no enemies left'

    return False, f'{path.name}: unknown expectation {expect!r}'


def main(argv: list[str]) -> int:
    paths = sorted(FIXTURE_DIR.glob('*.json'))
    if not paths:
        print('No combat target coherence fixtures found.')
        return 1
    ok = True
    for path in paths:
        passed, msg = run_fixture(path)
        print(('OK   ' if passed else 'FAIL ') + msg)
        ok = ok and passed
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
