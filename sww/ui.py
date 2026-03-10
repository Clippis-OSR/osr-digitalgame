\
from __future__ import annotations
import sys
import textwrap

class UI:
    def __init__(self):
        self.auto_combat = False
        self.auto_style = "balanced"
        # Optional hook: if set, called as on_log(msg) -> bool.
        # If it returns True, UI.log will suppress printing.
        self.on_log = None

    def clear_screen(self):
        try:
            if sys.stdout.isatty():
                print("\033[2J\033[H", end="")
        except Exception:
            pass

    def hr(self):
        print("-"*72)

    def title(self, s: str):
        self.hr()
        print(s)
        self.hr()

    def prompt(self, s: str, default: str | None = None) -> str:
        if default is not None:
            s = f"{s} [{default}] "
        else:
            s = f"{s} "
        v = input(s).strip()
        return v if v else (default or "")

    def choose(self, title: str, options: list[str]) -> int:
        self.title(title)
        for i,opt in enumerate(options, start=1):
            print(f"{i}. {opt}")
        while True:
            v = self.prompt("Choose:", None)
            if v.isdigit() and 1 <= int(v) <= len(options):
                return int(v)-1
            print("Invalid choice.")

    def log(self, msg: str):
        handled = False
        if self.on_log is not None:
            try:
                handled = bool(self.on_log(str(msg)))
            except Exception:
                handled = False
        if not handled:
            print(msg)

    def wrap(self, msg: str, width: int = 72):
        print(textwrap.fill(msg, width=width))
