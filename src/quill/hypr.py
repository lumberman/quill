"""Hyprland integration.

Everything here goes through Omarchy's Lua dispatch layer. Note that
`hyprctl dispatch <arg>` wraps the argument as `return hl.dispatch(<arg>)`,
so the argument must be a Lua *expression* evaluating to a dispatcher.
"""

from __future__ import annotations

import json
import subprocess
import time

# Omarchy tags windows dynamically; dynamic tags carry a trailing "*".
_TERMINAL_TAG = "terminal"


def _run(args: list[str], timeout: float = 3.0) -> str:
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
        return out.stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def _json(args: list[str]):
    raw = _run(args)
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def active_window() -> dict | None:
    """The focused window, or None when the desktop itself has focus."""
    win = _json(["hyprctl", "activewindow", "-j"])
    if isinstance(win, dict) and win.get("address"):
        return win
    return None


def cursor_pos() -> tuple[int, int]:
    """Global cursor position in logical pixels."""
    raw = _run(["hyprctl", "cursorpos"]).strip()
    try:
        x, y = raw.split(",")
        return int(x), int(y)
    except ValueError:
        return 0, 0


def monitors() -> list[dict]:
    mons = _json(["hyprctl", "monitors", "-j"])
    return mons if isinstance(mons, list) else []


def monitor_at(x: int, y: int) -> dict | None:
    """The monitor containing a global point, falling back to the focused one."""
    mons = monitors()
    for m in mons:
        mx, my = m.get("x", 0), m.get("y", 0)
        # Hyprland reports physical width/height; divide by scale for logical size.
        scale = m.get("scale") or 1.0
        w = int(m.get("width", 0) / scale)
        h = int(m.get("height", 0) / scale)
        if mx <= x < mx + w and my <= y < my + h:
            return m
    for m in mons:
        if m.get("focused"):
            return m
    return mons[0] if mons else None


def is_terminal(win: dict | None) -> bool:
    """Terminals need CTRL+Insert / SHIFT+Insert instead of CTRL+C / CTRL+V.

    Reuses Omarchy's `terminal` window tag so there is one definition of what
    counts as a terminal (see default/hypr/bindings/clipboard.lua).
    """
    if not win:
        return False
    for tag in win.get("tags") or []:
        if tag.rstrip("*") == _TERMINAL_TAG:
            return True
    return False


def dispatch(expr: str) -> bool:
    """Run a Lua dispatcher expression. Returns True when Hyprland accepted it."""
    out = _run(["hyprctl", "dispatch", expr])
    return "error" not in out.lower()


def _lua_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def send_shortcut(mods: str, key: str, hold_s: float = 0.05) -> None:
    """Inject a chord into whatever surface currently has keyboard focus.

    Uses send_key_state's explicit down/up split rather than send_shortcut:
    Hyprland's send_shortcut can leave synthetic key state stuck or repeating
    (hyprwm/Hyprland#14099), and Omarchy's own clipboard bindings work around
    it the same way.

    The window target is deliberately omitted so the chord also reaches focused
    layer-shell surfaces, not just normal windows.
    """
    spec = f'{{ mods = {_lua_str(mods)}, key = {_lua_str(key)}, state = %s }}'
    dispatch(f"hl.dsp.send_key_state({spec % _lua_str('down')})")
    time.sleep(hold_s)
    dispatch(f"hl.dsp.send_key_state({spec % _lua_str('up')})")


def focus_window(address: str) -> bool:
    """Refocus a window by address, with the pre-Lua dispatcher as a fallback."""
    if not address:
        return False
    if dispatch(f'hl.dsp.focus({{ window = "address:{address}" }})'):
        return True
    return bool(_run(["hyprctl", "dispatch", "focuswindow", f"address:{address}"]))


def notify(summary: str, body: str = "", urgency: str = "normal") -> None:
    args = [
        "notify-send",
        "-a", "Quill",
        "-u", urgency,
        "-i", "accessories-text-editor",
        summary,
    ]
    if body:
        args.append(body)
    _run(args)


# Modifier bits as Hyprland reports them in `hyprctl binds -j`.
_MODS = [(64, "Super"), (4, "Ctrl"), (8, "Alt"), (1, "Shift")]
_MOUSE_NAMES = {"mouse:272": "Left-click", "mouse:273": "Right-click",
                "mouse:274": "Middle-click"}


def _format_chord(modmask: int, key: str) -> str:
    parts = [name for bit, name in _MODS if modmask & bit]
    parts.append(_MOUSE_NAMES.get(key, key))
    return " + ".join(parts)


def binds_matching(needle: str) -> list[tuple[str, str]]:
    """(chord, description) for bindings whose description mentions `needle`.

    Read live rather than hardcoded, so the settings window stays truthful when
    the user rebinds something in bindings.lua.
    """
    binds = _json(["hyprctl", "binds", "-j"])
    if not isinstance(binds, list):
        return []
    out = []
    seen = set()
    for bind in binds:
        description = str(bind.get("description") or "")
        if needle.lower() not in description.lower():
            continue
        chord = _format_chord(int(bind.get("modmask") or 0),
                             str(bind.get("key") or ""))
        if chord in seen:
            continue
        seen.add(chord)
        # Strip the "Quill: " prefix; the group heading already says Quill.
        label = description.split(":", 1)[1].strip() if ":" in description else description
        out.append((chord, label))
    return out
