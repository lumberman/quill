"""Name, mark and tagline, in one place."""

from __future__ import annotations

from pathlib import Path

from . import __version__

NAME = "Quill"
TAGLINE = "Rewrite or spellcheck any text, without leaving the app"

#: What it is, in the two or three words that go under the wordmark. The
#: shell's panels all carry one of these -- "UNTANGLING WIRES" under
#: Bluetooth, "MAX 5X" under Claude Code.
SHORT = "AI text checker"

#: Read from the package rather than repeated here: the two had already
#: drifted, so the settings header was showing 1.0.0 while the package said
#: 0.1.0.
VERSION = __version__

_ICON = Path(__file__).resolve().parent.parent.parent / "share" / "icons" / "quill.svg"


def icon_path() -> str | None:
    """The mark, or None so callers fall back to a stock icon name."""
    return str(_ICON) if _ICON.exists() else None
