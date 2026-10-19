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


class CapturedClipboard(str):
    """Original text plus the copy offer Quill may still own.

    This remains a string for existing callers of capture_selection.
    Equality of text is only a best-effort ownership check: another client
    can copy the same text or change the offer between the check and restore.
    """

    def __new__(cls, saved: str, captured: str):
        value = super().__new__(cls, saved)
        value.captured = captured
        return value


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


def set_clipboard(text: str) -> bool:
    """wl-copy daemonises to serve the offer, so this survives our exit."""
    try:
        res = subprocess.run(["wl-copy", "--", text], timeout=3,
                             check=False)
    except (subprocess.SubprocessError, OSError):
        return False
    return res.returncode == 0


def clear_clipboard() -> None:
    try:
        subprocess.run(["wl-copy", "--clear"], timeout=3, check=False)
    except (subprocess.SubprocessError, OSError):
        pass


def copy_chord(is_terminal: bool) -> tuple[str, str]:
    return ("CTRL", "Insert") if is_terminal else ("CTRL", "C")


def paste_chord(is_terminal: bool) -> tuple[str, str]:
    return ("SHIFT", "Insert") if is_terminal else ("CTRL", "V")


def _restore(saved: str | None) -> None:
    """Put saved text back. None means nothing textual was saved."""
    if saved is None:
        return
    if saved:
        set_clipboard(saved)
    else:
        clear_clipboard()


def _is_focused(address: str) -> bool:
    win = hypr.active_window()
    return bool(address) and (win or {}).get("address") == address


def _focus_target(address: str) -> bool:
    """Focus the target and confirm Hyprland reports it active."""
    if not address or not hypr.focus_window(address):
        return False
    time.sleep(0.12)
    return _is_focused(address)


def _snapshot() -> tuple[str, str]:
    """Classify the clipboard as ("text", value) or ("other", "").

    Other covers non-text offers, failed reads and an empty clipboard.
    wl-paste exits non-zero both when nothing is copied and when it
    cannot read the offer, so Quill treats both as unrestorable.
    """
    try:
        res = subprocess.run(["wl-paste", "--list-types"],
                             capture_output=True, text=True,
                             timeout=3, check=False)
        types = res.stdout.lower()
        if res.returncode != 0 or not types.strip():
            return "other", ""
        if not any(hint in types for hint in _TEXT_HINTS):
            return "other", ""
        res = subprocess.run(["wl-paste", "--no-newline"],
                             capture_output=True, timeout=3,
                             check=False)
        if res.returncode != 0:
            return "other", ""
        return "text", res.stdout.decode("utf-8")
    except (subprocess.SubprocessError, OSError, UnicodeDecodeError):
        return "other", ""


def _current_text() -> str | None:
    kind, value = _snapshot()
    return value if kind == "text" else None


def _rollback(ours: str, kind: str, before: str) -> None:
    """Undo our write, unless something newer already replaced it."""
    if _current_text() != ours:
        return
    if kind == "text" and before:
        set_clipboard(before)
    else:
        clear_clipboard()


def restore_capture_if_owned(saved: str | None) -> None:
    """Undo synthetic Copy only while its observed text is still offered."""
    if isinstance(saved, CapturedClipboard) and _current_text() == saved.captured:
        _restore(saved)


def capture_selection(win: dict | None, timeout_s: float = 0.7) -> tuple[str, str | None]:
    """Return (selected_text, saved_clipboard).

    saved_clipboard is the clipboard contents from before we touched anything,
    or None when the clipboard held something non-textual we must not clobber.
    A synthetic copy wraps saved text with the observed copy offer so a later
    refusal can restore it conditionally; it still compares equal to a string.
    """
    saved = _wl_paste() if _has_text() else None
    address = (win or {}).get("address", "")
    if not _is_focused(address):
        return "", saved

    # PRIMARY is the latest selection anywhere. Quill cannot prove it
    # came from the focused window, so a stale one can slip through.
    if _has_text(primary=True):
        primary = _wl_paste(primary=True)
        if primary.strip():
            return (primary, saved) if _is_focused(address) else ("", saved)

    # Fallback: ask only the recorded window to copy. It can still lose
    # focus in the narrow interval between the check and the key event.
    if not _is_focused(address):
        return "", saved
    mods, key = copy_chord(hypr.is_terminal(win))
    sent = hypr.send_shortcut(mods, key)
    if not _is_focused(address):
        # Focus changed during Copy. The current offer may be a newer copy
        # from another window; reading it now cannot establish our ownership.
        return "", saved
    if not sent:
        # A failed dispatch cannot establish which client changed the offer.
        return "", saved

    deadline = time.monotonic() + timeout_s
    changed = False
    observed = None
    while time.monotonic() < deadline:
        time.sleep(0.05)
        if not _is_focused(address):
            # A focus mismatch also makes the offer seen while waiting
            # ambiguous. Leave it in place for the current owner.
            return "", saved
        if not _has_text():
            changed = True
            continue
        current = _wl_paste()
        if current != saved:
            changed = True
            observed = current
        if current.strip() and current != saved:
            return current, (CapturedClipboard(saved, current)
                             if saved is not None else None)

    # An unchanged clipboard cannot tell an ignored chord from a copy
    # of the same text, so report no selection rather than guess.
    # If the chord left a blank or non-text offer, put saved text
    # back. Non-text contents are never saved, so they stay replaced.
    if changed and saved is not None and observed is not None:
        restore_capture_if_owned(CapturedClipboard(saved, observed))
    return "", saved


def replace_selection(text: str, win: dict | None, saved_clipboard: str | None,
                      restore_clipboard: bool = True) -> bool:
    """Paste `text` over the still-active selection in the original window.

    The caller must have closed any Quill surface first: the paste chord is sent
    to whatever holds keyboard focus, and our own layer surface would swallow it.

    Returns False when the target is not focused, the clipboard cannot be
    restored, the write does not read back, or a key dispatch fails. A failed
    key-up can follow an accepted key-down, so False does not prove that the
    target app made no change. Focus does not prove selection identity.
    """
    address = (win or {}).get("address", "")
    if not _focus_target(address):
        restore_capture_if_owned(saved_clipboard)
        return False
    # Refuse before writing anything that a failure could not undo.
    kind, before = _snapshot()
    if kind == "other":
        restore_capture_if_owned(saved_clipboard)
        return False

    # Read back: a failed write would otherwise paste the old text.
    if not set_clipboard(text) or _current_text() != text:
        _rollback(text, kind, before)
        restore_capture_if_owned(saved_clipboard)
        return False
    # Focus can move while the offer starts up; undo the write if so.
    if not _is_focused(address):
        _rollback(text, kind, before)
        restore_capture_if_owned(saved_clipboard)
        return False

    mods, key = paste_chord(hypr.is_terminal(win))
    if not hypr.send_shortcut(mods, key):
        _rollback(text, kind, before)
        restore_capture_if_owned(saved_clipboard)
        return False

    if restore_clipboard:
        # Let the target app finish reading the offer first, and leave
        # anything copied since then alone.
        time.sleep(0.45)
        if _current_text() == text:
            _restore(saved_clipboard)
    return True
