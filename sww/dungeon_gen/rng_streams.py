from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sww.dice import Dice
from sww.rng import LoggedRandom


LogFn = Callable[[str, dict[str, Any]], None]


def _seed_to_int(seed: str) -> int:
    """Derive a stable 31-bit int seed from an arbitrary string."""
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    # Keep in the same range as make_seed() (<= 2**31-1)
    v = int.from_bytes(h[:4], "big", signed=False) & 0x7FFFFFFF
    return v if v != 0 else 1


@dataclass(frozen=True)
class RNGStream:
    name: str
    seed: int
    rng: LoggedRandom
    dice: Dice

    def roll(self, expr: str) -> int:
        return int(self.dice.roll(expr).total)


class RNGStreams:
    """Deterministic named RNG streams.

    We derive each stream's numeric seed from the base seed plus the stream
    name, so that adding or removing rolls in one stream cannot perturb the
    others.
    """

    def __init__(
        self,
        base_seed: str,
        *,
        log_fn: Optional[LogFn] = None,
    ):
        self.base_seed = str(base_seed)
        self._log_fn = log_fn
        self._cache: dict[str, RNGStream] = {}

    def stream(self, name: str) -> RNGStream:
        name = str(name)
        if name in self._cache:
            return self._cache[name]
        derived = _seed_to_int(f"{self.base_seed}::{name}")
        rng = LoggedRandom(derived, channel=name, log_fn=self._log_fn)
        dice = Dice(rng=rng)
        s = RNGStream(name=name, seed=derived, rng=rng, dice=dice)
        self._cache[name] = s
        return s

    def describe(self) -> dict[str, str]:
        # For reports: map stream name -> derived seed string
        return {k: str(v.seed) for k, v in sorted(self._cache.items(), key=lambda kv: kv[0])}
