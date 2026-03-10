\
from __future__ import annotations
from .data import load_json, sha256_text
from .models import Spell
from .validation import validate_monster_library, validate_content_schemas
from .monster_ai import validate_ai_profiles
from .version import ENGINE_NAME, ENGINE_VERSION

class Content:
    def __init__(self):
        # Build stamping for save compatibility / debugging.
        self.engine_name = ENGINE_NAME
        self.engine_version = ENGINE_VERSION

        # Content hashes (data lockdown). These are recorded in saves.
        self.content_hashes = {
            "equipment_general.json": sha256_text("equipment_general.json"),
            "monster_saves.json": sha256_text("monster_saves.json"),
            "turn_undead.json": sha256_text("turn_undead.json"),
            "monsters.json": sha256_text("monsters.json"),
            "spells.json": sha256_text("spells.json"),
            "races.json": sha256_text("races.json"),
        }

        self.equipment_general = load_json("equipment_general.json")
        self.races = load_json("races.json")
        self.monster_saves = load_json("monster_saves.json")
        # P4.10.6: prefer compiled turn-undead table when available.
        try:
            self.turn_undead = load_json("turn_undead_compiled.json")
            self.content_hashes["turn_undead_compiled.json"] = sha256_text("turn_undead_compiled.json")
        except Exception:
            self.turn_undead = load_json("turn_undead.json")
        self.monsters = load_json("monsters.json")
        spells_raw = load_json("spells.json")
        self.spells = [Spell(**s) for s in spells_raw]

        # P4.10.4: canonical compiled spell lists (optional, but preferred for determinism)
        try:
            self.spell_lists_compiled = load_json("spell_lists_compiled_v1.json")
            self.content_hashes["spell_lists_compiled_v1.json"] = sha256_text("spell_lists_compiled_v1.json")
        except Exception:
            self.spell_lists_compiled = None

        # Optional: structured monster specials overrides (used for audit passes like poison).
        try:
            self.monster_specials = load_json("monster_specials.json")
            self.content_hashes["monster_specials.json"] = sha256_text("monster_specials.json")
        except Exception:
            self.monster_specials = {}

        # Optional: monster damage profiles (P2.9+).
        # These drive immunities/resistances/vulnerabilities and magic-weapon gating.
        try:
            self.monster_damage_profiles = load_json("monster_damage_profiles.json")
            self.content_hashes["monster_damage_profiles.json"] = sha256_text("monster_damage_profiles.json")
        except Exception:
            self.monster_damage_profiles = {}

        # P3.5: Monster AI behavior profiles (optional)
        try:
            self.monster_ai_profiles = load_json("monster_ai_profiles.json")
            self.content_hashes["monster_ai_profiles.json"] = sha256_text("monster_ai_profiles.json")
        except Exception:
            self.monster_ai_profiles = {}

        # P3.6: lightweight schema validation for JSON-backed content.
        validate_content_schemas(
            monsters=self.monsters,
            spells=spells_raw,
            monster_damage_profiles=self.monster_damage_profiles,
            monster_specials=self.monster_specials,
            monster_ai_profiles=self.monster_ai_profiles,
            strict=True,
        )

        # P3.0: referential integrity check for monster library
        validate_monster_library(self.monsters, self.monster_specials, self.monster_damage_profiles, strict=True)

        # P3.5: validate AI profile schema (shape and enums).
        ai_probs = validate_ai_profiles(self.monster_ai_profiles)
        if ai_probs:
            raise ValueError("Monster AI profiles validation failed:\n" + "\n".join(ai_probs))

        # P3.1.15: build monster indices (stable monster_id + name + aliases)
        self._monster_by_id = {}
        self._monster_by_name = {}
        for m in (self.monsters or []):
            if not isinstance(m, dict):
                continue
            mid = str(m.get('monster_id') or '').strip().lower()
            nm = str(m.get('name') or '').strip().lower()
            if mid:
                self._monster_by_id[mid] = m
            if nm:
                self._monster_by_name[nm] = m
            for a in (m.get('aliases') or []):
                al = str(a).strip().lower()
                if al and al not in self._monster_by_name:
                    self._monster_by_name[al] = m

    def get_spell(self, ident: str) -> Spell | None:
        """Resolve a spell by stable id, name, or alias.

        Saves and prepared spell lists may contain either spell_ids (preferred)
        or historical display names (legacy). This resolver keeps both working.
        """
        ident_l = str(ident or "").strip().lower()
        if not ident_l:
            return None

        # 1) Exact id match
        for s in self.spells:
            if str(getattr(s, "spell_id", "")).strip().lower() == ident_l:
                return s

        # 2) Name match
        for s in self.spells:
            if str(getattr(s, "name", "")).strip().lower() == ident_l:
                return s

        # 3) Alias match
        for s in self.spells:
            for a in (getattr(s, "aliases", []) or []):
                if str(a).strip().lower() == ident_l:
                    return s
        return None

    def find_spell(self, name: str) -> Spell | None:
        # Backwards compat
        return self.get_spell(name)

    def canonical_spell_id(self, ident: str) -> str:
        sp = self.get_spell(ident)
        return getattr(sp, "spell_id", ident) if sp else ident

    def spell_display(self, ident: str) -> str:
        sp = self.get_spell(ident)
        return getattr(sp, "name", ident) if sp else ident

    def get_monster(self, ident: str) -> dict | None:
        """Resolve a monster by monster_id, name, or alias."""
        ident_l = str(ident or '').strip().lower()
        if not ident_l:
            return None
        if ident_l in getattr(self, '_monster_by_id', {}):
            return self._monster_by_id[ident_l]
        return getattr(self, '_monster_by_name', {}).get(ident_l)

    def canonical_monster_id(self, ident: str) -> str:
        m = self.get_monster(ident)
        if not m:
            return ident
        mid = str(m.get('monster_id') or '').strip()
        return mid or ident

    def monster_display(self, ident: str) -> str:
        m = self.get_monster(ident)
        return str(m.get('name')) if m else ident

    def list_spells_for_class(self, class_name: str) -> list[Spell]:
        """Return the spell list for a class.

        P4.10.4 prefers canonical compiled spell lists when present, falling back
        to legacy parsing of Spell.spell_level strings.
        """
        # 1) Canonical compiled lists
        try:
            comp = self.spell_lists_compiled or {}
            classes = comp.get("classes") if isinstance(comp, dict) else None
            if isinstance(classes, dict) and class_name in classes:
                levels = classes.get(class_name) or {}
                # Flatten in spell-level order, preserving canonical ordering within each level.
                out: list[Spell] = []
                for lvl in sorted((int(k) for k in levels.keys()), key=int):
                    for ent in (levels.get(str(lvl)) or []):
                        sid = str((ent or {}).get("spell_id") or "").strip()
                        if not sid:
                            continue
                        sp = self.get_spell(sid)
                        if sp:
                            out.append(sp)
                return out
        except Exception:
            pass

        # 2) Legacy fallback: parse spell_level field like "Cleric, 7th Level; Magic-User, 5th Level"
        class_l = class_name.lower()
        out: list[Spell] = []
        for s in self.spells:
            if class_l in s.spell_level.lower():
                out.append(s)
        return out
