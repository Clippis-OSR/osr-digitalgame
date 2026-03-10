from __future__ import annotations


def draw_grid(pygame, screen, gm, tile, colors):
    for y in range(gm.height):
        for x in range(gm.width):
            r = pygame.Rect(x * tile, y * tile, tile, tile)
            t = gm.get(x, y)
            if t in ("wall","wall_secret"):
                pygame.draw.rect(screen, colors.wall, r)
            elif t.startswith("door_"):
                # Doors: closed variants drawn like walls; open doors as floor with outline.
                if t == "door_open":
                    pygame.draw.rect(screen, colors.bg, r)
                    pygame.draw.rect(screen, colors.wall, r, 2)
                else:
                    pygame.draw.rect(screen, colors.door, r)
            elif t == "cover":
                pygame.draw.rect(screen, colors.cover, r)
            elif t == "rubble":
                pygame.draw.rect(screen, colors.rubble, r)
            pygame.draw.rect(screen, colors.grid, r, 1)


def draw_units(pygame, screen, state, tile, colors, selected_unit_id: str | None):
    for u in state.units.values():
        if not u.is_alive():
            continue
        # Hide hidden foes from the player view.
        if getattr(u, 'hidden', False) and u.side == 'foe':
            continue
        r = pygame.Rect(u.x * tile + 4, u.y * tile + 4, tile - 8, tile - 8)
        c = colors.pc if u.side == "pc" else colors.foe
        pygame.draw.rect(screen, c, r)
        if selected_unit_id == u.unit_id:
            pygame.draw.rect(screen, colors.sel, r, 2)


def draw_overlays(pygame, screen, state, ui_model, tile, colors):
    # Light overlay: dim tiles not illuminated.
    lit = getattr(ui_model, "lit_tiles", set()) or set()
    if lit:
        dark = pygame.Surface((tile, tile), pygame.SRCALPHA)
        dark.fill((0, 0, 0, 160))
        for y in range(state.gm.height):
            for x in range(state.gm.width):
                if (x, y) in lit:
                    continue
                screen.blit(dark, (x * tile, y * tile))

    # Movement range.
    for (x, y) in ui_model.move_tiles:
        r = pygame.Rect(x * tile + 2, y * tile + 2, tile - 4, tile - 4)
        pygame.draw.rect(screen, colors.overlay, r, 1)
    # Path preview.
    for (x, y) in ui_model.preview_path:
        r = pygame.Rect(x * tile + 6, y * tile + 6, tile - 12, tile - 12)
        pygame.draw.rect(screen, colors.overlay, r, 2)

    # Spell AOE preview (tiles affected by hovered spell target)
    for (x, y) in getattr(ui_model, 'spell_preview_tiles', set()) or set():
        r = pygame.Rect(x * tile + 5, y * tile + 5, tile - 10, tile - 10)
        pygame.draw.rect(screen, colors.spell, r, 2)

# Valid targets highlight (tile containing a valid target unit_id)
    if ui_model.valid_targets:
        for tid in ui_model.valid_targets:
            u = state.units.get(tid)
            if not u or not u.is_alive():
                continue
            x, y = u.pos
            r = pygame.Rect(x * tile + 3, y * tile + 3, tile - 6, tile - 6)
            pygame.draw.rect(screen, colors.target, r, 2)
