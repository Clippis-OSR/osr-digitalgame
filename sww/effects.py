from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Iterable

from .models import Actor, mod_ability
from . import rules


# --- Hooks --------------------------------------------------------------------

class Hook:
    """Well-known effect timing hooks.

    Keep this intentionally small; most OSR effects fit in these.
    """
    START_OF_ROUND = "start_of_round"
    START_OF_TURN = "start_of_turn"
    END_OF_TURN = "end_of_turn"
    ON_HIT = "on_hit"
    ON_DAMAGE = "on_damage"
    DAY_PASSED = "day_passed"


@dataclass
class ModifierBundle:
    """Aggregated modifiers/constraints derived from active effects."""
    can_act: bool = True
    petrified: bool = False
    to_hit_mod: int = 0
    ac_mod: int = 0
    save_mod: int = 0
    forced_retreat: bool = False
    # Healing constraints for diseases/rots.
    blocks_natural_heal: bool = False
    blocks_magical_heal: bool = False
    # If set, the actor is treated as belonging to this side in combat.
    # Expected values: "party" or "foe".
    side_override: Optional[str] = None
    # Future: move_mod, spell_disrupted, etc.


class EffectDef(Protocol):
    kind: str

    def on_apply(self, mgr: "EffectsManager", target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None: ...
    def on_hook(self, mgr: "EffectsManager", hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None: ...
    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle: ...
    def stacking_key(self, inst: Dict[str, Any]) -> str: ...
    def stacking_policy(self, inst: Dict[str, Any]) -> str: ...


# --- Manager ------------------------------------------------------------------

class EffectsManager:
    """Centralized status effect system.

    This manager is intentionally lightweight and JSON-first. Each Actor owns a
    dict of effect instances under Actor.effect_instances.

    The manager:
    - applies effects with standardized stacking behavior
    - ticks effects on hooks
    - aggregates modifiers (e.g., paralysis prevents actions)
    - emits events via Game.events (when available)
    - performs small migrations from legacy status containers (e.g., poison in Actor.status)
    """

    def __init__(self, game: Any):
        self.game = game
        self._defs: dict[str, EffectDef] = {
            "poison": PoisonEffectDef(),
            "paralysis": ParalysisEffectDef(),
            "petrification": PetrificationEffectDef(),
            "regeneration": RegenerationEffectDef(),
            "gaze_petrification": GazePetrificationEffectDef(),
            "avert_gaze": AvertGazeEffectDef(),
            "energy_drain": EnergyDrainEffectDef(),
            "fear": FearEffectDef(),
            "charm": CharmEffectDef(),
            "disease": DiseaseEffectDef(),
            "grapple": GrappleEffectDef(),
            "engulf": EngulfEffectDef(),
            "swallow": SwallowEffectDef(),
            # P4.10.9: Monk deadly strike stun.
            "stunned": StunEffectDef(),
        }

    # ---- utilities ----

    def _emit(self, name: str, data: Optional[Dict[str, Any]] = None) -> None:
        ev = getattr(self.game, "events", None)
        if ev is None:
            return
        try:
            ev.emit(
                name,
                data=data or {},
                campaign_day=int(getattr(self.game, "campaign_day", 1)),
                day_watch=int(getattr(self.game, "day_watch", 0)),
                dungeon_turn=int(getattr(self.game, "dungeon_turn", 0)),
                room_id=int(getattr(self.game, "room_id", 0)),
                hex_q=int(getattr(self.game, "party_hex", (0, 0))[0]),
                hex_r=int(getattr(self.game, "party_hex", (0, 0))[1]),
            )
            # If we are inside a buffered combat log, mirror key effect events into battle events
            try:
                be = getattr(self.game, "battle_evt", None)
                if callable(be) and bool(getattr(self.game, "_battle_buffer_active", False)):
                    if name == "effect_applied":
                        be("EFFECT_APPLIED", tgt=str((data or {}).get("target", "")), kind=str((data or {}).get("kind", "")))
                    elif name == "effect_removed":
                        reason = str((data or {}).get("reason", ""))
                        kind = str((data or {}).get("kind", ""))
                        tgt = str((data or {}).get("target", ""))
                        if reason == "expired":
                            be("EFFECT_EXPIRED", tgt=tgt, kind=kind)
                        else:
                            be("EFFECT_REMOVED", tgt=tgt, kind=kind, reason=reason)
            except Exception:
                pass
        except Exception:
            # Don't let event issues break gameplay.
            pass

    def _ui(self, msg: str) -> None:
        ui = getattr(self.game, "ui", None)
        if ui is not None:
            try:
                ui.log(msg)
            except Exception:
                pass

    def _dice(self):
        return getattr(self.game, "dice", None)

    def register(self, eff_def: EffectDef) -> None:
        self._defs[eff_def.kind] = eff_def

    def ensure_migrated(self, a: Actor) -> None:
        """Migrate legacy status structures into effect_instances (best-effort).

        P2.0 stored poison DOT under a.status["poison"]["effects"].
        """
        insts = getattr(a, "effect_instances", None)
        if not isinstance(insts, dict):
            a.effect_instances = {}
            insts = a.effect_instances

        st = getattr(a, "status", None)
        if not isinstance(st, dict) or "poison" not in st:
            return

        p = st.get("poison")
        if not isinstance(p, dict):
            return
        effects = p.get("effects")
        if not isinstance(effects, dict) or not effects:
            return

        # Convert each stored poison effect into a poison effect instance.
        for key, eff in list(effects.items()):
            if not isinstance(eff, dict):
                continue
            mode = str(eff.get("mode") or "").lower()
            pid = str(eff.get("id") or key).strip() or key
            src = str(eff.get("source") or "poison")
            # Make a stable-ish instance_id based on stored key.
            instance_id = f"poison:{pid}:{key}"
            if instance_id in insts:
                continue
            inst: Dict[str, Any] = {
                "instance_id": instance_id,
                "kind": "poison",
                "source": {"name": src},
                "created_round": int(getattr(self.game, "combat_round", 1) or 1),
                "duration": {"type": "rounds", "remaining": int(eff.get("rounds", 0) or 0)} if mode == "dot" else {"type": "none"},
                "tags": ["harmful", "poison"],
                "params": {
                    "id": pid,
                    "mode": mode or "dot",
                    "dot_damage": str(eff.get("dot_damage") or "1d6"),
                    "save_tags": ["poison"],
                    "save_each_tick": bool(eff.get("save_each_tick", False)),
                },
                "state": {"resolved": bool(eff.get("resolved", False))},
            }
            insts[instance_id] = inst

        # Remove legacy poison blob now that it's migrated.
        st.pop("poison", None)
        a.status = st

    # ---- API ----

    def apply(self, target: Actor, inst: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> None:
        ctx = dict(ctx or {})
        self.ensure_migrated(target)

        kind = str(inst.get("kind") or inst.get("type") or "").lower()
        if not kind:
            return
        eff_def = self._defs.get(kind)
        if eff_def is None:
            return

        # Ensure instance_id
        instance_id = str(inst.get("instance_id") or "").strip()
        if not instance_id:
            pid = str(inst.get("params", {}).get("id") or kind)
            # Include round to avoid collisions; store a stable-ish id.
            round_no = int(ctx.get("round_no") or getattr(self.game, "combat_round", 1) or 1)
            instance_id = f"{kind}:{pid}:r{round_no}:{target.name}"
            inst["instance_id"] = instance_id

        # Standardize containers
        if not isinstance(getattr(target, "effect_instances", None), dict):
            target.effect_instances = {}
        container = target.effect_instances

        # Stacking
        policy = eff_def.stacking_policy(inst)
        skey = eff_def.stacking_key(inst)

        existing_id = None
        for eid, e in list(container.items()):
            if not isinstance(e, dict):
                continue
            if str(e.get("kind") or "").lower() != kind:
                continue
            try:
                if eff_def.stacking_key(e) == skey:
                    existing_id = eid
                    break
            except Exception:
                continue

        if existing_id is not None:
            if policy == "ignore":
                self._emit("effect_ignored", {"kind": kind, "target": target.name, "instance_id": existing_id})
                return
            if policy in ("refresh", "replace"):
                # Replace or refresh duration/state.
                if policy == "replace":
                    container.pop(existing_id, None)
                else:
                    # Refresh: keep id but refresh remaining duration if present in new inst.
                    old = container.get(existing_id, {})
                    # If both have duration, refresh remaining.
                    if isinstance(old, dict) and isinstance(old.get("duration"), dict) and isinstance(inst.get("duration"), dict):
                        old["duration"]["remaining"] = int(inst["duration"].get("remaining", old["duration"].get("remaining", 0)) or 0)
                    # Also allow param refresh (e.g., dot_damage)
                    if isinstance(old, dict) and isinstance(old.get("params"), dict) and isinstance(inst.get("params"), dict):
                        old["params"].update(inst.get("params") or {})
                    container[existing_id] = old
                    inst = old
                    inst["instance_id"] = existing_id

        # Store and apply
        container[instance_id] = inst
        self._emit("effect_applied", {"kind": kind, "target": target.name, "instance_id": instance_id, "source": inst.get("source")})
        try:
            eff_def.on_apply(self, target, inst, ctx)
        except Exception:
            # Effects should never crash combat.
            pass

    def remove(self, target: Actor, instance_id: str, *, reason: str = "removed", ctx: Optional[Dict[str, Any]] = None) -> None:
        ctx = dict(ctx or {})
        if not isinstance(getattr(target, "effect_instances", None), dict):
            return
        inst = target.effect_instances.pop(instance_id, None)
        if inst is None:
            return
        self._emit("effect_removed", {"kind": inst.get("kind"), "target": target.name, "instance_id": instance_id, "reason": reason})

    def remove_kind(self, target: Actor, kind: str, *, reason: str = "removed") -> int:
        """Remove all effect instances of a given kind from a target.

        Returns the number of removed instances.
        """
        kind = str(kind or "").lower().strip()
        if not kind:
            return 0
        self.ensure_migrated(target)
        if not isinstance(getattr(target, "effect_instances", None), dict):
            return 0
        to_remove = [eid for eid, inst in list(target.effect_instances.items())
                     if isinstance(inst, dict) and str(inst.get("kind") or "").lower() == kind]
        for eid in to_remove:
            self.remove(target, eid, reason=reason)
        return len(to_remove)


    def cleanup_on_death(self, dead: Actor, *, pool: Iterable[Actor] | None = None) -> None:
        """Hardening: remove control effects that should not persist after death.

        This targets common "dead-but-still-grappled/swallowed" edge cases.
        """

        dead_name = str(getattr(dead, "name", "") or "")
        dead_mid = str(getattr(dead, "monster_id", "") or "")
        dead_key = f"{dead_name}|{dead_mid}".lower()

        # Remove the dead actor's own control states.
        for k in ("grapple", "engulf", "swallow", "fear", "charm"):
            try:
                self.remove_kind(dead, k, reason="death")
            except Exception:
                pass

        # Remove any other actor's control states that reference the dead as source.
        if pool is None:
            return
        for a in pool:
            if a is None or a is dead:
                continue
            container = getattr(a, "effect_instances", None)
            if not isinstance(container, dict):
                continue
            for eid, inst in list(container.items()):
                if not isinstance(inst, dict):
                    continue
                kind = str(inst.get("kind") or "").lower()
                if kind not in ("grapple", "engulf", "swallow"):
                    continue
                params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
                sk = str(params.get("source_key") or "").lower()
                if sk and sk == dead_key:
                    self.remove(a, eid, reason="source_died")

    def tick(self, actors: Iterable[Actor], hook: str, ctx: Optional[Dict[str, Any]] = None) -> None:
        ctx = dict(ctx or {})
        # First pass: allow effects to react to the hook.
        actors_list = list(actors)
        for a in actors_list:
            if a is None:
                continue
            self.ensure_migrated(a)
            container = getattr(a, "effect_instances", None)
            if not isinstance(container, dict) or not container:
                continue
            for eid in list(container.keys()):
                inst = container.get(eid)
                if not isinstance(inst, dict):
                    container.pop(eid, None)
                    continue
                kind = str(inst.get("kind") or "").lower()
                eff_def = self._defs.get(kind)
                if eff_def is None:
                    continue
                try:
                    eff_def.on_hook(self, hook, a, inst, ctx)
                except Exception:
                    pass
                # Expire duration after hook processing.
                dur = inst.get("duration")
                if isinstance(dur, dict) and dur.get("type") == "rounds":
                    try:
                        rem = int(dur.get("remaining", 0) or 0)
                    except Exception:
                        rem = 0
                    # Some effects (e.g., avert_gaze) use manager-managed duration tick
                    # to ensure they remain active for the whole round.
                    if hook == Hook.START_OF_ROUND and kind == "avert_gaze":
                        continue
                    if rem <= 0:
                        self.remove(a, eid, reason="expired", ctx=ctx)

        # Second pass: manager-managed duration decrement for select effects.
        if hook == Hook.START_OF_ROUND:
            for a in actors_list:
                container = getattr(a, "effect_instances", None)
                if not isinstance(container, dict) or not container:
                    continue
                for eid, inst in list(container.items()):
                    if not isinstance(inst, dict):
                        continue
                    kind = str(inst.get("kind") or "").lower()
                    if kind != "avert_gaze":
                        continue
                    dur = inst.get("duration")
                    if not (isinstance(dur, dict) and dur.get("type") == "rounds"):
                        continue
                    try:
                        dur["remaining"] = int(dur.get("remaining", 0) or 0) - 1
                    except Exception:
                        dur["remaining"] = 0
                    if int(dur.get("remaining", 0) or 0) <= 0:
                        self.remove(a, eid, reason="expired", ctx=ctx)

    def query_modifiers(self, actor: Actor) -> ModifierBundle:
        self.ensure_migrated(actor)
        out = ModifierBundle()
        container = getattr(actor, "effect_instances", None)
        if not isinstance(container, dict) or not container:
            return out
        for inst in container.values():
            if not isinstance(inst, dict):
                continue
            kind = str(inst.get("kind") or "").lower()
            eff_def = self._defs.get(kind)
            if eff_def is None:
                continue
            try:
                mb = eff_def.get_modifiers(actor, inst)
            except Exception:
                continue
            out.can_act = out.can_act and mb.can_act
            out.petrified = out.petrified or mb.petrified
            out.to_hit_mod += mb.to_hit_mod
            out.ac_mod += mb.ac_mod
            out.save_mod += mb.save_mod
            out.forced_retreat = out.forced_retreat or mb.forced_retreat
            out.blocks_natural_heal = out.blocks_natural_heal or mb.blocks_natural_heal
            out.blocks_magical_heal = out.blocks_magical_heal or mb.blocks_magical_heal
            if mb.side_override:
                out.side_override = mb.side_override
        return out


# --- Poison -------------------------------------------------------------------

class PoisonEffectDef:
    kind = "poison"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        # Default: same poison id refreshes.
        return "refresh"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        pid = str(params.get("id") or "poison")
        mode = str(params.get("mode") or "")
        return f"{pid}:{mode}"

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        # Poison doesn't directly modify actions; DOT ticks on hooks.
        return ModifierBundle()

    def on_apply(self, mgr: EffectsManager, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        mode = str(params.get("mode") or "instant_death").lower()
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "poison")
        tags = set(params.get("save_tags") or ["poison"])

        # Determine if a save is required on apply for this poison.
        save_on_apply = True
        if mode == "dot":
            save_on_apply = bool(params.get("save_on_apply", True))
        if save_on_apply:
            ok = rules.saving_throw(mgr._dice(), target, bonus=int(params.get("save_bonus", 0) or 0), tags=tags)  # type: ignore[arg-type]
            mgr._emit("saving_throw_rolled", {"target": target.name, "tags": list(tags), "success": ok, "kind": "poison"})
            if ok:
                mgr._ui(f"{target.name} resists {src}'s poison!")
                mgr.remove(target, str(inst.get("instance_id")), reason="resisted", ctx=ctx)
                return

        # Fail or no save: apply effect behavior.
        if mode == "instant_death":
            mgr._ui(f"{target.name} fails their save vs poison and dies!")
            target.hp = -1
            mgr._emit("poison_killed", {"target": target.name, "source": src})
            mgr.remove(target, str(inst.get("instance_id")), reason="resolved", ctx=ctx)
            return

        if mode == "damage":
            dmg_expr = str(params.get("damage") or "1d6")
            try:
                rr = mgr._dice().roll(dmg_expr)  # type: ignore[union-attr]
                dmg = int(rr.total)
            except Exception:
                dmg = int(mgr._dice().d(6))  # type: ignore[union-attr]
            target.hp = max(-1, target.hp - dmg)
            mgr._ui(f"{target.name} is poisoned by {src} for {dmg} damage! (HP {target.hp}/{target.hp_max})")
            mgr._emit("poison_damage", {"target": target.name, "source": src, "damage": dmg})
            if target.hp <= -1:
                mgr._ui(f"{target.name} dies from poison!")
                mgr._emit("poison_killed", {"target": target.name, "source": src})
            mgr.remove(target, str(inst.get("instance_id")), reason="resolved", ctx=ctx)
            return

        if mode == "dot":
            # Ensure duration.
            dur = inst.get("duration")
            if not isinstance(dur, dict) or dur.get("type") != "rounds":
                # Try to take remaining from params
                rounds = int(params.get("rounds", 0) or 0)
                inst["duration"] = {"type": "rounds", "remaining": rounds}
            # Announce
            rem = int(inst.get("duration", {}).get("remaining", 0) or 0)
            mgr._ui(f"{target.name} is poisoned by {src}! ({rem} rounds)")
            return

    def on_hook(self, mgr: EffectsManager, hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        mode = str(params.get("mode") or "").lower()
        if mode != "dot":
            return
        if hook != Hook.START_OF_ROUND:
            return
        if target.hp <= 0:
            return
        # Tick DOT
        dur = inst.get("duration")
        if not isinstance(dur, dict) or dur.get("type") != "rounds":
            return
        rem = int(dur.get("remaining", 0) or 0)
        if rem <= 0:
            return

        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "poison")

        # Optional save each tick
        if bool(params.get("save_each_tick", False)):
            tags = set(params.get("save_tags") or ["poison"])
            ok = rules.saving_throw(mgr._dice(), target, bonus=int(params.get("save_bonus", 0) or 0), tags=tags)  # type: ignore[arg-type]
            mgr._emit("saving_throw_rolled", {"target": target.name, "tags": list(tags), "success": ok, "kind": "poison_tick"})
            if ok:
                mgr._ui(f"{target.name} fights off the poison!")
                dur["remaining"] = 0
                return

        dmg_expr = str(params.get("dot_damage") or "1d6")
        try:
            rr = mgr._dice().roll(dmg_expr)  # type: ignore[union-attr]
            dmg = int(rr.total)
        except Exception:
            dmg = int(mgr._dice().d(6))  # type: ignore[union-attr]

        target.hp = max(-1, target.hp - dmg)
        mgr._ui(f"{target.name} suffers {dmg} poison damage from {src}! (HP {target.hp}/{target.hp_max})")
        mgr._emit("effect_ticked", {"kind": "poison", "target": target.name, "instance_id": inst.get("instance_id"), "damage": dmg, "source": src})

        if target.hp <= -1:
            mgr._ui(f"{target.name} dies from poison!")
            mgr._emit("poison_killed", {"target": target.name, "source": src})
            dur["remaining"] = 0
            return

        dur["remaining"] = rem - 1
        if dur["remaining"] <= 0:
            mgr._ui(f"{target.name}'s poison fades.")


# --- Paralysis ----------------------------------------------------------------

class ParalysisEffectDef:
    """Simple combat paralysis/hold effect.

    This is intentionally minimal:
    - save vs paralysis on application
    - on failure, the target cannot act while the effect lasts
    - duration counts down in combat rounds
    """

    kind = "paralysis"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        # Same paralysis id refreshes.
        return "refresh"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        pid = str(params.get("id") or "paralysis")
        return pid

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        return ModifierBundle(can_act=False)

    def on_apply(self, mgr: EffectsManager, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "paralysis")
        tags = set(params.get("save_tags") or ["paralysis"])

        # P4.10.5: racial immunity hook (Elf vs ghoul paralysis).
        try:
            from .races import immune_to_ghoul_paralysis
            if immune_to_ghoul_paralysis(target, src):
                mgr._ui(f"{target.name} is immune to {src}'s paralysis!")
                mgr.remove(target, str(inst.get("instance_id")), reason="immune", ctx=ctx)
                return
        except Exception:
            pass

        ok = rules.saving_throw(mgr._dice(), target, bonus=int(params.get("save_bonus", 0) or 0), tags=tags)  # type: ignore[arg-type]
        mgr._emit("saving_throw_rolled", {"target": target.name, "tags": list(tags), "success": ok, "kind": "paralysis"})
        if ok:
            mgr._ui(f"{target.name} resists {src}'s paralysis!")
            mgr.remove(target, str(inst.get("instance_id")), reason="resisted", ctx=ctx)
            return

        # Ensure duration.
        dur = inst.get("duration")
        if not isinstance(dur, dict) or dur.get("type") != "rounds":
            rounds = int(params.get("rounds", 1) or 1)
            inst["duration"] = {"type": "rounds", "remaining": rounds}
            dur = inst["duration"]

        rem = int(dur.get("remaining", 0) or 0)
        if rem <= 0:
            mgr.remove(target, str(inst.get("instance_id")), reason="no_duration", ctx=ctx)
            return

        mgr._ui(f"{target.name} is paralyzed by {src}! ({rem} rounds)")

    def on_hook(self, mgr: EffectsManager, hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        if hook != Hook.START_OF_ROUND:
            return
        if target.hp <= 0:
            return
        dur = inst.get("duration")
        if not isinstance(dur, dict) or dur.get("type") != "rounds":
            return
        rem = int(dur.get("remaining", 0) or 0)
        if rem <= 0:
            return
        dur["remaining"] = rem - 1
        mgr._emit(
            "effect_ticked",
            {
                "kind": "paralysis",
                "target": target.name,
                "instance_id": inst.get("instance_id"),
                "remaining": int(dur["remaining"]),
            },
        )
        if dur["remaining"] <= 0:
            mgr._ui(f"{target.name} can move again.")


# --- Stun --------------------------------------------------------------------

class StunEffectDef:
    """Simple stun effect used by Monk Deadly Strike (P4.10.9).

    - No saving throw (the Deadly Strike proc already rolled)
    - Prevents actions while active
    - Duration counts down in combat rounds
    """

    kind = "stunned"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        # Same id refreshes.
        return "refresh"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        pid = str(params.get("id") or "stunned")
        return pid

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        return ModifierBundle(can_act=False)

    def on_apply(self, mgr: EffectsManager, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "stun")

        dur = inst.get("duration")
        if not isinstance(dur, dict) or dur.get("type") != "rounds":
            rounds = int(params.get("rounds", 1) or 1)
            inst["duration"] = {"type": "rounds", "remaining": rounds}
            dur = inst["duration"]

        rem = int(dur.get("remaining", 0) or 0)
        if rem <= 0:
            mgr.remove(target, str(inst.get("instance_id")), reason="no_duration", ctx=ctx)
            return

        mgr._ui(f"{target.name} is stunned by {src}! ({rem} rounds)")

    def on_hook(self, mgr: EffectsManager, hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        if hook != Hook.START_OF_ROUND:
            return
        if target.hp <= 0:
            return
        dur = inst.get("duration")
        if not isinstance(dur, dict) or dur.get("type") != "rounds":
            return
        rem = int(dur.get("remaining", 0) or 0)
        if rem <= 0:
            return
        dur["remaining"] = rem - 1
        mgr._emit(
            "effect_ticked",
            {
                "kind": "stunned",
                "target": target.name,
                "instance_id": inst.get("instance_id"),
                "remaining": int(dur["remaining"]),
            },
        )
        if dur["remaining"] <= 0:
            mgr._ui(f"{target.name} shakes off the stun.")


# --- Petrification ------------------------------------------------------------

class PetrificationEffectDef:
    """Save-or-petrify effect.

    - On apply: save vs petrification
    - On success: removed immediately
    - On failure: persists (permanent unless cured)
    - While active: target cannot act; marked petrified
    """

    kind = "petrification"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        return "ignore"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        return "petrified"

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        return ModifierBundle(can_act=False, petrified=True)

    def on_apply(self, mgr: EffectsManager, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "petrification")
        tags = set(params.get("save_tags") or ["petrification"])

        ok = rules.saving_throw(mgr._dice(), target, bonus=int(params.get("save_bonus", 0) or 0), tags=tags)  # type: ignore[arg-type]
        mgr._emit("saving_throw_rolled", {"target": target.name, "tags": list(tags), "success": ok, "kind": "petrification"})
        if ok:
            mgr._ui(f"{target.name} resists petrification from {src}!")
            mgr.remove(target, str(inst.get("instance_id")), reason="resisted", ctx=ctx)
            return

        mgr._ui(f"{target.name} is turned to stone by {src}!")
        dur = inst.get("duration")
        if not (isinstance(dur, dict) and dur.get("type") in ("rounds", "none")):
            inst["duration"] = {"type": "none"}

    def on_hook(self, mgr: EffectsManager, hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        return


# --- Energy Drain -------------------------------------------------------------

class EnergyDrainEffectDef:
    """Energy/level drain (e.g., Wights, Wraiths, Spectres).

    Implemented as an immediate resolution on apply:
    - drains N levels from PCs (min level 1)
    - sets XP to the minimum threshold for the new level
    - reduces HP max by one level's worth of HD per drained level (using the
      same HD roll/minimum logic as level-up, but subtracting)
    - recomputes derived class features (save curve, spell slots, thief skills)

    The instance is removed immediately after applying.
    """

    kind = "energy_drain"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        # Multiple hits should drain multiple times.
        return "stack"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        # Unique per application.
        return str(inst.get("instance_id") or "energy_drain")

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        return ModifierBundle()

    def on_apply(self, mgr: EffectsManager, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "energy drain")

        if not bool(getattr(target, "is_pc", False)):
            mgr.remove(target, str(inst.get("instance_id")), reason="non_pc", ctx=ctx)
            return

        try:
            n = int(params.get("levels", 1) or 1)
        except Exception:
            n = 1
        n = max(1, n)

        old_level = int(getattr(target, "level", 1) or 1)
        old_xp = int(getattr(target, "xp", 0) or 0)
        cls = str(getattr(target, "cls", "Fighter") or "Fighter")

        new_level = max(1, old_level - n)
        drained_levels = max(0, old_level - new_level)
        if drained_levels <= 0:
            mgr.remove(target, str(inst.get("instance_id")), reason="no_effect", ctx=ctx)
            return

        # XP floor for new level.
        try:
            xp_floor = int(mgr.game.xp_threshold_for_class(cls, new_level) or 0)
        except Exception:
            xp_floor = 0

        # HP max reduction.
        hp_loss_total = 0
        for _ in range(drained_levels):
            try:
                hd = int(mgr.game.class_hit_die(cls) or 6)
            except Exception:
                hd = 6
            try:
                roll = int(mgr._dice().d(hd))  # type: ignore[union-attr]
            except Exception:
                roll = 1
            min_loss = (hd + 1) // 2
            loss = max(roll, min_loss)
            # Mirror level-up: add CON mod only if positive.
            try:
                stats = getattr(target, "stats", None)
                if stats is not None:
                    from .rules import mod_ability
                    loss += max(0, mod_ability(getattr(stats, "CON", 10)))
            except Exception:
                pass
            hp_loss_total += int(loss)

        setattr(target, "level", int(new_level))
        setattr(target, "xp", int(xp_floor))

        try:
            target.hp_max = max(1, int(getattr(target, "hp_max", 1) or 1) - int(hp_loss_total))
        except Exception:
            target.hp_max = max(1, int(getattr(target, "hp_max", 1) or 1))
        target.hp = min(int(getattr(target, "hp", 1) or 1), int(getattr(target, "hp_max", 1) or 1))

        try:
            mgr.game.recalc_pc_class_features(target)  # type: ignore[arg-type]
        except Exception:
            pass

        # Trim prepared spells if slots decreased.
        try:
            slots = dict(getattr(target, "spell_slots", {}) or {})
            max_prepared = sum(int(v) for v in slots.values())
            prep = list(getattr(target, "spells_prepared", []) or [])
            if max_prepared >= 0 and len(prep) > max_prepared:
                setattr(target, "spells_prepared", prep[:max_prepared])
        except Exception:
            pass

        mgr._ui(f"{target.name} is drained by {src}! Level {old_level}→{new_level}, XP {old_xp}→{xp_floor}.")
        mgr._emit(
            "energy_drained",
            {
                "target": target.name,
                "source": src,
                "levels": int(drained_levels),
                "old_level": int(old_level),
                "new_level": int(new_level),
                "old_xp": int(old_xp),
                "new_xp": int(xp_floor),
                "hp_loss": int(hp_loss_total),
            },
        )

        mgr.remove(target, str(inst.get("instance_id")), reason="resolved", ctx=ctx)

    def on_hook(self, mgr: EffectsManager, hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        return


# --- Fear --------------------------------------------------------------------

class FearEffectDef:
    """Simple fear effect.

    - Save vs Spells on application
    - On failure, lasts N rounds
    - While active, signals combat code to force retreat/cowering
    """

    kind = "fear"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        return "refresh"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        return str(params.get("id") or "fear")

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        th = int(params.get("to_hit_mod", -2) or -2)
        return ModifierBundle(forced_retreat=True, to_hit_mod=th)

    def on_apply(self, mgr: EffectsManager, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "fear")
        tags = set(params.get("save_tags") or ["spells"])

        ok = rules.saving_throw(mgr._dice(), target, bonus=int(params.get("save_bonus", 0) or 0), tags=tags)  # type: ignore[arg-type]
        mgr._emit("saving_throw_rolled", {"target": target.name, "tags": list(tags), "success": ok, "kind": "fear"})
        if ok:
            mgr._ui(f"{target.name} steels their nerve against {src}!")
            mgr.remove(target, str(inst.get("instance_id")), reason="resisted", ctx=ctx)
            return

        dur = inst.get("duration")
        if not isinstance(dur, dict) or dur.get("type") != "rounds":
            rounds = int(params.get("rounds", 0) or 0)
            if rounds <= 0:
                expr = str(params.get("duration") or "1d4")
                try:
                    rr = mgr._dice().roll(expr)  # type: ignore[union-attr]
                    rounds = int(rr.total)
                except Exception:
                    rounds = int(mgr._dice().d(4))  # type: ignore[union-attr]
            inst["duration"] = {"type": "rounds", "remaining": max(1, rounds)}
            dur = inst["duration"]

        rem = int(dur.get("remaining", 0) or 0)
        mgr._ui(f"{target.name} is seized by fear! ({rem} rounds)")

    def on_hook(self, mgr: EffectsManager, hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        if hook != Hook.START_OF_ROUND:
            return
        dur = inst.get("duration")
        if not isinstance(dur, dict) or dur.get("type") != "rounds":
            return
        rem = int(dur.get("remaining", 0) or 0)
        if rem <= 0:
            return
        dur["remaining"] = rem - 1
        mgr._emit("effect_ticked", {"kind": "fear", "target": target.name, "instance_id": inst.get("instance_id"), "remaining": int(dur["remaining"])})
        if dur["remaining"] <= 0:
            mgr._ui(f"{target.name} overcomes their fear.")


# --- Charm -------------------------------------------------------------------

class CharmEffectDef:
    """Charm that flips combat allegiance for a duration."""

    kind = "charm"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        return "refresh"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        return str(params.get("id") or "charm")

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        st = inst.get("state") if isinstance(inst.get("state"), dict) else {}
        side = st.get("side_override")
        side = str(side) if side in ("party", "foe") else None
        return ModifierBundle(side_override=side)

    def on_apply(self, mgr: EffectsManager, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "charm")
        tags = set(params.get("save_tags") or ["spells"])

        ok = rules.saving_throw(mgr._dice(), target, bonus=int(params.get("save_bonus", 0) or 0), tags=tags)  # type: ignore[arg-type]
        mgr._emit("saving_throw_rolled", {"target": target.name, "tags": list(tags), "success": ok, "kind": "charm"})
        if ok:
            mgr._ui(f"{target.name} resists {src}'s charm!")
            mgr.remove(target, str(inst.get("instance_id")), reason="resisted", ctx=ctx)
            return

        charm_to = ctx.get("charm_to_side")
        if charm_to not in ("party", "foe"):
            charm_to = ctx.get("source_side") if ctx.get("source_side") in ("party", "foe") else None
        if charm_to not in ("party", "foe"):
            charm_to = "foe" if bool(getattr(target, "is_pc", False)) else "party"

        st = inst.get("state") if isinstance(inst.get("state"), dict) else {}
        st["side_override"] = str(charm_to)
        inst["state"] = st

        dur = inst.get("duration")
        if not isinstance(dur, dict) or dur.get("type") != "rounds":
            rounds = int(params.get("rounds", 0) or 0)
            if rounds <= 0:
                expr = str(params.get("duration") or "1d6")
                try:
                    rr = mgr._dice().roll(expr)  # type: ignore[union-attr]
                    rounds = int(rr.total)
                except Exception:
                    rounds = int(mgr._dice().d(6))  # type: ignore[union-attr]
            inst["duration"] = {"type": "rounds", "remaining": max(1, rounds)}
            dur = inst["duration"]

        rem = int(dur.get("remaining", 0) or 0)
        mgr._ui(f"{target.name} is charmed by {src}! ({rem} rounds)")

    def on_hook(self, mgr: EffectsManager, hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        if hook != Hook.START_OF_ROUND:
            return
        dur = inst.get("duration")
        if not isinstance(dur, dict) or dur.get("type") != "rounds":
            return
        rem = int(dur.get("remaining", 0) or 0)
        if rem <= 0:
            return
        dur["remaining"] = rem - 1
        mgr._emit("effect_ticked", {"kind": "charm", "target": target.name, "instance_id": inst.get("instance_id"), "remaining": int(dur["remaining"])})
        if dur["remaining"] <= 0:
            mgr._ui(f"{target.name} is no longer charmed.")


# --- Regeneration -------------------------------------------------------------

class RegenerationEffectDef:
    """Simple regeneration effect (e.g., Trolls)."""

    kind = "regeneration"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        return "replace"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        return "regeneration"

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        return ModifierBundle()

    def on_apply(self, mgr: EffectsManager, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        amt = int(params.get("amount", 0) or 0)
        if amt <= 0:
            amt = 1
            params["amount"] = amt
            inst["params"] = params
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "regeneration")
        mgr._ui(f"{target.name} regenerates wounds ({amt}/round).")
        dur = inst.get("duration")
        if not (isinstance(dur, dict) and dur.get("type") in ("rounds", "none")):
            inst["duration"] = {"type": "none"}

    def on_hook(self, mgr: EffectsManager, hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        # Suppression: fire/acid damage prevents the next regen tick (classic troll logic).
        if hook == Hook.ON_DAMAGE:
            dmg_types = set((ctx.get("damage_types") or []) if isinstance(ctx.get("damage_types"), list) else [])
            if "fire" in dmg_types or "acid" in dmg_types:
                state = inst.get("state") if isinstance(inst.get("state"), dict) else {}
                # Skip the next START_OF_ROUND healing.
                state["suppressed_rounds"] = max(int(state.get("suppressed_rounds", 0) or 0), 1)
                inst["state"] = state
                mgr._emit("regeneration_suppressed", {"target": target.name, "instance_id": inst.get("instance_id"), "damage_types": list(dmg_types)})
            return

        if hook != Hook.START_OF_ROUND:
            return
        if int(getattr(target, "hp", 0) or 0) <= 0:
            return
        if int(getattr(target, "hp", 0) or 0) >= int(getattr(target, "hp_max", 0) or 0):
            return

        # If suppressed, consume suppression and do not heal.
        state = inst.get("state") if isinstance(inst.get("state"), dict) else {}
        sup = int(state.get("suppressed_rounds", 0) or 0)
        if sup > 0:
            state["suppressed_rounds"] = sup - 1
            inst["state"] = state
            if sup == 1:
                mgr._ui(f"{target.name}'s regeneration is suppressed!")
            return

        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        amt = int(params.get("amount", 1) or 1)
        new_hp = min(int(target.hp_max), int(target.hp) + amt)
        healed = int(new_hp) - int(target.hp)
        if healed <= 0:
            return
        target.hp = int(new_hp)
        mgr._emit("effect_ticked", {"kind": "regeneration", "target": target.name, "instance_id": inst.get("instance_id"), "healed": healed})
        mgr._ui(f"{target.name} regenerates {healed} hp. (HP {target.hp}/{target.hp_max})")


# --- Gaze / Averting ---------------------------------------------------------

class AvertGazeEffectDef:
    """A short-lived defensive posture to avoid gaze attacks.

    While active:
    - the actor is ignored by gaze_petrification effects
    - the actor suffers -2 to hit (attacking blindly / not looking)
    """

    kind = "avert_gaze"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        return "refresh"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        return "avert_gaze"

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        return ModifierBundle(to_hit_mod=int((inst.get("params") or {}).get("to_hit_mod", -2) if isinstance(inst.get("params"), dict) else -2))

    def on_apply(self, mgr: EffectsManager, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        dur = inst.get("duration")
        if not (isinstance(dur, dict) and dur.get("type") == "rounds"):
            inst["duration"] = {"type": "rounds", "remaining": 1}
        mgr._ui(f"{target.name} averts their gaze (-2 to hit, immune to gaze this round).")

    def on_hook(self, mgr: EffectsManager, hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        # Duration decrement is handled centrally by EffectsManager so the
        # immunity lasts through the round.
        return


class GazePetrificationEffectDef:
    """An aura-like gaze effect (Medusa/Basilisk).

    Applied to the *monster* on spawn.
    Each START_OF_ROUND, forces opposing combatants who are not averting gaze
    to Save vs Petrification or become petrified.
    """

    kind = "gaze_petrification"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        return "replace"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        return "gaze_petrification"

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        return ModifierBundle()

    def on_apply(self, mgr: EffectsManager, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        # No immediate effect; it pulses each round.
        mgr._ui(f"{target.name}'s gaze is deadly!")
        dur = inst.get("duration")
        if not (isinstance(dur, dict) and dur.get("type") in ("none", "rounds")):
            inst["duration"] = {"type": "none"}

    def _is_averting(self, a: Actor, *, ctx_round_no: int) -> bool:
        insts = getattr(a, "effect_instances", None)
        if not isinstance(insts, dict):
            return False
        for e in insts.values():
            if isinstance(e, dict) and str(e.get("kind") or "").lower() == "avert_gaze":
                # still active
                dur = e.get("duration")
                if not isinstance(dur, dict) or dur.get("type") != "rounds":
                    return True
                rem = int(dur.get("remaining", 0) or 0)
                if rem > 0:
                    return True
                # Avert gaze is often set for "this round" and may be decremented
                # during the same START_OF_ROUND tick; treat it as active for the
                # round it was created.
                try:
                    created = int(e.get("created_round", 0) or 0)
                except Exception:
                    created = 0
                if rem == 0 and created == int(ctx_round_no):
                    return True
        return False

    def on_hook(self, mgr: EffectsManager, hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        if hook != Hook.START_OF_ROUND:
            return
        if int(getattr(target, "hp", 0) or 0) <= 0:
            return

        party = ctx.get("party")
        foes = ctx.get("foes")
        if not isinstance(party, list) or not isinstance(foes, list):
            return

        # Determine which side the gaze creature is on.
        if target in foes:
            victims = [a for a in party if getattr(a, "hp", 0) > 0 and 'fled' not in (getattr(a, 'effects', []) or [])]
        else:
            victims = [a for a in foes if getattr(a, "hp", 0) > 0 and 'fled' not in (getattr(a, 'effects', []) or [])]

        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        sid = str(params.get("id") or "gaze").strip() or "gaze"
        tags = set(params.get("save_tags") or ["petrification"])

        round_no = int(ctx.get("round_no") or getattr(mgr.game, "combat_round", 1) or 1)
        for v in victims:
            if getattr(v, "hp", 0) <= 0:
                continue
            # If already petrified, skip.
            if mgr.query_modifiers(v).petrified:
                continue
            if self._is_averting(v, ctx_round_no=round_no):
                continue

            # Apply petrification effect instance; PetrificationEffectDef handles the save.
            pinst = {
                "kind": "petrification",
                "source": {"name": getattr(target, "name", "gaze")},
                "created_round": int(ctx.get("round_no") or getattr(mgr.game, "combat_round", 1) or 1),
                "duration": {"type": "none"},
                "tags": ["harmful", "petrification"],
                "params": {
                    "id": f"{sid}_petrify",
                    "save_tags": list(tags),
                },
                "state": {},
            }
            mgr.apply(v, pinst, ctx=ctx)


class DiseaseEffectDef:
    """Longer-lived diseases/rots that persist across combat.

    This is the foundation for effects like Mummy Rot. The effect:
    - may deal damage when a campaign day passes
    - can block natural and/or magical healing
    - persists until explicitly cured/removed
    """

    kind = "disease"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        return f"disease:{str(params.get('id') or 'disease')}"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        # Default: refresh same disease id, allow different diseases to stack.
        return "refresh"

    def on_apply(self, mgr: EffectsManager, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        tags = set(params.get("save_tags") or ["spell"])
        if "spells" in tags and "spell" not in tags:
            tags.add("spell")
        # Disease usually offers a save (e.g., mummy rot may or may not in different tables;
        # we implement a save by default but allow forcing no-save by omitting save_tags).
        if tags:
            ok = rules.saving_throw(mgr._dice(), target, bonus=int(params.get("save_bonus", 0) or 0), tags=set(tags))
            mgr._emit("saving_throw_rolled", {"target": target.name, "tags": sorted(list(tags)), "success": bool(ok)})
            if ok:
                mgr._ui(f"{target.name} resists the affliction.")
                # Remove the effect immediately
                mgr.remove(target, str(inst.get("instance_id")), reason="save_success", ctx=ctx)
                return
        mgr._ui(f"{target.name} is afflicted with disease!")

    def on_hook(self, mgr: EffectsManager, hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        if hook != Hook.DAY_PASSED:
            return
        if getattr(target, "hp", 0) <= 0:
            return
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}

        dmg_expr = params.get("damage_per_day") or params.get("damage")
        if not dmg_expr:
            return

        # Roll damage using the game's dice parser if available; otherwise accept ints.
        dmg = 0
        try:
            if isinstance(dmg_expr, (int, float)):
                dmg = int(dmg_expr)
            else:
                d = mgr._dice()
                # Prefer Dice.roll which supports NdM(+/-X).
                if d is not None and hasattr(d, "roll"):
                    dmg = int(d.roll(str(dmg_expr)).total)
                else:
                    # Minimal parser for NdM
                    s = str(dmg_expr).lower().replace(" ", "")
                    if "d" in s:
                        n, m = s.split("d", 1)
                        n = int(n or 1)
                        m = int(m or 6)
                        dmg = max(1, n) * (m // 2)
                    else:
                        dmg = int(s)
        except Exception:
            dmg = 0

        if dmg <= 0:
            return

        target.hp = max(-1, int(getattr(target, "hp", 0)) - dmg)
        mgr._emit("disease_damage", {"target": target.name, "damage": int(dmg), "effect": inst.get("instance_id")})
        mgr._ui(f"{target.name} suffers {dmg} damage from disease. (HP {target.hp}/{target.hp_max})")

        if target.hp <= 0:
            mgr._emit("character_died", {"target": target.name, "cause": "disease"})
            mgr._ui(f"{target.name} dies of disease!")

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        return ModifierBundle(
            blocks_natural_heal=bool(params.get("blocks_natural_heal", True)),
            blocks_magical_heal=bool(params.get("blocks_magical_heal", False)),
        )

# --- Grapple / Engulf / Swallow ------------------------------------------------

class GrappleEffectDef:
    """Represents being held, pinned, or constricted.

    OSR-simple implementation:
    - On apply: saving throw can negate (default vs paralysis)
    - Each round: victim makes an automatic escape check
    - Optional constrict damage per round

    (A future patch can add a dedicated player 'Escape' combat action.)
    """

    kind = "grapple"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        return "refresh"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        gid = str(params.get("id") or "grapple")
        return gid

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        th = int(params.get("to_hit_mod", -2) or -2)
        return ModifierBundle(to_hit_mod=th)

    def on_apply(self, mgr: "EffectsManager", target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "grapple")

        # P3.6: persist stable-ish linkage for cleanup.
        try:
            smid = str(ctx.get("source_monster_id") or "")
            params.setdefault("source_key", f"{src}|{smid}".lower())
            params.setdefault("target_key", f"{target.name}|{getattr(target, 'monster_id', '')}".lower())
            inst["params"] = params
        except Exception:
            pass

        tags = set(params.get("save_tags") or ["paralysis"])
        if bool(params.get("save_on_apply", True)):
            ok = rules.saving_throw(mgr._dice(), target, bonus=int(params.get("save_bonus", 0) or 0), tags=tags)  # type: ignore[arg-type]
            mgr._emit("saving_throw_rolled", {"target": target.name, "tags": list(tags), "success": ok, "kind": "grapple"})
            if ok:
                mgr._ui(f"{target.name} avoids being grappled by {src}!")
                mgr.remove(target, str(inst.get("instance_id")), reason="resisted", ctx=ctx)
                return

        mgr._ui(f"{target.name} is grappled by {src}!")

    def on_hook(self, mgr: "EffectsManager", hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        if hook != Hook.START_OF_ROUND:
            return
        if int(getattr(target, "hp", 0) or 0) <= 0:
            return

        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "grapple")

        # Optional constrict damage.
        dmg_expr = str(params.get("damage_per_round") or "").strip()
        dmg_type = str(params.get("damage_type") or "physical").strip() or "physical"
        if dmg_expr:
            try:
                rr = mgr._dice().roll(dmg_expr)  # type: ignore[union-attr]
                dmg = int(rr.total)
            except Exception:
                dmg = int(mgr._dice().d(6))  # type: ignore[union-attr]
            if dmg > 0:
                target.hp = max(-1, int(getattr(target, "hp", 0)) - dmg)
                mgr._ui(f"{target.name} takes {dmg} {dmg_type} damage from {src}'s hold. (HP {target.hp}/{target.hp_max})")
                mgr._emit("grapple_damage", {"target": target.name, "source": src, "damage": int(dmg), "damage_type": dmg_type})
                try:
                    g = mgr.game
                    if hasattr(g, "_notify_damage"):
                        g._notify_damage(target, int(dmg), damage_types={dmg_type})
                except Exception:
                    pass

        # Automatic escape attempt.
        if not bool(params.get("auto_escape", False)):
            return
        escape_dc = int(params.get("escape_dc", 14) or 14)
        stat = str(params.get("escape_stat", "STR") or "STR").upper()
        stats = getattr(target, "stats", None)
        stat_score = 10
        try:
            if stats is not None and hasattr(stats, stat):
                stat_score = int(getattr(stats, stat) or 10)
        except Exception:
            stat_score = 10

        bonus = int(mod_ability(stat_score))
        try:
            roll = int(mgr._dice().d(20))  # type: ignore[union-attr]
        except Exception:
            roll = 10
        total = roll + bonus + int(params.get("escape_bonus", 0) or 0)
        if total >= escape_dc:
            mgr._ui(f"{target.name} breaks free of {src}!")
            mgr._emit("grapple_escaped", {"target": target.name, "source": src, "roll": int(roll), "total": int(total), "dc": int(escape_dc)})
            mgr.remove(target, str(inst.get("instance_id")), reason="escaped", ctx=ctx)


class EngulfEffectDef:
    """Engulfed: like grapple, but victim cannot act."""

    kind = "engulf"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        return "refresh"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        eid = str(params.get("id") or "engulf")
        return eid

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        return ModifierBundle(can_act=False)

    def on_apply(self, mgr: "EffectsManager", target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "engulf")

        # P3.6: persist stable-ish linkage for cleanup.
        try:
            smid = str(ctx.get("source_monster_id") or "")
            params.setdefault("source_key", f"{src}|{smid}".lower())
            params.setdefault("target_key", f"{target.name}|{getattr(target, 'monster_id', '')}".lower())
            inst["params"] = params
        except Exception:
            pass

        tags = set(params.get("save_tags") or ["paralysis"])
        if bool(params.get("save_on_apply", True)):
            ok = rules.saving_throw(mgr._dice(), target, bonus=int(params.get("save_bonus", 0) or 0), tags=tags)  # type: ignore[arg-type]
            mgr._emit("saving_throw_rolled", {"target": target.name, "tags": list(tags), "success": ok, "kind": "engulf"})
            if ok:
                mgr._ui(f"{target.name} avoids being engulfed by {src}!")
                mgr.remove(target, str(inst.get("instance_id")), reason="resisted", ctx=ctx)
                return

        mgr._ui(f"{target.name} is engulfed by {src}!")

    def on_hook(self, mgr: "EffectsManager", hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        if hook != Hook.START_OF_ROUND:
            return
        if int(getattr(target, "hp", 0) or 0) <= 0:
            return

        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "engulf")

        dmg_expr = str(params.get("damage_per_round") or "1d6").strip()
        dmg_type = str(params.get("damage_type") or "acid").strip() or "acid"

        try:
            rr = mgr._dice().roll(dmg_expr)  # type: ignore[union-attr]
            dmg = int(rr.total)
        except Exception:
            dmg = int(mgr._dice().d(6))  # type: ignore[union-attr]

        if dmg > 0:
            target.hp = max(-1, int(getattr(target, "hp", 0)) - dmg)
            mgr._ui(f"{target.name} takes {dmg} {dmg_type} damage while engulfed by {src}. (HP {target.hp}/{target.hp_max})")
            mgr._emit("engulf_damage", {"target": target.name, "source": src, "damage": int(dmg), "damage_type": dmg_type})
            try:
                g = mgr.game
                if hasattr(g, "_notify_damage"):
                    g._notify_damage(target, int(dmg), damage_types={dmg_type})
            except Exception:
                pass

        # Escape attempt.
        if not bool(params.get("auto_escape", False)):
            return
        escape_dc = int(params.get("escape_dc", 16) or 16)
        stat = str(params.get("escape_stat", "STR") or "STR").upper()
        stats = getattr(target, "stats", None)
        stat_score = 10
        try:
            if stats is not None and hasattr(stats, stat):
                stat_score = int(getattr(stats, stat) or 10)
        except Exception:
            stat_score = 10

        bonus = int(mod_ability(stat_score))
        try:
            roll = int(mgr._dice().d(20))  # type: ignore[union-attr]
        except Exception:
            roll = 10
        total = roll + bonus + int(params.get("escape_bonus", 0) or 0)
        if total >= escape_dc:
            mgr._ui(f"{target.name} forces their way out of {src}!")
            mgr._emit("engulf_escaped", {"target": target.name, "source": src, "roll": int(roll), "total": int(total), "dc": int(escape_dc)})
            mgr.remove(target, str(inst.get("instance_id")), reason="escaped", ctx=ctx)


class SwallowEffectDef:
    """Swallowed whole: victim cannot act and takes digestion damage each round."""

    kind = "swallow"

    def stacking_policy(self, inst: Dict[str, Any]) -> str:
        return "refresh"

    def stacking_key(self, inst: Dict[str, Any]) -> str:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        sid = str(params.get("id") or "swallow")
        return sid

    def get_modifiers(self, target: Actor, inst: Dict[str, Any]) -> ModifierBundle:
        return ModifierBundle(can_act=False)

    def on_apply(self, mgr: "EffectsManager", target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "swallow")

        # P3.6: persist stable-ish linkage for cleanup.
        try:
            smid = str(ctx.get("source_monster_id") or "")
            params.setdefault("source_key", f"{src}|{smid}".lower())
            params.setdefault("target_key", f"{target.name}|{getattr(target, 'monster_id', '')}".lower())
            inst["params"] = params
        except Exception:
            pass

        tags = set(params.get("save_tags") or ["paralysis"])
        if bool(params.get("save_on_apply", False)):
            ok = rules.saving_throw(mgr._dice(), target, bonus=int(params.get("save_bonus", 0) or 0), tags=tags)  # type: ignore[arg-type]
            mgr._emit("saving_throw_rolled", {"target": target.name, "tags": list(tags), "success": ok, "kind": "swallow"})
            if ok:
                mgr._ui(f"{target.name} avoids being swallowed by {src}!")
                mgr.remove(target, str(inst.get("instance_id")), reason="resisted", ctx=ctx)
                return

        mgr._ui(f"{target.name} is swallowed whole by {src}!")

    def on_hook(self, mgr: "EffectsManager", hook: str, target: Actor, inst: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        if hook != Hook.START_OF_ROUND:
            return
        if int(getattr(target, "hp", 0) or 0) <= 0:
            return

        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "swallow")

        dmg_expr = str(params.get("damage_per_round") or "2d6").strip()
        dmg_type = str(params.get("damage_type") or "acid").strip() or "acid"

        try:
            rr = mgr._dice().roll(dmg_expr)  # type: ignore[union-attr]
            dmg = int(rr.total)
        except Exception:
            dmg = int(mgr._dice().d(6))  # type: ignore[union-attr]

        if dmg > 0:
            target.hp = max(-1, int(getattr(target, "hp", 0)) - dmg)
            mgr._ui(f"{target.name} takes {dmg} {dmg_type} damage inside {src}. (HP {target.hp}/{target.hp_max})")
            mgr._emit("swallow_damage", {"target": target.name, "source": src, "damage": int(dmg), "damage_type": dmg_type})
            try:
                g = mgr.game
                if hasattr(g, "_notify_damage"):
                    g._notify_damage(target, int(dmg), damage_types={dmg_type})
            except Exception:
                pass

        if int(getattr(target, "hp", 0) or 0) <= 0:
            mgr._ui(f"{target.name} dies inside {src}!")
            mgr._emit("character_died", {"target": target.name, "cause": "swallowed"})
