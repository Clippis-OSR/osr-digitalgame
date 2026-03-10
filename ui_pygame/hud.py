from __future__ import annotations

from .widgets import Button
from sww.battle_events import format_event


class HUD:
    """Sidebar HUD: action buttons + unit info + log."""

    def __init__(self, pygame, x0: int, width: int, height: int, font, big_font):
        self.pygame = pygame
        self.x0 = int(x0)
        self.width = int(width)
        self.height = int(height)
        self.font = font
        self.big = big_font
        self.buttons: list[Button] = []
        self.spell_buttons: list[Button] = []
        self.item_buttons: list[Button] = []
        self._build_buttons()

    def _build_buttons(self):
        pg = self.pygame
        x = self.x0 + 10
        y = 70
        w = self.width - 20
        h = 28
        gap = 8
        self.buttons = [
            Button(pg.Rect(x, y + (h + gap) * 0, w, h), "1 Move", "MOVE"),
            Button(pg.Rect(x, y + (h + gap) * 1, w, h), "2 Melee", "MELEE"),
            Button(pg.Rect(x, y + (h + gap) * 2, w, h), "3 Missile", "MISSILE"),
            Button(pg.Rect(x, y + (h + gap) * 3, w, h), "4 Cover", "COVER"),
            Button(pg.Rect(x, y + (h + gap) * 4, w, h), "H Hide", "HIDE"),
            Button(pg.Rect(x, y + (h + gap) * 5, w, h), "5 Cast", "CAST"),
            Button(pg.Rect(x, y + (h + gap) * 6, w, h), "6 Move+Melee", "MOVE_MELEE"),
            Button(pg.Rect(x, y + (h + gap) * 7, w, h), "7 Open Door", "OPEN_DOOR"),
            Button(pg.Rect(x, y + (h + gap) * 8, w, h), "8 Force Door", "FORCE_DOOR"),
            Button(pg.Rect(x, y + (h + gap) * 9, w, h), "9 Spike Door", "SPIKE_DOOR"),
            Button(pg.Rect(x, y + (h + gap) * 10, w, h), "0 Throw Item", "THROW_ITEM"),
            Button(pg.Rect(x, y + (h + gap) * 11, w, h), "L Listen", "LISTEN"),
            Button(pg.Rect(x, y + (h + gap) * 12, w, h), "S Search", "SEARCH"),
            Button(pg.Rect(x, y + (h + gap) * 13, w, h), "P Parley", "PARLEY"),
            Button(pg.Rect(x, y + (h + gap) * 14, w, h), "B Bribe", "BRIBE"),
            Button(pg.Rect(x, y + (h + gap) * 15, w, h), "T Threaten", "THREATEN"),
            Button(pg.Rect(x, self.height - 52, w, h), "Space End/Pass", "END_TURN"),
        ]

    def handle_click(self, pos):
        mx, my = pos
        for b in self.item_buttons:
            if b.hit(mx, my):
                return b.action_id
        for b in self.spell_buttons:
            if b.hit(mx, my):
                return b.action_id
        for b in self.buttons:
            if b.hit(mx, my):
                return b.action_id
        return None

    def draw(self, screen, state, controller, colors):
        pg = self.pygame
        # panel bg
        pg.draw.rect(screen, (12, 12, 12), pg.Rect(self.x0, 0, self.width, self.height))

        y = 10

        def blit(text, f=None):
            nonlocal y
            f = f or self.font
            s = f.render(text, True, (230, 230, 230))
            screen.blit(s, (self.x0 + 10, y))
            y += s.get_height() + 6

        blit("SWW Tactical (P4.5)", self.big)
        blit("Keys: 1-6 + H hide | Space end", self.font)

        # Round / phase panel
        blit(" ")
        blit(f"Round: {getattr(state, 'round_no', 1)}")
        blit(f"Phase: {getattr(state, 'phase', '?')}")
        blit(f"Initiative: {getattr(state, 'side_first', '?')} first")

        blit(" ")
        blit("Declare order:")
        order = list(getattr(state, 'declare_order', []) or [])
        d_idx = int(getattr(state, 'declare_index', 0) or 0)
        for idx, uid in enumerate(order[:12]):
            prefix = ">" if (getattr(state, 'phase', '') == 'DECLARE' and idx == d_idx) else " "
            u = state.units.get(uid)
            name = getattr(getattr(u, 'actor', None), 'name', '') if u else ''
            side = getattr(u, 'side', '') if u else ''
            blit(f"{prefix} {uid} {name} [{side}]")

        # Buttons
        for b in self.buttons:
            pg.draw.rect(screen, (25, 25, 25), b.rect)
            pg.draw.rect(screen, (60, 60, 60), b.rect, 1)
            s = self.font.render(b.label, True, (230, 230, 230))
            screen.blit(s, (b.rect.x + 8, b.rect.y + 6))

        y = max(y, self.buttons[-1].rect.bottom + 14)
        blit(" ")

        active = state.active_unit()
        if active:
            blit(f"Active: {active.unit_id} ({active.side})")
            blit(f"HP: {getattr(active.actor, 'hp', 0)}/{getattr(active.actor, 'hp_max', getattr(active.actor, 'hp', 0))}")
            blit(f"Move: {active.move_remaining}  Act: {active.actions_remaining}")
            blit(f"Action: {controller.sel.action_id}")

        # Spell picker (shown when controller is in SELECT_SPELL)
        self.spell_buttons = []
        self.item_buttons = []
        if getattr(controller, "node", "") == "DECLARE_SELECT_SPELL" and active:
            blit(" ")
            blit("Prepared spells:")
            prepared = list(getattr(active.actor, "spells_prepared", []) or [])
            # Render up to 8 spells as clickable rows.
            yy = y
            for i, sp in enumerate(prepared[:8]):
                r = pg.Rect(self.x0 + 10, yy, self.width - 20, 24)
                self.spell_buttons.append(Button(r, str(sp), f"SPELL:{sp}"))
                pg.draw.rect(screen, (25, 25, 25), r)
                pg.draw.rect(screen, (60, 60, 60), r, 1)
                s = self.font.render(str(sp), True, (230, 230, 230))
                screen.blit(s, (r.x + 8, r.y + 4))
                yy += 26
            y = yy + 6


        if getattr(controller, "node", "") == "DECLARE_SELECT_ITEM" and active:
            blit(" ")
            blit("Select item to throw:")
            items = [
                ("oil", "Oil Flask", getattr(getattr(controller, "game", None), "oil_flasks", 0)),
                ("holy_water", "Holy Water", getattr(getattr(controller, "game", None), "holy_water", 0)),
                ("net", "Net", getattr(getattr(controller, "game", None), "nets", 0)),
                ("caltrops", "Caltrops", getattr(getattr(controller, "game", None), "caltrops", 0)),
            ]
            yy = y
            for (iid, label, count) in items:
                r = pg.Rect(self.x0 + 10, yy, self.width - 20, 24)
                self.item_buttons.append(Button(r, f"{label} ({int(count)})", f"ITEM:{iid}"))
                pg.draw.rect(screen, (25, 25, 25), r)
                pg.draw.rect(screen, (60, 60, 60), r, 1)
                s = self.font.render(f"{label} ({int(count)})", True, (230, 230, 230))
                screen.blit(s, (r.x + 8, r.y + 4))
                yy += 26
            y = yy + 6

        blit(" ")
        sel_id = controller.sel.selected_unit_id
        if sel_id and sel_id in state.units:
            u = state.units[sel_id]
            blit(f"Selected: {u.unit_id}")
            blit(f"Name: {getattr(u.actor, 'name', '')}")
            st = getattr(u.actor, "status", {}) or {}
            cov = st.get("cover", 0)
            if cov:
                blit(f"Cover: {cov}")

        # Log tail (typed BattleEvents)
        y = self.height - 160
        pg.draw.rect(screen, (16, 16, 16), pg.Rect(self.x0 + 10, y, self.width - 20, 150))
        pg.draw.rect(screen, (40, 40, 40), pg.Rect(self.x0 + 10, y, self.width - 20, 150), 1)
        yy = y + 6
        tail = state.events[-7:]
        for e in tail:
            line = format_event(e)
            s = self.font.render(line[:52], True, (210, 210, 210))
            screen.blit(s, (self.x0 + 16, yy))
            yy += s.get_height() + 4
