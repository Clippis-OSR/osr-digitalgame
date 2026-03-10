from __future__ import annotations

import os
from dataclasses import dataclass


def _safe_import_pygame():
    try:
        import pygame  # type: ignore

        return pygame
    except Exception as e:
        raise SystemExit(
            "pygame is required for the grid prototype. Install pygame-ce (recommended) or pygame.\n"
            f"Import error: {e}"
        )


@dataclass
class Colors:
    bg: tuple[int, int, int] = (18, 18, 18)
    grid: tuple[int, int, int] = (40, 40, 40)
    wall: tuple[int, int, int] = (90, 90, 90)
    door: tuple[int, int, int] = (100, 85, 55)
    cover: tuple[int, int, int] = (70, 60, 40)
    rubble: tuple[int, int, int] = (50, 50, 35)
    pc: tuple[int, int, int] = (80, 140, 240)
    foe: tuple[int, int, int] = (220, 90, 90)
    sel: tuple[int, int, int] = (240, 220, 100)
    overlay: tuple[int, int, int] = (80, 200, 120)
    target: tuple[int, int, int] = (240, 140, 80)
    spell: tuple[int, int, int] = (160, 120, 240)


def run_pygame() -> int:
    pygame = _safe_import_pygame()

    from sww.game import Game
    from sww.ui_headless import HeadlessUI
    from sww.grid_map import GridMap
    from sww.grid_state import GridBattleState, UnitState
    from sww.turn_controller import TurnController
    from sww.ui_model import compute_ui_model
    from ui_pygame.input_router import InputRouter
    from ui_pygame.hud import HUD
    from ui_pygame import renderers

    # Deterministic seeds for a repeatable demo.
    os.environ.setdefault("STRICT_CONTENT", "true")

    ui = HeadlessUI()
    game = Game(ui)
    # Demo supplies
    setattr(game, "oil_flasks", getattr(game, "oil_flasks", 0) or 2)
    setattr(game, "holy_water", getattr(game, "holy_water", 0) or 1)
    setattr(game, "nets", getattr(game, "nets", 0) or 1)
    setattr(game, "caltrops", getattr(game, "caltrops", 0) or 1)

    # Build a small default party without prompts.
    _make_demo_party(game)

    # Build a small demo map.
    gm = GridMap.empty(20, 15)
    _decorate_demo_map(gm)

    state = GridBattleState(gm=gm)
    # Place party on left.
    pcs = list(game.party.living())
    for i, pc in enumerate(pcs):
        state.add_unit(UnitState(unit_id=f"pc:{i}", actor=pc, side="pc", x=2, y=3 + i * 2, move_remaining=6, light_radius=6))
    # Place 3 orcs on right.
    orcs = _make_monsters(game, "orc", 3)
    for i, m in enumerate(orcs):
        state.add_unit(UnitState(unit_id=f"foe:{i}", actor=m, side="foe", x=16, y=4 + i * 3, move_remaining=6))
    state.setup_default_initiative()

    controller = TurnController(game, state)

    # Pygame init.
    pygame.init()
    tile = 32
    sidebar = 320
    screen = pygame.display.set_mode((gm.width * tile + sidebar, gm.height * tile))
    pygame.display.set_caption("SWW Tactical (P4.4)")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 18)
    big = pygame.font.SysFont(None, 22)
    colors = Colors()

    router = InputRouter(tile, gm.width, gm.height)
    hud = HUD(pygame, gm.width * tile, sidebar, gm.height * tile, font, big)

    running = True
    while running:
        clock.tick(60)
        # AI auto-step.
        controller.step_ai_if_needed()
        state.remove_dead_from_initiative()

        ui_model = compute_ui_model(state, controller.sel.selected_unit_id, controller.sel.hovered_tile, controller.sel.action_id)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                break
            if event.type == pygame.KEYDOWN:
                it = router.on_keydown(event.key, pygame)
                if it:
                    controller.handle_intent(it)
                if event.key == pygame.K_ESCAPE:
                    running = False
                    break

            if event.type == pygame.MOUSEMOTION:
                it = router.on_mouse_motion(event.pos)
                if it:
                    controller.handle_intent(it)

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my = event.pos
                if mx >= gm.width * tile:
                    # HUD click.
                    act = hud.handle_click(event.pos)
                    if isinstance(act, str) and act.startswith("ITEM:"):
                        from sww.intents import IntentChooseItem

                        controller.handle_intent(IntentChooseItem(act.split(":",1)[1]))
                    elif isinstance(act, str) and act.startswith("SPELL:"):
                        from sww.intents import IntentChooseSpell

                        controller.handle_intent(IntentChooseSpell(act.split(":",1)[1]))
                    elif act == "END_TURN":
                        from sww.intents import IntentEndTurn

                        controller.handle_intent(IntentEndTurn())
                    elif act:
                        from sww.intents import IntentChooseAction

                        controller.handle_intent(IntentChooseAction(act))
                else:
                    it = router.on_grid_click(event.pos)
                    if it:
                        controller.handle_intent(it)

        # Render
        screen.fill(colors.bg)
        renderers.draw_grid(pygame, screen, gm, tile, colors)
        renderers.draw_overlays(pygame, screen, state, ui_model, tile, colors)
        renderers.draw_units(pygame, screen, state, tile, colors, controller.sel.selected_unit_id)
        hud.draw(screen, state, controller, colors)
        pygame.display.flip()

    pygame.quit()
    return 0


