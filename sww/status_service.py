from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TRANSIENT_BATTLE_STATUS_KEYS = {"casting", "cover", "parry", "flee_pending"}


@dataclass(frozen=True)
class StatusService:
    """Single owner for status lifecycle operations.

    This service intentionally preserves existing semantics used by the prior
    status_lifecycle helpers while centralizing all mutation paths.
    """

    @staticmethod
    def status_dict(actor: Any) -> dict:
        st = getattr(actor, "status", None)
        if isinstance(st, dict):
            return st
        st = {}
        actor.status = st
        return st

    @staticmethod
    def apply_status(actor: Any, key: str, value: Any, *, mode: str = "replace") -> bool:
        st = StatusService.status_dict(actor)
        k = str(key)
        if mode == "block" and k in st:
            return False
        if mode == "max":
            old = st.get(k, 0)
            try:
                st[k] = max(int(old or 0), int(value or 0))
            except Exception:
                st[k] = value
            return st.get(k) != old
        if mode == "stack_int":
            old = st.get(k, 0)
            try:
                st[k] = int(old or 0) + int(value or 0)
            except Exception:
                st[k] = value
            return st.get(k) != old
        old = st.get(k)
        st[k] = value
        return st.get(k) != old

    @staticmethod
    def clear_status(actor: Any, key: str) -> bool:
        st = StatusService.status_dict(actor)
        k = str(key)
        existed = k in st
        st.pop(k, None)
        return existed

    @staticmethod
    def expire_timed_statuses(actors: list[Any], *, phase: str = "start") -> None:
        for actor in actors:
            st = StatusService.status_dict(actor)
            if not st:
                continue
            for k in list(st.keys()):
                if k == "casting" and phase == "start":
                    continue
                v = st.get(k)
                if isinstance(v, int):
                    if v > 0:
                        st[k] = v - 1
                    if int(st.get(k, 0) or 0) <= 0:
                        st.pop(k, None)
                elif isinstance(v, dict) and "rounds" in v:
                    try:
                        v["rounds"] = int(v.get("rounds", 0)) - 1
                    except Exception:
                        v["rounds"] = 0
                    if int(v.get("rounds", 0) or 0) <= 0:
                        st.pop(k, None)

    @staticmethod
    def cleanup_on_death_or_exit(actor: Any) -> None:
        st = StatusService.status_dict(actor)
        for k in TRANSIENT_BATTLE_STATUS_KEYS:
            st.pop(k, None)
        StatusService._run_cleanup_hooks(actor, reason="death_or_exit")

    @staticmethod
    def _run_cleanup_hooks(actor: Any, *, reason: str) -> None:
        key = "_status_cleanup_reasons"
        seen = getattr(actor, key, None)
        if not isinstance(seen, set):
            seen = set()
            setattr(actor, key, seen)
        if reason in seen:
            return
        seen.add(reason)
        hooks = getattr(actor, "status_cleanup_hooks", None)
        if not isinstance(hooks, list):
            return
        for hook in list(hooks):
            if not callable(hook):
                continue
            try:
                hook(actor, reason)
            except Exception:
                continue
