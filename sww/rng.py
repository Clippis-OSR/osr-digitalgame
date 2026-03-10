from __future__ import annotations

import base64
import pickle
import random
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

LogFn = Callable[[str, dict[str, Any]], None]

class LoggedRandom:
    """A small wrapper around random.Random that logs outputs.

    This is used to harden deterministic replay: we still seed PRNGs, but we
    ALSO record every random output so a future replay runner can reproduce
    exact outcomes even if call ordering changes slightly.
    """

    def __init__(self, seed: int, *, channel: str, log_fn: Optional[LogFn] = None):
        self.seed = int(seed)
        self.channel = str(channel)
        self._rng = random.Random(self.seed)
        self._log = log_fn

    def _emit(self, name: str, data: dict[str, Any]) -> None:
        if self._log:
            payload = dict(data)
            payload["channel"] = self.channel
            self._log(name, payload)

    def _emit_recorded(self, ev: dict[str, Any]) -> None:
        """Emit the recorded event verbatim.

        This makes strict replay comparisons resilient to changes in call arguments
        (e.g., randint ranges) while still enforcing event ordering.
        """
        if not self._log:
            return
        name = str(ev.get("name") or "")
        data = {k: v for k, v in (ev or {}).items() if k != "name"}
        if not data.get("channel"):
            data["channel"] = self.channel
        self._log(name, data)

    # Methods used by the codebase
    def random(self) -> float:
        v = self._rng.random()
        self._emit("rng_random", {"value": v})
        return v

    def randrange(self, start: int, stop: int | None = None, step: int = 1) -> int:
        v = self._rng.randrange(start, stop, step)
        self._emit("rng_randrange", {"start": start, "stop": stop, "step": step, "value": v})
        return v

    def randint(self, a: int, b: int) -> int:
        v = self._rng.randint(a, b)
        self._emit("rng_randint", {"a": a, "b": b, "value": v})
        return v

    def choice(self, seq):
        # Record a stable index to make strict replay possible.
        if not seq:
            raise IndexError("Cannot choose from an empty sequence")
        idx = self._rng.randrange(0, len(seq))
        v = seq[idx]
        self._emit("rng_choice", {"len": len(seq), "index": int(idx), "value": repr(v)[:120]})
        return v

    # Replay compatibility helper (no-op for live RNG)
    def peek_next_name(self):
        return None

    def shuffle(self, x) -> None:
        self._rng.shuffle(x)
        self._emit("rng_shuffle", {"len": len(x)})
        return None

    def sample(self, population, k: int):
        v = self._rng.sample(population, k)
        self._emit("rng_sample", {"len": len(population), "k": k})
        return v

    def export_state(self) -> str:
        """Serialize internal PRNG state for exact save/load restoration."""
        raw = pickle.dumps(self._rng.getstate(), protocol=pickle.HIGHEST_PROTOCOL)
        return base64.b64encode(raw).decode("ascii")

    def import_state(self, state_blob: str | None) -> None:
        if not state_blob:
            return
        raw = base64.b64decode(str(state_blob).encode("ascii"))
        self._rng.setstate(pickle.loads(raw))

    # If code ever needs access:
    @property
    def raw(self) -> random.Random:
        return self._rng


