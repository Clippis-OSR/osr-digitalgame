\
import random
import re
from dataclasses import dataclass

_DICE_RE = re.compile(r"^\s*(\d*)d(\d+)\s*([+-]\s*\d+)?\s*$", re.I)

@dataclass
class RollResult:
    expr: str
    total: int
    rolls: list[int]
    mod: int

class Dice:
    def __init__(self, rng: random.Random | None = None, seed: int | None = None):
        # Prefer injected RNG (for logging/replay). Fall back to a local Random.
        self.rng = rng if rng is not None else random.Random(seed)

    def d(self, sides: int) -> int:
        # Use randint as the canonical dice primitive.
        # Some older replay logs encoded dice rolls as rng_choice events. We only
        # treat an upcoming rng_choice as a dice roll when it looks index-like.
        try:
            # Legacy compatibility: some older replays recorded dice as rng_choice
            # events (where the chosen "value" is numeric). Only in that case do we
            # interpret the choice as a dice roll.
            if hasattr(self.rng, "peek_next_name") and self.rng.peek_next_name() == "rng_choice":
                ev = self.rng.peek_next_event() if hasattr(self.rng, "peek_next_event") else None
                val = ev.get("value") if isinstance(ev, dict) else None
                if val is not None and str(val).lstrip("-").isdigit():
                    return self.rng.choice(list(range(1, int(sides) + 1)))
        except Exception:
            pass
        return self.rng.randint(1, sides)

    def roll(self, expr: str) -> RollResult:
        m = _DICE_RE.match(expr.replace(" ", ""))
        if not m:
            raise ValueError(f"Bad dice expression: {expr}")
        n = int(m.group(1) or "1")
        sides = int(m.group(2))
        mod = int((m.group(3) or "0").replace(" ", ""))

        # Strict-replay diagnostics: if we're about to roll multiple dice but the
        # recorded stream wants a non-dice choice next, surface a clearer error.
        try:
            rng = getattr(self, "rng", None)
            if n > 1 and bool(getattr(rng, "strict_replay", False)) and hasattr(rng, "peek_next_name"):
                nxt = rng.peek_next_name()
                if nxt == "rng_choice" and hasattr(rng, "peek_next_event"):
                    ev = rng.peek_next_event() or {}
                    val = ev.get("value")
                    if isinstance(val, str) and "Actor(" in val:
                        ctx = getattr(rng, "ctx", None)
                        raise RuntimeError(
                            f"Strict replay: attempted to roll '{expr}' (n={n}) but next recorded event is rng_choice(Actor). "
                            f"This usually means we are rolling too many dice for damage/spell. ctx={ctx}"
                        )
        except RuntimeError:
            raise
        except Exception:
            pass
        rolls = [self.d(sides) for _ in range(n)]
        return RollResult(expr=expr, total=sum(rolls) + mod, rolls=rolls, mod=mod)

    def in_6(self, target: int) -> bool:
        # succeed on 1..target (e.g., 1-2 in 6 => target=2)
        return self.d(6) <= target

    def percent(self) -> int:
        return self.rng.randint(1, 100)
