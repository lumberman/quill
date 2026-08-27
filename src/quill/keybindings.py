"""Reading and rewriting Quill's own keybindings in bindings.lua.

Only the marked block is ever touched. Everything outside it belongs to the
user, and a settings window has no business rewriting it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

MARK_START = "-- >>> quill >>>"
MARK_END = "-- <<< quill <<<"

# Hyprland's modifier names, in the order Omarchy writes them.
_MOD_ORDER = ("SUPER", "CTRL", "ALT", "SHIFT")

_BIND_RE = re.compile(
    r'^(?P<indent>\s*)o\.bind\(\s*"(?P<chord>[^"]+)"\s*,\s*'
    r'"(?P<desc>[^"]*)"\s*,\s*"(?P<cmd>[^"]*)"\s*\)\s*$'
)


@dataclass(frozen=True)
class Binding:
    chord: str
    description: str
    command: str
    line: int          # index within the file, for a precise rewrite

    @property
    def label(self) -> str:
        """Description without the "Quill: " prefix."""
        return self.description.split(":", 1)[1].strip() if ":" in self.description \
            else self.description


def bindings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "hypr" / "bindings.lua"


def _block_bounds(lines: list[str]) -> tuple[int, int] | None:
    start = end = None
    for index, line in enumerate(lines):
        if MARK_START in line:
            start = index
        elif MARK_END in line and start is not None:
            end = index
            break
    return (start, end) if start is not None and end is not None else None


def read(path: Path | None = None) -> list[Binding]:
    path = path or bindings_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    bounds = _block_bounds(lines)
    if bounds is None:
        return []
    start, end = bounds

    found = []
    for index in range(start + 1, end):
        match = _BIND_RE.match(lines[index])
        if match:
            found.append(Binding(match["chord"], match["desc"], match["cmd"], index))
    return found


def set_chord(binding: Binding, chord: str, path: Path | None = None) -> None:
    """Rewrite one bind line in place. Raises OSError on failure."""
    path = path or bindings_path()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    bounds = _block_bounds([line.rstrip("\n") for line in lines])
    if bounds is None or not (bounds[0] < binding.line < bounds[1]):
        raise OSError("that binding is no longer inside Quill's block")

    match = _BIND_RE.match(lines[binding.line].rstrip("\n"))
    if not match or match["chord"] != binding.chord:
        raise OSError("bindings.lua changed since it was read; reopen settings")

    shutil.copy(path, f"{path}.bak.{time.strftime('%Y%m%d-%H%M%S')}-before-quill-edit")
    lines[binding.line] = (
        f'{match["indent"]}o.bind("{chord}", "{match["desc"]}", '
        f'"{match["cmd"]}")\n'
    )
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    tmp.replace(path)


def reload() -> bool:
    try:
        out = subprocess.run(["hyprctl", "reload"], capture_output=True,
                             text=True, timeout=10, check=False)
        return "error" not in (out.stdout + out.stderr).lower()
    except (subprocess.SubprocessError, OSError):
        return False


def chord_from_event(keyval: int, state) -> str | None:
    """Turn a GTK key press into a Hyprland chord, or None if it is only mods."""
    from gi.repository import Gdk

    mods = []
    if state & Gdk.ModifierType.SUPER_MASK:
        mods.append("SUPER")
    if state & Gdk.ModifierType.CONTROL_MASK:
        mods.append("CTRL")
    if state & Gdk.ModifierType.ALT_MASK:
        mods.append("ALT")
    if state & Gdk.ModifierType.SHIFT_MASK:
        mods.append("SHIFT")

    name = Gdk.keyval_name(keyval) or ""
    # A modifier on its own is not a chord yet; the user is still holding keys.
    if not name or name in (
        "Super_L", "Super_R", "Control_L", "Control_R",
        "Alt_L", "Alt_R", "Shift_L", "Shift_R", "ISO_Level3_Shift",
    ):
        return None

    key = name.upper() if len(name) == 1 else name
    ordered = [m for m in _MOD_ORDER if m in mods]
    return " + ".join(ordered + [key])


def is_bound_elsewhere(chord: str, own: Binding) -> str | None:
    """The description of a conflicting Hyprland binding, if there is one.

    Checked against the compositor rather than the file, so bindings from
    Omarchy's own defaults are caught too.
    """
    import json

    modmask = 0
    bits = {"SHIFT": 1, "CTRL": 4, "ALT": 8, "SUPER": 64}
    parts = [p.strip() for p in chord.split("+")]
    for part in parts[:-1]:
        modmask |= bits.get(part.upper(), 0)
    key = parts[-1]

    try:
        raw = subprocess.run(["hyprctl", "binds", "-j"], capture_output=True,
                             text=True, timeout=10, check=False).stdout
        entries = json.loads(raw)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None

    for entry in entries:
        if int(entry.get("modmask") or 0) != modmask:
            continue
        if str(entry.get("key") or "").lower() != key.lower():
            continue
        description = str(entry.get("description") or "")
        if description == own.description:
            continue        # that is this very binding
        return description or "another binding"
    return None
