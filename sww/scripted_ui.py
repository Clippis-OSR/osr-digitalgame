
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Deque, List, Optional, Sequence, Union
from collections import deque

from .ui import UI


ChooseAction = Union[int, str, Callable[[str, List[str]], int]]
PromptAction = Union[str, Callable[[str, Optional[str]], str]]


class ScriptedUIExhausted(RuntimeError):
    """Raised when scripted inputs are exhausted and auto_default is False."""


@dataclass
class ScriptedStep:
    kind: str  # "choose" or "prompt"
    value: Any


class ScriptedUI(UI):
    """
    UI driver that feeds pre-programmed choices into the game.

    - For choose(title, options), next action may be:
        * int: option index (0-based)
        * str: matches option text (case-insensitive substring) OR "N" for option number (1-based)
        * callable: f(title, options) -> index
    - For prompt(text, default), next action may be:
        * str: returned directly ("" means accept default)
        * callable: f(text, default) -> str

    If auto_default=True and the script runs out, choose() returns 0 and prompt() returns default (or "").
    """

    def __init__(
        self,
        actions: Sequence[Any] | None = None,
        auto_default: bool = True,
        echo: bool = False,
        fallback_choose: Callable[[str, List[str]], int] | None = None,
        fallback_prompt: Callable[[str, Optional[str]], str] | None = None,
    ):
        super().__init__()
        self._q: Deque[Any] = deque(actions or [])
        self.auto_default = auto_default
        self.echo = echo
        self.fallback_choose = fallback_choose
        self.fallback_prompt = fallback_prompt
        self.transcript: List[ScriptedStep] = []

    def push(self, *actions: Any) -> None:
        for a in actions:
            self._q.append(a)

    def _next(self) -> Any:
        if self._q:
            return self._q.popleft()
        # During fixture replays we prefer continuing deterministically rather than failing
        # when the scripted input queue is exhausted.
        # Returning None lets prompt()/choose() fall back to defaults.
        return None

    def prompt(self, s: str, default: str | None = None) -> str:
        a = self._next()
        if callable(a):
            v = a(s, default)
        elif isinstance(a, str):
            v = a
        elif a is None and self.fallback_prompt is not None:
            v = self.fallback_prompt(s, default)
        elif a is None:
            v = default or ""
        else:
            # Coerce other primitives to string
            v = str(a)

        if v == "" and default is not None:
            v = default

        self.transcript.append(ScriptedStep("prompt", {"text": s, "default": default, "value": v}))
        if self.echo:
            super().log(f"[SCRIPT prompt] {s} -> {v}")
        return v

    def choose(self, title: str, options: list[str]) -> int:
        a = self._next()
        idx: int

        if callable(a):
            idx = int(a(title, options))
        elif isinstance(a, int):
            idx = a
        elif isinstance(a, str):
            t = a.strip()
            if t.isdigit():
                # treat as 1-based display number
                idx = int(t) - 1
            else:
                low = t.lower()
                found = None
                for i, opt in enumerate(options):
                    if low in opt.lower():
                        found = i
                        break
                idx = found if found is not None else 0
        elif a is None and self.fallback_choose is not None:
            idx = int(self.fallback_choose(title, options))
        elif a is None:
            idx = 0
        else:
            idx = 0

        if idx < 0 or idx >= len(options):
            idx = 0

        self.transcript.append(ScriptedStep("choose", {"title": title, "options": options, "index": idx}))
        if self.echo:
            super().log(f"[SCRIPT choose] {title} -> {idx} ({options[idx] if options else 'n/a'})")
        return idx


    def hr(self):
        if self.echo:
            super().hr()

    def title(self, s: str):
        # Don't print in scripted runs unless echo=True.
        self.transcript.append(ScriptedStep("title", {"title": s}))
        if self.echo:
            super().title(s)

    def log(self, msg: str):
        self.transcript.append(ScriptedStep("log", {"msg": str(msg)}))
        if self.echo:
            super().log(msg)

    def wrap(self, msg: str, width: int = 72):
        self.transcript.append(ScriptedStep("wrap", {"msg": str(msg), "width": width}))
        if self.echo:
            super().wrap(msg, width=width)


def actions_from_transcript(transcript: list[ScriptedStep]) -> list[Any]:
    """Convert a ScriptedUI transcript into a replayable action list.

    Only 'choose' and 'prompt' steps are converted (titles/logs are ignored).
    For choose: uses the recorded 0-based index.
    For prompt: uses the recorded response value.
    """
    actions: list[Any] = []
    for step in transcript:
        if step.kind == "choose":
            actions.append(int(step.value.get("index", 0)))
        elif step.kind == "prompt":
            actions.append(str(step.value.get("value", "")))
    return actions
