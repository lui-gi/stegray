"""Terminal rendering of analyzer results."""

import os
from collections.abc import Sequence
from typing import Any, TextIO

# Lines of a tool's output shown on a terminal before eliding the rest.
MAX_TTY_LINES = 40
MAX_LISTED_FILES = 20

# What a result amounts to for the reader, derived from its status and content.
FOUND = "found"
OUTPUT = "output"
NOTHING = "nothing"
FAILED = "failed"

_BOLD = "1"
_DIM = "2"
_RED = "31"
_GREEN = "1;32"
_CYAN = "36"

# 256-color codes handed out to the tools in turn, so each one keeps its own color
# across runs. Red and green are left out: they mean failed and found.
_TOOL_COLORS = tuple(
    f"38;5;{n}"
    for n in (39, 208, 141, 179, 73, 204, 105, 143, 37, 173, 177, 110, 215, 67, 169, 187, 99, 137)
)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def outcome_of(result: dict[str, Any]) -> str:
    """Classify a result as found, plain output, nothing found or failed."""
    status = result.get("status", "failed")
    if status == "ok":
        if result.get("extracted") or result.get("note"):
            return FOUND
        return OUTPUT if result.get("output") else NOTHING
    return NOTHING if status == "error" else FAILED


class Reporter:
    """Print analyzer results as readable sections."""

    def __init__(
        self,
        stream: TextIO,
        *,
        color: bool | None = None,
        full: bool = False,
        name_width: int = 0,
        tools: Sequence[str] = (),
    ) -> None:
        """Configure colors and truncation from the stream unless overridden.

        ``tools`` lists every analyzer in display order; a tool's position picks its color.
        """
        self.stream = stream
        tty = stream.isatty()
        self.color = (tty and "NO_COLOR" not in os.environ) if color is None else color
        # Piped output is never elided so `stegray f | grep flag` sees everything.
        self.full = full or not tty
        # Pads the names of one-line sections so their labels line up.
        self.name_width = name_width
        self._tool_colors = {
            name: _TOOL_COLORS[index % len(_TOOL_COLORS)] for index, name in enumerate(tools)
        }
        # Color of the vertical line, which follows the tool of the current section.
        self._bar = _DIM
        self._outcomes: dict[str, list[str]] = {}
        # Whether the last section was a single line, so a run of them stays together.
        self._compact = False

    def _paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def _tool(self, name: str, style: str, *, width: int = 0) -> str:
        """Paint a tool's name in its own color on top of ``style``."""
        color = self._tool_colors.get(name)
        return self._paint(f"{name:<{width}}", f"{style};{color}" if color else style)

    def _print(self, text: str = "") -> None:
        print(text, file=self.stream)

    def header(self, title: str, details: dict[str, str]) -> None:
        """Print the run header."""
        self._print(self._paint(title, _BOLD))
        for key, value in details.items():
            self._print(f"{self._paint(f'{key}:', _DIM)} {value}")

    def _lines(self, output: str | list[str] | dict[str, str]) -> list[str]:
        if isinstance(output, dict):
            pad = max((len(key) for key in output), default=0)
            return [f"{key:<{pad}} : {value}" for key, value in output.items()]
        if isinstance(output, str):
            return output.strip("\n").splitlines()
        return [str(line) for line in output]

    def _gutter(self, text: str = "", *, last: bool = False, code: str | None = None) -> None:
        """Print one body line hanging off the section's vertical line."""
        bar = self._paint("╰" if last else "│", self._bar)
        self._print(f"{bar} {self._paint(text, code) if code else text}".rstrip())

    def _body(self, lines: list[str]) -> None:
        shown = lines if self.full else lines[:MAX_TTY_LINES]
        for line in shown:
            self._gutter(line)
        if len(shown) < len(lines):
            hidden = len(lines) - len(shown)
            self._gutter(f"… {hidden} more lines (use --full to show them)", code=_DIM)

    def _title(self, name: str, outcome: str, result: dict[str, Any]) -> str:
        if outcome == NOTHING:
            padded = self._tool(name, _DIM, width=self.name_width)
            return f"{self._paint('·', _DIM)} {padded}  {self._paint('nothing found', _DIM)}"
        if outcome == FAILED:
            padded = self._tool(name, _BOLD, width=self.name_width)
            return f"{self._paint('✗', _RED)} {padded}  {self._paint('failed', _RED)}"
        title = self._tool(name, _BOLD)
        if outcome == FOUND:
            extracted = result.get("extracted")
            label = f"extracted {_count(len(extracted), 'file')}" if extracted else "found"
            return f"{title}  {self._paint(f'★ {label}', _GREEN)}"
        output = result.get("output")
        if isinstance(output, dict):
            return f"{title}  {self._paint(_count(len(output), 'field'), _DIM)}"
        return title

    def section(self, name: str, result: dict[str, Any]) -> None:
        """Print one analyzer's result."""
        outcome = outcome_of(result)
        self._bar = self._tool_colors.get(name, _DIM)
        extracted: list[str] = result.get("extracted") or []
        entry = self._tool(name, _DIM if outcome == NOTHING else _BOLD)
        if outcome == FOUND and extracted:
            entry += f" ({_count(len(extracted), 'file')})"
        self._outcomes.setdefault(outcome, []).append(entry)

        lines = self._lines(result.get("error") or []) + self._lines(result.get("output") or [])
        # A tool that found nothing only says so; its own words are kept for --full.
        if outcome == NOTHING and not self.full:
            lines = []
        compact = not lines and outcome in (NOTHING, FAILED)
        if not (compact and self._compact):
            self._print()
        self._compact = compact

        self._print(self._title(name, outcome, result))
        self._body(lines)
        if result.get("note"):
            self._gutter(f"note: {result['note']}", last=not extracted, code=_CYAN)
        listed = extracted[:MAX_LISTED_FILES]
        more = len(extracted) - len(listed)
        for index, path in enumerate(listed):
            self._gutter(f"→ {path}", last=not more and index == len(listed) - 1)
        if more:
            self._gutter(f"… {more} more", last=True, code=_DIM)

    def summary(self, skipped: dict[str, str]) -> None:
        """Print which analyzers found something, found nothing, failed or did not run."""
        rows: list[tuple[str, str, str]] = [
            (label, ", ".join(self._outcomes[outcome]), code)
            for outcome, label, code in (
                (FOUND, "found", _GREEN),
                (NOTHING, "nothing", _DIM),
                (FAILED, "failed", _RED),
            )
            if outcome in self._outcomes
        ]
        by_reason: dict[str, list[str]] = {}
        for name, reason in skipped.items():
            by_reason.setdefault(reason.split(":")[0], []).append(name)
        # Only the first skip reason carries the label; the others line up under it.
        for index, (reason, names) in enumerate(by_reason.items()):
            rows.append(("" if index else "skipped", f"{', '.join(names)} ({reason})", _DIM))
        if not rows:
            return
        self._bar = _DIM
        self._print()
        self._print(self._paint("summary", _BOLD))
        pad = max(len(label) for label, _, _ in rows)
        for index, (label, text, code) in enumerate(rows):
            line = f"{self._paint(f'{label:<{pad}}', code)}  {text}"
            self._gutter(line, last=index == len(rows) - 1)