def _decorate_demo_map(gm):
    # Simple walls border.
    for x in range(gm.width):
        gm.set(x, 0, "wall")
        gm.set(x, gm.height - 1, "wall")
    for y in range(gm.height):
        gm.set(0, y, "wall")
        gm.set(gm.width - 1, y, "wall")
    # A wall spine.
    for y in range(2, gm.height - 2):
        if y in (5, 9):
            # doorways
            gm.set(10, y, "door_closed")
            continue
        gm.set(10, y, "wall")
    # Cover near the gap.
    for (x, y) in [(8, 5), (8, 9), (12, 5), (12, 9)]:
        gm.set(x, y, "cover")
    # Rubble (difficult terrain) around the center.
    for (x, y) in [(6, 7), (7, 7), (8, 7), (11, 7), (12, 7), (13, 7)]:
        gm.set(x, y, "rubble")


def _make_demo_party(game):
    # Minimal non-interactive party.
    game.party.members = []
    # Use deterministic 3d6 rolls.
    classes = ["Fighter", "Cleric", "Thief", "Magic-User"]
    names = ["Aster", "Bram", "Cleo", "Dain"]
    weapons = ["Sword", "Mace", "Dagger", "Staff"]
    armors = ["Chain", "Chain", "Leather", "None"]
    shields = [True, True, False, False]

    for i in range(4):
        cls = classes[i]
        stats = game.roll_stats_3d6()
        hd = game.class_hit_die(cls)
        from sww.game import PC, mod_ability

        hp = max(1, game.dice.d(hd) + max(0, mod_ability(stats.CON)))
        pc = PC(
            name=names[i],
            hp=hp,
            hp_max=hp,
            ac_desc=9,
            hd=1,
            save=15,
            morale=9,
            alignment="Neutrality",
            is_pc=True,
            cls=cls,
            level=1,
            xp=0,
            stats=stats,
        )
        try:
            game.recalc_pc_class_features(pc)
        except Exception:
            pass
        pc.effects = []
        pc.weapon = weapons[i]
        pc.armor = None if armors[i] == "None" else armors[i]
        pc.shield = bool(shields[i])
        try:
            from sww.equipment import compute_ac_desc

            pc.ac_desc = compute_ac_desc(9, pc.armor, pc.shield, getattr(stats, "DEX", None))
        except Exception:
            pass
        # Demo prepared spells for the Magic-User (grid casting).
        if cls == "Magic-User":
            pc.spells_prepared = ["Sleep", "Magic Missile", "Fireball"]
        game.party.members.append(pc)

    game.gold = 50
    game.torches = 3
    game.rations = 3
    game.campaign_day = 1


def _make_monsters(game, monster_id: str, count: int):
    m = game.content.get_monster(monster_id)
    out = []
    for _ in range(count):
        out.append(game.encounters._mk_monster(m))
    return out

