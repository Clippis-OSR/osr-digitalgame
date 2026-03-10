from __future__ import annotations


class Button:
    """Tiny immediate-mode button helper for pygame."""

    def __init__(self, rect, label: str, action_id: str):
        self.rect = rect
        self.label = label
        self.action_id = action_id

    def hit(self, mx: int, my: int) -> bool:
        return self.rect.collidepoint(mx, my)
