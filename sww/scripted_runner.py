from __future__ import annotations

from typing import Any, Dict, Tuple

from .wilderness import neighbors, hex_distance
from .commands import TravelToHex, ReturnToTown, InvestigateCurrentLocation


class ScriptedRunFailed(RuntimeError):
    pass


def step_toward(cur: Tuple[int, int], tgt: Tuple[int, int]) -> Tuple[int, int]:
    """Choose the adjacent hex that reduces distance to target the most."""
    neigh = neighbors(cur)
    best = None
    best_dist = 10**9
    for _d, coord in neigh.items():
        dist = hex_distance(coord, tgt)
        if dist < best_dist:
            best_dist = dist
            best = coord
    return best if best is not None else cur


def run_contract_navigation(game, max_steps: int = 80) -> None:
    """Drive wilderness travel toward the first accepted contract target and return to town.

    This is a *non-interactive* scripted driver meant for QA.
    It avoids UI loops while still exercising the same core mechanics:
    - hex generation
    - watch advancement
    - ration consumption
    - POI investigation
    - contract progress updates
    """

    active = [c for c in (game.active_contracts or []) if c.get('status') == 'accepted']
    if not active:
        raise ScriptedRunFailed('No accepted contracts to run.')

    c = active[0]
    th = c.get('target_hex') or [0, 0]
    tgt = (int(th[0]), int(th[1]))

    # Disable encounter noise for QA navigation runs if caller hasn't already.
    if hasattr(game, '_wilderness_encounter_check'):
        try:
            game._wilderness_encounter_check = lambda hx, encounter_mod=0: None
        except Exception:
            pass

    # Ensure starting hex exists
    game.party_hex = tuple(getattr(game, 'party_hex', (0, 0)))
    game._ensure_current_hex()

    for _ in range(max_steps):
        cur = tuple(game.party_hex)
        hx: Dict[str, Any] = game._ensure_current_hex()

        # Update contract progress on entering hex
        try:
            game._update_contract_progress_on_hex(int(cur[0]), int(cur[1]), hx)
        except Exception:
            pass

        if cur == tgt:
            # If POI-targeted contract, investigate once.
            poi = hx.get('poi') if isinstance(hx, dict) else None
            if poi and c.get('target_poi_type') and not c.get('completed'):
                try:
                    game.dispatch(InvestigateCurrentLocation())
                except Exception:
                    pass
                # Refresh contract state after investigation
                active2 = [x for x in (game.active_contracts or []) if x.get('cid') == c.get('cid')]
                if active2:
                    c = active2[0]

            # Return to town (safe)
            game.dispatch(ReturnToTown())
            try:
                game._check_contracts_on_town_arrival()
            except Exception:
                pass
            return

        # Travel one hex toward target
        nxt = step_toward(cur, tgt)
        game.dispatch(TravelToHex(int(nxt[0]), int(nxt[1])))
        # Ensure the new hex exists
        game._ensure_current_hex()

    raise ScriptedRunFailed(f'Failed to reach target {tgt} within {max_steps} steps.')