class ReplayRandom:
    """Replay RNG that reuses previously recorded RNG events.

    This is used by strict replay verification. Instead of re-running a PRNG,
    we consume the recorded RNG event stream and return the stored values.

    Expected event format: {"name": <str>, ..., "channel": <str>}.
    The 'name' is the event name emitted by LoggedRandom (e.g. rng_randint).
    """

    def __init__(self, events: list[dict[str, Any]], *, channel: str, log_fn: Optional[LogFn] = None):
        self.channel = str(channel)
        self._events = list(events or [])
        self._i = 0
        self._log = log_fn
        self._last_expected: str | None = None
        self._last_ev: dict[str, Any] | None = None
        self._trace: list[dict[str, Any]] = []  # last few consumption sites
        # Marker used by diagnostics to know we're in strict recorded-RNG mode.
        self.strict_replay = True

    @property
    def trace_tail(self) -> list[dict[str, Any]]:
        return list(self._trace)

    def peek_next_name(self):
        if self._i >= len(self._events):
            return None
        ev = self._events[self._i] or {}
        return str(ev.get("name") or "")

    def peek_next_event(self):
        """Peek the next recorded event without consuming it."""
        try:
            if self._i >= len(self._events):
                return None
            return self._events[self._i]
        except Exception:
            return None

    @property
    def pos(self) -> int:
        return int(self._i)

    @property
    def remaining(self) -> int:
        return max(0, len(self._events) - int(self._i))

    @property
    def last_expected(self) -> str | None:
        return self._last_expected

    @property
    def last_event(self) -> dict[str, Any] | None:
        return dict(self._last_ev) if self._last_ev is not None else None

    def _next(self, expected_name: str) -> dict[str, Any]:
        self._last_expected = expected_name
        if self._i >= len(self._events):
            ctx = getattr(self, "ctx", None)
            ctx_s = f" | ctx={ctx}" if ctx else ""
            raise RuntimeError(
                f"Replay RNG exhausted for channel={self.channel!r} at {expected_name}{ctx_s}"
            )

        # Strict replay compatibility: some historical logs contain one-or-more
        # rng_choice "actor hints" immediately before what is now a dice roll
        # (rng_randint). These were never semantically used by the rules engine
        # but must be consumed to keep the stream aligned.
        if expected_name == "rng_randint":
            safety = 0
            while self._i < len(self._events):
                ev0 = self._events[self._i] or {}
                if str(ev0.get("name") or "") != "rng_choice":
                    break
                hint = str(ev0.get("value") or "")
                if ("Actor(" not in hint) and ("PC(" not in hint):
                    break
                # Consume the hint as a no-op and emit it to the replay log.
                safety += 1
                if safety > 16:
                    break
                self._i += 1
                self._last_ev = dict(ev0)
                self._emit_recorded(ev0)
                # Do not record a trace tail entry here: this is alignment-only.

        ev = dict(self._events[self._i] or {})
        self._i += 1
        name = str(ev.get("name") or "")
        ch = str(ev.get("channel") or "")
        if name != expected_name:
            ctx = getattr(self, "ctx", None)
            ctx_s = f" | ctx={ctx}" if ctx else ""
            raise RuntimeError(
                f"Replay RNG mismatch for channel={self.channel!r} at event #{self._i-1}: expected {expected_name}, got {name!r}{ctx_s}"
            )
        if ch and ch != self.channel:
            raise RuntimeError(f"Replay RNG channel mismatch: expected {self.channel!r}, got {ch!r}")
        self._last_ev = dict(ev)

        # Record a small trace tail to help pinpoint divergence sites.
        try:
            fr = None
            # 0=_next,1=randint/choice/etc,2=caller,3=caller-of-caller
            for depth in (3, 2):
                try:
                    fr = sys._getframe(depth)
                    if fr is not None:
                        break
                except Exception:
                    fr = None
            if fr is not None:
                rec = {
                    "i": int(self._i - 1),
                    "name": str(ev.get("name") or ""),
                    "file": str(fr.f_code.co_filename),
                    "line": int(fr.f_lineno),
                    "fn": str(fr.f_code.co_name),
                    "ctx": getattr(self, "ctx", None),
                    # When available, include the requested API call that consumed this event.
                    "req": getattr(self, "_req", None),
                }
                self._trace.append(rec)
                if len(self._trace) > 12:
                    self._trace = self._trace[-12:]
        except Exception:
            pass
        return ev


    def _emit(self, name: str, data: dict[str, Any]) -> None:
        if self._log:
            payload = dict(data)
            payload["channel"] = self.channel
            self._log(name, payload)

    def _emit_recorded(self, ev: dict[str, Any]) -> None:
        """Emit the recorded event verbatim.

        This makes strict replay comparisons resilient to changes in call arguments
        (e.g., randint ranges) while still enforcing event ordering.
        """
        if not self._log:
            return
        name = str(ev.get("name") or "")
        data = {k: v for k, v in (ev or {}).items() if k != "name"}
        if not data.get("channel"):
            data["channel"] = self.channel
        self._log(name, data)

    # Methods used by the codebase
    def random(self) -> float:
        ev = self._next("rng_random")
        v = float(ev.get("value"))
        self._emit_recorded(ev)
        return v

    def randrange(self, start: int, stop: int | None = None, step: int = 1) -> int:
        ev = self._next("rng_randrange")
        v = int(ev.get("value"))
        self._emit_recorded(ev)
        return v

    def randint(self, a: int, b: int) -> int:
        # Attach the requested bounds to the trace so we can see which call
        # consumed each recorded event during strict replays.
        try:
            self._req = {"fn": "randint", "a": int(a), "b": int(b)}
        except Exception:
            self._req = {"fn": "randint"}
        try:
            ev = self._next("rng_randint")

            # Strict replay hardening: enforce that the recorded bounds match
            # the requested bounds. This prevents silent corruption where a
            # different-size die (e.g. d12) is accidentally consumed by a d4 roll.
            ra = ev.get("a")
            rb = ev.get("b")
            if ra is not None and rb is not None:
                try:
                    ra_i = int(ra)
                    rb_i = int(rb)
                    if (ra_i, rb_i) != (int(a), int(b)):
                        ctx = getattr(self, "ctx", None)
                        ctx_s = f" | ctx={ctx}" if ctx else ""
                        raise RuntimeError(
                            f"Replay RNG randint bounds mismatch for channel={self.channel!r} at event #{self.pos-1}: "
                            f"requested [{int(a)}, {int(b)}], recorded [{ra_i}, {rb_i}]{ctx_s}"
                        )
                except ValueError:
                    # If recorded bounds are not parseable, treat as mismatch.
                    ctx = getattr(self, "ctx", None)
                    ctx_s = f" | ctx={ctx}" if ctx else ""
                    raise RuntimeError(
                        f"Replay RNG randint bounds mismatch for channel={self.channel!r} at event #{self.pos-1}: "
                        f"requested [{a}, {b}], recorded [{ra}, {rb}]{ctx_s}"
                    )

            v = int(ev.get("value"))
            if v < int(a) or v > int(b):
                ctx = getattr(self, "ctx", None)
                ctx_s = f" | ctx={ctx}" if ctx else ""
                raise RuntimeError(
                    f"Replay RNG randint value out of bounds for channel={self.channel!r} at event #{self.pos-1}: "
                    f"value={v}, requested [{int(a)}, {int(b)}]{ctx_s}"
                )

            self._emit_recorded(ev)
            return v
        finally:
            self._req = None


    def choice(self, seq):
        try:
            self._req = {"fn": "choice", "n": int(len(seq) if seq is not None else 0)}
        except Exception:
            self._req = {"fn": "choice"}
        # Replay compatibility: older logs sometimes used rng_randint for
        # index selection that is now modeled as rng_choice.
        nxt = self.peek_next_name()
        if nxt == "rng_randint":
            # Replay compatibility: older logs sometimes used rng_randint for
            # index selection that is now modeled as rng_choice.
            ev = self._next("rng_randint")
            if not seq:
                raise IndexError("Cannot choose from an empty sequence")
            try:
                raw_v = int(ev.get("value"))
            except Exception:
                raw_v = 0
            # Prefer using recorded randint bounds to interpret the value.
            idx = None
            try:
                a = int(ev.get("a"))
                b = int(ev.get("b"))
                span = (b - a + 1)
                if span == len(seq):
                    idx = (raw_v - a) % len(seq)
            except Exception:
                idx = None
            if idx is None:
                # Fallback heuristic: if 1..len was used, convert to 0-based; otherwise modulo.
                if 1 <= raw_v <= len(seq):
                    idx = raw_v - 1
                else:
                    idx = raw_v % len(seq)
            v = seq[idx]
            self._emit_recorded(ev)
            self._req = None
            return v

        ev = self._next("rng_choice")
        if not seq:
            raise IndexError("Cannot choose from an empty sequence")
        val = ev.get("index")
        if (val is not None) and str(val).lstrip("-").isdigit():
            idx = int(val) % len(seq)
        else:
            # Backward-compatible best-effort mapping.
            # Prefer matching the recorded value against the sequence contents.
            hint = ev.get("value", None)
            idx = None
            if hint is not None:
                hint_s = str(hint)
                # Exact match against common string forms.
                matches = []
                for i, item in enumerate(seq):
                    if hint_s == str(item) or hint_s == repr(item):
                        matches.append(i)
                if len(matches) == 1:
                    idx = matches[0]
                elif hint_s.isdigit():
                    # Often recorded as 1-based index when choice was implemented via randint.
                    raw_v = int(hint_s)
                    if 1 <= raw_v <= len(seq):
                        idx = raw_v - 1
            if idx is None:
                # Last resort: stay deterministic but non-destructive.
                idx = 0
        v = seq[idx]
        self._emit_recorded(ev)
        self._req = None
        return v

    def shuffle(self, x) -> None:
        # Strict replay should avoid shuffle; we only consume the event.
        ev = self._next("rng_shuffle")
        self._emit_recorded(ev)
        return None

    def sample(self, population, k: int):
        # Strict replay should avoid sample unless we record indices.
        ev = self._next("rng_sample")
        v = list(population)[: int(k)]
        self._emit_recorded(ev)
        return v

    def export_state(self) -> dict[str, Any]:
        return {"pos": int(self._i)}

    def import_state(self, state_blob: dict[str, Any] | None) -> None:
        if not isinstance(state_blob, dict):
            return
        try:
            pos = int(state_blob.get("pos", self._i))
        except Exception:
            pos = self._i
        self._i = max(0, min(pos, len(self._events)))

    @property
    def raw(self):
        # Compatibility: Dice expects a .raw providing random-like methods.
        return self


def make_seed() -> int:
    """Cryptographically strong seed for a new run."""
    return random.SystemRandom().randint(1, 2**31 - 1)
