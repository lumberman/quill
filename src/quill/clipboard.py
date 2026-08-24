"""Selection capture and in-place replacement.

Reading the selection is done without touching the clipboard where possible:
the PRIMARY selection is set by the act of selecting text in nearly every
toolkit, so it costs nothing and destroys nothing. The synthetic-copy path is
only a fallback for apps that do not export PRIMARY (some Electron builds).
"""

from __future__ import annotations

import subprocess
import time

from . import hypr

_TEXT_HINTS = ("text/plain", "text/", "string", "utf8_string")


def _wl_paste(primary: bool = False) -> str:
    args = ["wl-paste", "--no-newline"]
    if primary:
        args.append("--primary")
    try:
        res = subprocess.run(args, capture_output=True, timeout=3, check=False)
    except (subprocess.SubprocessError, OSError):
        return ""
    if res.returncode != 0:
        return ""
    return res.stdout.decode("utf-8", errors="replace")


def _has_text(primary: bool = False) -> bool:
    """Whether the given selection currently offers a text mime type."""
    args = ["wl-paste", "--list-types"]
    if primary:
        args.append("--primary")
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=3, check=False)
    except (subprocess.SubprocessError, OSError):
        return False
    types = res.stdout.lower()
    return any(hint in types for hint in _TEXT_HINTS)


def set_clipboard(text: str) -> None:
    """wl-copy daemonises to serve the offer, so this survives our exit."""
    try:
        subprocess.run(["wl-copy", "--", text], timeout=3, check=False)
    except (subprocess.SubprocessError, OSError):
        pass


def clear_clipboard() -> None:
    try:
        subprocess.run(["wl-copy", "--clear"], timeout=3, check=False)
    except (subprocess.SubprocessError, OSError):
        pass


def copy_chord(is_terminal: bool) -> tuple[str, str]:
    return ("CTRL", "Insert") if is_terminal else ("CTRL", "C")


def paste_chord(is_terminal: bool) -> tuple[str, str]:
    return ("SHIFT", "Insert") if is_terminal else ("CTRL", "V")


def capture_selection(win: dict | None, timeout_s: float = 0.7) -> tuple[str, str | None]:
    """Return (selected_text, saved_clipboard).

    saved_clipboard is the clipboard contents from before we touched anything,
    or None when the clipboard held something non-textual we must not clobber.
    """
    saved = _wl_paste() if _has_text() else None

    if _has_text(primary=True):
        primary = _wl_paste(primary=True)
        if primary.strip():
            return primary, saved

    # Fallback: ask the focused app to copy, then watch the clipboard change.
    mods, key = copy_chord(hypr.is_terminal(win))
    hypr.send_shortcut(mods, key)

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(0.05)
        if not _has_text():
            continue
        current = _wl_paste()
        if current.strip() and current != saved:
            return current, saved

    # No change: either the app ignored the chord, or the selection was already
    # on the clipboard. Fall back to whatever is there.
    return (saved or ""), saved


def replace_selection(text: str, win: dict | None, saved_clipboard: str | None,
                      restore_clipboard: bool = True) -> None:
    """Paste `text` over the still-active selection in the original window.

    The caller must have closed any Quill surface first: the paste chord is sent
    to whatever holds keyboard focus, and our own layer surface would swallow it.
    """
    set_clipboard(text)

    address = (win or {}).get("address", "")
    if address:
        hypr.focus_window(address)
        time.sleep(0.12)

    mods, key = paste_chord(hypr.is_terminal(win))
    hypr.send_shortcut(mods, key)

    if not restore_clipboard:
        return

    # Let the target app finish reading the offer before we swap it back out.
    time.sleep(0.45)
    if saved_clipboard is None:
        return
    if saved_clipboard:
        set_clipboard(saved_clipboard)
    else:
        clear_clipboard()
