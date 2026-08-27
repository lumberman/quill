"""Name, mark and tagline, in one place."""

from __future__ import annotations

from pathlib import Path

NAME = "Quill"
TAGLINE = "Rewrite or spellcheck any text, without leaving the app"

_ICON = Path(__file__).resolve().parent.parent.parent / "share" / "icons" / "quill.svg"


def icon_path() -> str | None:
    """The mark, or None so callers fall back to a stock icon name."""
    return str(_ICON) if _ICON.exists() else None
