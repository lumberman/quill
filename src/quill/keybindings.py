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
MOD_ORDER = ("SUPER", "CTRL", "ALT", "SHIFT")

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


def quill_command(argument: str) -> str:
    """The command a bind should run, using the installed launcher."""
    launcher = Path.home() / ".local" / "bin" / "quill"
    exe = str(launcher) if launcher.exists() else "quill"
    return f"{exe} {argument}"


def action_id(binding: Binding) -> str | None:
    """The edit a binding runs, for `quill run <id>` binds."""
    match = re.search(r"\brun\s+([\w.-]+)\s*$", binding.command)
    return match.group(1) if match else None


def add(chord: str, description: str, command: str,
        path: Path | None = None) -> None:
    """Append a bind inside Quill's block. Raises OSError on failure."""
    path = path or bindings_path()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    bounds = _block_bounds([line.rstrip("\n") for line in lines])
    if bounds is None:
        raise OSError("Quill's block is missing from bindings.lua — run ./install.sh")

    shutil.copy(path, f"{path}.bak.{time.strftime('%Y%m%d-%H%M%S')}-before-quill-edit")
    entry = f'o.bind("{chord}", "{description}", "{command}")\n'
    lines.insert(bounds[1], entry)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    tmp.replace(path)


def remove(binding: Binding, path: Path | None = None) -> None:
    """Delete one bind line. Raises OSError if the file moved underneath."""
    path = path or bindings_path()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    bounds = _block_bounds([line.rstrip("\n") for line in lines])
    if bounds is None or not (bounds[0] < binding.line < bounds[1]):
        raise OSError("that binding is no longer inside Quill's block")
    match = _BIND_RE.match(lines[binding.line].rstrip("\n"))
    if not match or match["chord"] != binding.chord:
        raise OSError("bindings.lua changed since it was read; reopen settings")

    shutil.copy(path, f"{path}.bak.{time.strftime('%Y%m%d-%H%M%S')}-before-quill-edit")
    del lines[binding.line]
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


# Hyprland names mouse buttons by their evdev code; nobody thinks in those.
_MOUSE_LABELS = {
    "mouse:272": "Left-click",
    "mouse:273": "Right-click",
    "mouse:274": "Middle-click",
    "mouse:275": "Back button",
    "mouse:276": "Forward button",
    "mouse_up": "Wheel up",
    "mouse_down": "Wheel down",
}

def pretty_key(part: str) -> str:
    """A key name a human can read: "mouse:273" -> "Right-click"."""
    return _MOUSE_LABELS.get(part.strip().lower(), part)


def pretty(chord: str) -> str:
    """A whole chord a human can read, keeping the "+" separators."""
    return " + ".join(pretty_key(p.strip()) for p in chord.split("+") if p.strip())


def is_pointer(chord: str) -> bool:
    return "mouse" in chord.lower()


def has_modifiers(state) -> bool:
    return bool(_mods_from_state(state))


def _mods_from_state(state) -> list[str]:
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
    return [m for m in MOD_ORDER if m in mods]


def chord_from_event(keyval: int, state) -> str | None:
    """Turn a GTK key press into a Hyprland chord, or None if it is only mods."""
    from gi.repository import Gdk

    ordered = _mods_from_state(state)
    name = Gdk.keyval_name(keyval) or ""
    # A modifier on its own is not a chord yet; the user is still holding keys.
    if not name or name in (
        "Super_L", "Super_R", "Control_L", "Control_R",
        "Alt_L", "Alt_R", "Shift_L", "Shift_R", "ISO_Level3_Shift",
    ):
        return None

    key = name.upper() if len(name) == 1 else name
    return " + ".join(ordered + [key])


def is_bound_elsewhere(chord: str, own: Binding) -> str | None:
    """The description of a conflicting Hyprland binding, if there is one.

    Checked against the compositor rather than the file, so bindings from
    Omarchy's own defaults are caught too.
    """
    import json

    def norm(text: str) -> str:
        return " + ".join(p.strip().upper() for p in text.split("+") if p.strip())

    if norm(chord) == norm(own.chord):
        return None            # unchanged; it cannot clash with itself

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
        return description or "another binding"
    return None
