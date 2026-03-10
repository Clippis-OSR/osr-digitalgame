from __future__ import annotations

from typing import Any


BLOCKED_STATUS_KEYS = {"asleep", "held", "paralyzed", "paralysis", "silence", "confused", "fear"}
# Transient battle-only status keys removed at combat end/save sanitization.
TRANSIENT_BATTLE_STATUS_KEYS = {"casting", "cover", "parry", "flee_pending"}


def status_dict(actor: Any) -> dict:
    st = getattr(actor, "status", None)
    if isinstance(st, dict):
        return st
    st = {}
    actor.status = st
    return st


def apply_status(actor: Any, key: str, value: Any, *, mode: str = "replace") -> bool:
    """Apply/refresh/stack a status key using a single contract.

    mode: replace|max|stack_int|block
    Returns True when the status changed.
    """
    st = status_dict(actor)
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


def clear_status(actor: Any, key: str) -> bool:
    """Remove a status key, returning True if it existed."""
    st = status_dict(actor)
    k = str(key)
    existed = k in st
    st.pop(k, None)
    return existed


def tick_round_statuses(actors: list[Any], *, phase: str = "start") -> None:
    """Round-based status ticking with one expiry path."""
    for actor in actors:
        st = status_dict(actor)
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


def cleanup_actor_battle_status(actor: Any) -> None:
    """Cleanup path for death/flee/removal from battlefield."""
    st = status_dict(actor)
    for k in ("casting", "cover", "parry", "flee_pending"):
        st.pop(k, None)
