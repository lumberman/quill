"""Name, mark and tagline, in one place."""

from __future__ import annotations

from pathlib import Path

NAME = "Quill"
TAGLINE = "Rewrite or spellcheck any text, without leaving the app"

#: What it is, in the two or three words that go under the wordmark. The
#: shell's panels all carry one of these -- "UNTANGLING WIRES" under
#: Bluetooth, "MAX 5X" under Claude Code.
SHORT = "AI text checker"

#: Kept in step with shell-plugin/manifest.json, which is the only other
#: place a Quill version is written down.
VERSION = "1.0.0"

_ICON = Path(__file__).resolve().parent.parent.parent / "share" / "icons" / "quill.svg"


def icon_path() -> str | None:
    """The mark, or None so callers fall back to a stock icon name."""
    return str(_ICON) if _ICON.exists() else None
