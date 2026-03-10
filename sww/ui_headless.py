from __future__ import annotations


class HeadlessUI:
    """A non-interactive UI for simulations/fixtures/GUI front-ends.

    The core engine calls UI methods for logging and prompting. In a GUI
    prototype we don't want blocking prompts; this UI provides safe defaults
    and collects logs.
    """

    def __init__(self):
        self.lines: list[str] = []
        # Optional hook: if set, called as on_log(msg) -> bool.
        # If it returns True, HeadlessUI.log will suppress storing.
        self.on_log = None

    # ---- Rendering / logging

    def clear_screen(self) -> None:
        return None

    def title(self, text: str) -> None:
        self.lines.append(f"== {text} ==")

    def hr(self) -> None:
        self.lines.append("-")

    def log(self, text: str) -> None:
        handled = False
        if self.on_log is not None:
            try:
                handled = bool(self.on_log(str(text)))
            except Exception:
                handled = False
        if not handled:
            self.lines.append(str(text))

    # ---- Input (never block)
    def prompt(self, question: str, default: str | None = None) -> str:
        # Always return default; GUI can supply inputs by calling engine methods.
        return "" if default is None else str(default)

    def choose(self, prompt: str, options: list[str]) -> int:
        # Deterministic default: first option.
        return 0

    def menu(self, title: str, options: list[str]) -> int:
        # Deterministic default: first option.
        return 0
