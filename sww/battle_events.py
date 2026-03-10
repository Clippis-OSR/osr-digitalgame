from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BattleEvent:
    """Typed tactical event for UI + replay.

    Keep this small and stable. Use `kind` + `data` so schema can evolve without
    breaking older replays (unknown keys can be ignored).
    """

    kind: str
    data: dict[str, object] = field(default_factory=dict)


def evt(kind: str, **data: object) -> BattleEvent:
    return BattleEvent(kind=kind, data=dict(data))


def format_event(e: BattleEvent) -> str:
    """Human-readable formatting for HUD log."""

    k = e.kind
    d = e.data

    if k == "TURN_STARTED":
        return f"Turn: {d.get('unit_id', '?')}"
    if k == "TURN_ENDED":
        return f"{d.get('unit_id', '?')} ends turn."

    if k == "INITIATIVE_ROLL":
        die = d.get("die", "?")
        p = d.get("party", "?")
        f = d.get("foes", "?")
        first = d.get("first", "?")
        return f"Initiative (1d{die}): Party {p} vs Foes {f} ({'Party' if first == 'party' else 'Foes'} act first)"
    if k == "PHASE_STARTED":
        ph = str(d.get("phase","?")).capitalize()
        return f"Phase: {ph}"
    if k == "SIDE_TURN":
        side = d.get("side","?")
        ph = d.get("phase")
        label = "Party" if side == "party" else "Foes"
        if ph:
            return f"{label} act ({ph})."
        return f"{label} act."
    if k == "ACTION_BLOCKED":
        who = d.get("actor","?")
        reason = d.get("reason","?")
        detail = d.get("detail")
        if detail:
            return f"{who}: blocked — {reason} ({detail})"
        return f"{who}: blocked — {reason}"
    if k == "ACTION_MODIFIER":
        who = d.get("actor","?")
        reason = d.get("reason","modifier")
        val = d.get("value")
        tgt = d.get("target")
        if tgt is not None and val is not None:
            return f"{who} → {tgt}: {reason} {val:+}"
        if val is not None:
            return f"{who}: {reason} {val:+}"
        return f"{who}: {reason}"
    if k == "ROUND_STATE":
        # Compact state panel
        lines = []
        dist = d.get("distance_ft")
        if dist is not None:
            lines.append(f"State (range {dist} ft):")
        else:
            lines.append("State:")
        party = d.get("party") or []
        foes = d.get("foes") or []
        def _fmt(u):
            nm = u.get("name","?")
            hp = u.get("hp","?")
            hpmax = u.get("hp_max")
            eff = u.get("effects") or []
            eff_s = ""
            if eff:
                eff_s = " [" + ", ".join(eff[:4]) + ("" if len(eff)<=4 else ", …") + "]"
            if hpmax is not None:
                return f"  {nm} HP {hp}/{hpmax}{eff_s}"
            return f"  {nm} HP {hp}{eff_s}"
        if party:
            lines.append(" Party:")
            for u in party:
                lines.append(_fmt(u))
        if foes:
            lines.append(" Enemies:")
            for u in foes:
                lines.append(_fmt(u))
        return "\n".join(lines)
    if k == "MOVED":
        return f"{d.get('unit_id', '?')} moved {d.get('tiles', 0)} tile(s)."
    if k == "TAKE_COVER":
        return f"{d.get('unit_id', '?')} takes cover."
    if k == "ATTACK":
        mode = d.get("mode", "")
        res = d.get("result", None)
        rr = f" [{res}]" if res else ""
        mm = f" ({mode})" if mode else ""
        roll = d.get("roll", None)
        total = d.get("total", None)
        need = d.get("need", None)
        rt = ""
        if roll is not None and total is not None and need is not None:
            rt = f" (roll {roll} → {total} vs {need})"
        elif roll is not None:
            rt = f" (roll {roll})"
        return f"{d.get('attacker_id', '?')} attacks {d.get('target_id', '?')}{mm}{rr}{rt}."
    if k == "DAMAGE":
        hp = d.get("hp", None)
        hpmax = d.get("hp_max", None)
        tail = ""
        if hp is not None and hpmax is not None:
            tail = f" (HP {hp}/{hpmax})"
        return f"{d.get('target_id', '?')} takes {d.get('amount', 0)} damage.{tail}"

    if k == "HEAL":
        hp = d.get("hp", None)
        hpmax = d.get("hp_max", None)
        tail = ""
        if hp is not None and hpmax is not None:
            tail = f" (HP {hp}/{hpmax})"
        return f"{d.get('target_id', '?')} heals {d.get('amount', 0)} HP.{tail}"
    if k == "SAVE_THROW":
        spell = d.get("spell", None)
        roll = d.get("roll", None)
        need = d.get("need", None)
        res = d.get("result", None)
        rr = f" [{res}]" if res else ""
        rt = ""
        if roll is not None and need is not None:
            rt = f" (roll {roll} vs {need})"
        sp = f" vs {spell}" if spell else ""
        return f"{d.get('unit_id','?')} saves ({d.get('save','?')}){sp}.{rr}{rt}"

    if k == "UNIT_DIED":
        return f"{d.get('unit_id', '?')} is slain!"
    if k == "OPPORTUNITY_ATTACK_TRIGGERED":
        return f"{d.get('attacker_id', '?')} makes an opportunity attack on {d.get('target_id', '?')}."
    if k == "SPELL_CAST":
        return f"{d.get('caster_id', '?')} casts {d.get('spell', '?')}."
    if k == "AOE_TILES":
        return f"{d.get('spell', '?')} affects {d.get('count', 0)} tile(s)."

    if k == "AOE_AFFECTS":
        dmg = d.get("dmg", None)
        dt = f" for {dmg}" if dmg is not None else ""
        return f"{d.get('spell','?')} affects {d.get('count',0)} target(s){dt}."

    if k == "ROUND_STARTED":
        return f"Round {d.get('round_no', '?')} (initiative: {d.get('side_first', '?')} first)."
    if k == "PHASE_STARTED":
        return f"Phase: {d.get('phase', '?')}"
    if k == "ACTION_DECLARED":
        return f"{d.get('unit_id','?')} declares {d.get('action','?')}."
    if k == "SPELL_DISRUPTED":
        return f"{d.get('caster_id','?')}'s spell is disrupted!"

    if k == "SPELL_RESOLVED":
        return f"{d.get('caster_id','?')}'s {d.get('spell','?')} takes effect."
    if k == "SPELL_FAILED":
        return f"{d.get('caster_id','?')}'s {d.get('spell','?')} fails."
    if k == "SPELL_IMMUNE":
        return f"{d.get('target_id','?')} is unaffected by {d.get('spell','?')}."
    if k == "MAGIC_RESISTED":
        roll = d.get("roll", None)
        pct = d.get("pct", None)
        rt = ""
        if roll is not None and pct is not None:
            rt = f" (d%={roll} ≤ {pct})"
        return f"{d.get('target_id','?')} resists {d.get('spell','?')}!{rt}"



    if k == "MORALE_CHECK":
        return f"Morale check ({d.get('side','?')}): roll {d.get('roll','?')} vs {d.get('target','?')} ({d.get('reason','?')})."
    if k == "MORALE_PASSED":
        return f"{d.get('side','?')} holds!"
    if k == "MORALE_FAILED":
        return f"{d.get('side','?')} fails morale: {d.get('result','?')}."
    if k == "UNIT_FALLBACK":
        return f"{d.get('unit_id','?')} falls back!"
    if k == "UNIT_ROUTED":
        return f"{d.get('unit_id','?')} routs!"
    if k == "UNIT_SURRENDERED":
        return f"{d.get('unit_id','?')} surrenders!"

    if k == "DOOR_CONTACT":
        lb = bool(d.get("listened_bonus", False))
        return "Door contact! " + ("(You listened first.)" if lb else "")

    if k == "SURPRISE_ROLL":
        ctx = str(d.get("context", "") or "")
        return f"Surprise ({ctx}) PC d6={d.get('pc_roll','?')} Foe d6={d.get('foe_roll','?')} => {d.get('result','?')}"
    if k == "DOOR_OPENED":
        return f"{d.get('unit','?')} opens a door."
    if k == "DOOR_FORCE_ATTEMPT":
        return f"{d.get('unit','?')} forces door: {d.get('total','?')} vs DC {d.get('dc','?')}."
    if k == "DOOR_FORCED_OPEN":
        return f"{d.get('unit','?')} forces the door open!"
    if k == "DOOR_SPIKED":
        return f"{d.get('unit','?')} spikes a door."
    if k == "ITEM_THROWN":
        return f"{d.get('unit','?')} throws {d.get('item','?')}."
    if k == "EFFECT_APPLIED":
        return f"{d.get('tgt','?')} gains {d.get('kind','?')}."
    if k == "EFFECT_REMOVED":
        return f"{d.get('tgt','?')} loses {d.get('kind','?')}."
    if k == "EFFECT_EXPIRED":
        return f"{d.get('tgt','?')}'s {d.get('kind','?')} ends."
    if k == "TERRAIN_CHANGED":
        return f"Terrain changed at ({d.get('x','?')},{d.get('y','?')}): {d.get('tile','?')}."
    if k in ("XP_AWARDED","XP_EARNED"):
        # XP_EARNED may be emitted per-foe; XP_AWARDED may be pooled.
        if "xp_total" in d:
            return f"XP awarded: {d.get('xp_total',0)} total ({d.get('per_pc',0)} each)."
        if "xp" in d and "unit_id" in d:
            return f"XP earned: {d.get('xp',0)} ({d.get('unit_id')})."
        return f"XP earned: {d.get('xp',0)}."
    if k == "LOOT_GAINED":
        return f"Loot gained: {d.get('gp',0)} gp."
    if k == "COMBAT_ENDED":
        r = d.get("reason", None)
        if r:
            return f"Combat ended ({r})."
        return "Combat ended."


    if k == "REACTION_ROLL":
        return f"Reaction: roll {d.get('roll','?')} => {d.get('stance','?')}."
    if k == "REACTION_BROKEN":
        return f"Parley broken ({d.get('reason','?')}). Foes turn hostile!"

    if k == "SOCIAL_AUTO_HOSTILE":
        return f"{str(d.get('kind','parley')).title()}: Chaos-aligned foes refuse — hostile!"
    if k == "SOCIAL_AUTO_FRIENDLY":
        return f"{str(d.get('kind','parley')).title()}: Law-aligned foes are amenable — friendly."

    if k == "SOCIAL_ROLL":
        kind = str(d.get("kind", "parley"))
        spent = int(d.get("spent_gp", 0) or 0)
        sp = f" (-{spent}gp)" if spent else ""
        return f"{kind.title()}{sp}: 2d6={d.get('roll','?')} +{d.get('bonus','?')} => {d.get('result','?')}"
    if k == "HIDDEN":
        return f"{d.get('unit_id','?')} slips into the shadows."
    if k == "HIDE_FAILED":
        r = d.get('roll', None)
        if r is None:
            return f"{d.get('unit_id','?')} cannot hide ({d.get('reason','?')})."
        return f"{d.get('unit_id','?')} fails to hide (d6={r})."
    if k == "DETECTED":
        return f"{d.get('unit_id','?')} is spotted!"
    if k == "AMBUSH":
        return f"Ambush! {d.get('attacker_id','?')} gains +{d.get('bonus',0)} to hit."
    if k == "AMMO_SPENT":
        who = d.get("actor","?")
        label = d.get("ammo","?")
        rem = d.get("remaining", None)
        if rem is not None:
            return f"{who} uses 1 {label} (remaining {rem})."
        return f"{who} uses 1 {label}."
    if k == "AMMO_RECOVERED":
        label = d.get("ammo","?")
        n = d.get("recovered",0)
        return f"Recovered {n} {label} after the fight."
    if k == "SPELL_SPENT":
        who = d.get("caster_id","?")
        sp = d.get("spell","?")
        rem = d.get("remaining", None)
        if rem is not None:
            return f"{who} expends {sp} (prepared left {rem})."
        return f"{who} expends {sp}."
    if k == "COMBAT_SUMMARY":
        lines = ["Combat summary:"]
        reason = d.get("reason")
        if reason:
            lines.append(f" Result: {reason}")
        party = d.get("party") or []
        if party:
            lines.append(" Party:")
            for u in party:
                nm = u.get("name","?")
                hp = u.get("hp","?")
                hpmax = u.get("hp_max")
                delta = u.get("delta")
                ds = f" ({delta:+})" if isinstance(delta,int) and delta!=0 else ""
                if hpmax is not None:
                    lines.append(f"  {nm} HP {hp}/{hpmax}{ds}")
                else:
                    lines.append(f"  {nm} HP {hp}{ds}")
        ammo = d.get("ammo_deltas") or []
        if ammo:
            lines.append(" Resources:")
            for a in ammo:
                lbl=a.get("ammo","?")
                spent=a.get("spent",0)
                rec=a.get("recovered",0)
                if spent or rec:
                    tail=[]
                    if spent: tail.append(f"spent {spent}")
                    if rec: tail.append(f"recovered {rec}")
                    lines.append(f"  {lbl}: " + ", ".join(tail))
        spells = d.get("spells_spent") or []
        if spells:
            lines.append(" Spells used:")
            for s in spells:
                lines.append(f"  {s.get('caster','?')}: {s.get('spell','?')}")
        foes = d.get("foes") or {}
        if foes:
            lines.append(f" Enemies: defeated {foes.get('defeated',0)}, fled {foes.get('fled',0)}, surrendered {foes.get('surrendered',0)}")
        xp = d.get('xp_earned', 0) or 0
        try:
            xp = int(xp)
        except Exception:
            xp = 0
        if xp:
            lines.append(f" XP earned: {xp}.")
        loot_gp = d.get('loot_gp', 0) or 0
        try:
            loot_gp = int(loot_gp)
        except Exception:
            loot_gp = 0
        loot_items = d.get('loot_items') or []
        if loot_gp or loot_items:
            lines.append(" Loot:")
            if loot_gp:
                lines.append(f"  {loot_gp} gp")
            if loot_items:
                # Keep it short; items already added to party inventory.
                preview = list(loot_items)[:6]
                tail = "" if len(loot_items) <= 6 else f" (+{len(loot_items)-6} more)"
                lines.append("  Items: " + ", ".join([str(x) for x in preview]) + tail)
        return "\n".join(lines)



    if k == "INFO":
        return str(d.get("text", ""))
    return f"{k}: {d}"
