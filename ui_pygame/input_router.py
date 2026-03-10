from __future__ import annotations

from sww.intents import (
    IntentCancel,
    IntentChooseAction,
    IntentEndTurn,
    IntentHoverTile,
    IntentSelectTile,
)


class InputRouter:
    """Convert pygame events into Intents.

    The app may also synthesize intents (e.g. clicking a HUD button).
    """

    def __init__(self, tile_size: int, grid_width: int, grid_height: int):
        self.tile = int(tile_size)
        self.w = int(grid_width)
        self.h = int(grid_height)

    def on_mouse_motion(self, pos):
        mx, my = pos
        gx, gy = mx // self.tile, my // self.tile
        if 0 <= gx < self.w and 0 <= gy < self.h:
            return IntentHoverTile(int(gx), int(gy))
        return None

    def on_grid_click(self, pos):
        mx, my = pos
        gx, gy = mx // self.tile, my // self.tile
        if 0 <= gx < self.w and 0 <= gy < self.h:
            return IntentSelectTile(int(gx), int(gy))
        return None

    def on_keydown(self, key, pygame):
        if key == pygame.K_ESCAPE:
            return IntentCancel()
        if key == pygame.K_l:
            return IntentChooseAction(action_id="LISTEN")
        if key == pygame.K_s:
            return IntentChooseAction(action_id="SEARCH")
        if key == pygame.K_p:
            return IntentChooseAction(action_id="PARLEY")
        if key == pygame.K_b:
            return IntentChooseAction(action_id="BRIBE")
        if key == pygame.K_t:
            return IntentChooseAction(action_id="THREATEN")
        if key == pygame.K_SPACE:
            return IntentEndTurn()
        if key == pygame.K_1:
            return IntentChooseAction("MOVE")
        if key == pygame.K_2:
            return IntentChooseAction("MELEE")
        if key == pygame.K_3:
            return IntentChooseAction("MISSILE")
        if key == pygame.K_4:
            return IntentChooseAction("COVER")
        if key == pygame.K_h:
            return IntentChooseAction("HIDE")
        if key == pygame.K_5:
            return IntentChooseAction("CAST")
        if key == pygame.K_6:
            return IntentChooseAction("MOVE_MELEE")
        if key == pygame.K_7:
            return IntentChooseAction("OPEN_DOOR")
        if key == pygame.K_8:
            return IntentChooseAction("FORCE_DOOR")
        if key == pygame.K_9:
            return IntentChooseAction("SPIKE_DOOR")
        if key == pygame.K_0:
            return IntentChooseAction("THROW_ITEM")
        return None
