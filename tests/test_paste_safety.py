"""Regression: Quill pastes only into the window the text came from.

    python3 tests/test_paste_safety.py

Replacement used to focus the recorded window without checking it took
focus, write the clipboard without checking the write, and paste into
whatever held the keyboard: a closed target sent the result to another app.
Capture fell back to the old clipboard when the synthetic Copy proved
nothing, so stale text could be edited and pasted over a real selection.
A refused paste also quit the popup and lost the result.

Hyprland and wl-clipboard are answered at the subprocess boundary, so the
real clipboard and hypr code runs unchanged without touching the desktop.
Window focus never proves the original selection is still selected, and
non-text clipboard offers cannot be restored, only left alone.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from fakes import (GLIB, THREADS, FakeClipboardApi, FakeHypr, UiCase, cli,
                   clipboard, config, hypr, provider)


# -- desktop fakes -----------------------------------------------------------

MISSING = object()


class FakeDesktop:
    """Hyprland plus wl-clipboard, answered at the subprocess boundary."""

    def __init__(self):
        self.windows: dict[str, dict] = {}
        self.focused: str | None = None
        self.clipboard = None  # ("text", str) | ("image", bytes) | None
        self.primary = None
        self.events: list[tuple] = []
        self.writes: list = []  # what Quill itself put on the clipboard
        self.focus_redirect = MISSING
        self.on_write = None

    def add(self, address, selection="", copy_mode="normal", tags=()):
        self.windows[address] = {
            "address": address, "class": "app", "title": address,
            "tags": list(tags), "selection": selection, "copy_mode": copy_mode,
        }
        return self.client(address)

    def client(self, address):
        return {k: v for k, v in self.windows[address].items()
                if k not in ("selection", "copy_mode")}

    @property
    def pastes(self):
        return [event[1:] for event in self.events if event[0] == "paste"]

    @property
    def copies(self):
        return [event[1:] for event in self.events if event[0] == "copy"]

    def run(self, args, **kwargs):
        args = [str(a) for a in args]
        out, rc = self._handle(args, kwargs.get("input"))
        if kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding"):
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            err = ""
        else:
            if isinstance(out, str):
                out = out.encode("utf-8")
            err = b""
        return subprocess.CompletedProcess(args, rc, out, err)

    def _handle(self, args, stdin):
        prog = os.path.basename(args[0])
        if prog == "wl-paste":
            return self._paste(args)
        if prog == "wl-copy":
            return self._copy(args, stdin)
        if prog == "hyprctl":
            return self._hyprctl(args[1:])
        if prog in ("notify-send", "gdbus"):
            self.events.append(("notify", tuple(args)))
            return "", 0
        return "", 127

    def _paste(self, args):
        if getattr(self, "paste_fails", False):
            return "", 1
        primary = "--primary" in args or "-p" in args
        offer = self.primary if primary else self.clipboard
        if offer is None:
            return "", 1
        kind, value = offer
        if "--list-types" in args or "-l" in args:
            return ("text/plain;charset=utf-8\nUTF8_STRING\n" if kind == "text"
                    else "image/png\n"), 0
        return value, 0

    def _copy(self, args, stdin):
        fails = getattr(self, "copy_fails", None)
        if fails == "raise":
            raise OSError("wl-copy is missing")
        if fails:
            return "", 1
        primary = "--primary" in args or "-p" in args
        if "--clear" in args or "-c" in args:
            text, offer = None, None
        else:
            if "--" in args:
                text = " ".join(args[args.index("--") + 1:])
            else:
                rest, skip = [], False
                for arg in args[1:]:
                    if skip:
                        skip = False
                        continue
                    if arg in ("-t", "--type", "-s", "--seat"):
                        skip = True
                        continue
                    if not arg.startswith("-"):
                        rest.append(arg)
                if rest:
                    text = " ".join(rest)
                elif isinstance(stdin, bytes):
                    text = stdin.decode("utf-8", errors="replace")
                else:
                    text = stdin or ""
            offer = ("text", text)
        if primary:
            self.primary = offer
            return "", 0
        self.clipboard = offer
        self.writes.append(text)
        if self.on_write:
            self.on_write(text)
        return "", 0

    def _hyprctl(self, rest):
        words = [w for w in rest if w not in ("-j", "--batch")]
        if not words:
            return "", 1
        cmd = words[0]
        if cmd == "activewindow":
            win = self.client(self.focused) if self.focused in self.windows else {}
            return json.dumps(win), 0
        if cmd == "clients":
            return json.dumps([self.client(a) for a in self.windows]), 0
        if cmd == "cursorpos":
            return "0, 0", 0
        if cmd == "monitors":
            return "[]", 0
        if cmd == "dispatch":
            return self._dispatch(" ".join(words[1:]))
        return "", 1

    def _dispatch(self, expr):
        found = re.search(r"address:(\w+)", expr)
        target = found.group(1) if found else None
        lowered = expr.lower()
        if "send_key_state" in lowered or "send_shortcut" in lowered:
            mods = re.search(r'mods\s*=\s*"([^"]*)"', expr)
            key = re.search(r'key\s*=\s*"([^"]*)"', expr)
            state = re.search(r'state\s*=\s*"([^"]*)"', expr)
            if state and state.group(1) != "down":
                return "ok", 0
            if mods and key:
                self._chord(mods.group(1), key.group(1), target or self.focused)
            return "ok", 0
        if lowered.startswith("sendshortcut"):
            parts = [p.strip() for p in expr.split(None, 1)[1].split(",")]
            self._chord(parts[0], parts[1], target or self.focused)
            return "ok", 0
        if "focus" in lowered and target is not None:
            return self._focus(target)
        return "ok", 0

    def _focus(self, address):
        if getattr(self, "focus_ack_fails", False):
            return "error: focus refused", 0
        if address not in self.windows:
            return "error: no such window", 0
        self.focused = address if self.focus_redirect is MISSING else self.focus_redirect
        self.events.append(("focus", address))
        return "ok", 0

    def _chord(self, mods, key, dest):
        chord = (mods.upper().replace(" ", ""), key.upper())
        self.events.append(("chord", dest, chord))
        if chord in (("CTRL", "C"), ("CTRL", "INSERT")):
            self.events.append(("copy", dest))
            win = self.windows.get(dest)
            if win is None:
                return
            if win["copy_mode"] == "normal" and win["selection"]:
                self.clipboard = ("text", win["selection"])
            elif win["copy_mode"] == "blank":
                self.clipboard = ("text", "\n")
        elif chord in (("CTRL", "V"), ("SHIFT", "INSERT")):
            text = self.clipboard[1] if self.clipboard and self.clipboard[0] == "text" else None
            self.events.append(("paste", dest, text))


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(float(seconds), 0.001)


def _no_popen(*_args, **_kwargs):
    raise AssertionError("unexpected subprocess.Popen")



# -- clipboard and focus -----------------------------------------------------

class ClipboardCase(unittest.TestCase):
    def setUp(self):
        self.desk = FakeDesktop()
        self.clock = FakeClock()
        fake_subprocess = types.SimpleNamespace(
            run=lambda *a, **k: self.desk.run(*a, **k),
            Popen=_no_popen,
            CompletedProcess=subprocess.CompletedProcess,
            SubprocessError=subprocess.SubprocessError,
            TimeoutExpired=subprocess.TimeoutExpired,
            CalledProcessError=subprocess.CalledProcessError,
            PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
STDOUT=subprocess.STDOUT,
        )
        fake_time = types.SimpleNamespace(
            monotonic=self.clock.monotonic, time=self.clock.monotonic,
            perf_counter=self.clock.monotonic, sleep=self.clock.sleep,
        )
        for module in (clipboard, hypr):
            for attr, value in (("subprocess", fake_subprocess), ("time", fake_time)):
                patcher = mock.patch.object(module, attr, value)
                patcher.start()
                self.addCleanup(patcher.stop)
        self.target = self.desk.add("0xaaa", selection="teh text")
        self.desk.add("0xbbb", selection="other app text")
        self.desk.clipboard = ("text", "saved")


class ReplaceSelectionTests(ClipboardCase):
    def replace(self, win, saved="saved", restore=True):
        return clipboard.replace_selection("new text", win, saved,
restore_clipboard=restore)

    def assert_refused(self, result, clipboard_before):
        self.assertIs(result, False, "replace_selection must report refusal")
        self.assertEqual(self.desk.pastes, [], "no paste chord may be sent")
        self.assertNotIn("new text", self.desk.writes,
                         "replacement text must not reach the clipboard")
        self.assertEqual(self.desk.clipboard, clipboard_before)

    def test_refuses_closed_target(self):
        self.desk.focused = "0xbbb"
        closed = dict(self.target, address="0xdead")
        self.assert_refused(self.replace(closed, restore=False), ("text", "saved"))

    def test_refuses_missing_target(self):
        self.desk.focused = "0xbbb"
        self.assert_refused(self.replace(None, restore=False), ("text", "saved"))

    def test_refuses_when_focus_lands_on_another_window(self):
        self.desk.focused = "0xbbb"
        self.desk.focus_redirect = "0xbbb"
        self.assert_refused(self.replace(self.target, restore=False), ("text", "saved"))

    def test_refuses_when_desktop_keeps_focus(self):
        self.desk.focused = None
        self.desk.focus_redirect = None
        self.assert_refused(self.replace(self.target, restore=False), ("text", "saved"))

    def test_refusal_leaves_non_text_clipboard_alone(self):
        self.desk.focused = "0xbbb"
        self.desk.clipboard = ("image", b"\x89PNG")
        closed = dict(self.target, address="0xdead")
        self.assert_refused(self.replace(closed, saved=None), ("image", b"\x89PNG"))
        self.assertEqual(self.desk.writes, [])

    def test_pastes_into_verified_target_and_restores(self):
        """Guard: the success path must keep working."""
        self.desk.focused = "0xbbb"
        result = self.replace(self.target)
        self.assertIsNot(result, False)
        self.assertEqual(self.desk.pastes, [("0xaaa", "new text")])
        self.assertEqual(self.desk.clipboard, ("text", "saved"))

    def test_focus_change_during_clipboard_write_never_pastes_elsewhere(self):
        """Guard: focus may move while the clipboard is being prepared."""
        self.desk.focused = "0xaaa"

        def steal(text):
            if text == "new text" and self.desk.focused != "0xbbb":
                self.desk.focused = "0xbbb"

        self.desk.on_write = steal
        result = self.replace(self.target)
        self.assertNotIn("0xbbb", [paste[0] for paste in self.desk.pastes])
        if not self.desk.pastes:
            self.assertIs(result, False)
        self.assertEqual(self.desk.clipboard, ("text", "saved"))



class ReplaceClipboardTests(ClipboardCase):
    def replace(self, saved="saved", win=None):
        return clipboard.replace_selection(
            "new text", win or self.target, saved)

    def test_refuses_when_clipboard_write_fails(self):
        for mode in ("rc", "raise"):
            with self.subTest(mode):
                self.desk.events.clear()
                self.desk.focused = "0xaaa"
                self.desk.clipboard = ("text", "saved")
                self.desk.copy_fails = mode
                self.assertIs(self.replace(), False)
                self.assertEqual(self.desk.pastes, [])
                self.assertEqual(self.desk.clipboard,
                                 ("text", "saved"))

    def test_rollback_keeps_newer_clipboard_text(self):
        self.desk.focused = "0xaaa"
        self.desk.clipboard = ("text", "newer")

        def steal(text):
            if text == "new text":
                self.desk.focused = "0xbbb"

        self.desk.on_write = steal
        self.assertIs(self.replace(), False)
        self.assertEqual(self.desk.pastes, [])
        self.assertEqual(self.desk.clipboard, ("text", "newer"))

    def test_racing_copy_is_never_pasted_or_overwritten(self):
        self.desk.focused = "0xaaa"

        def racer(text):
            if text == "new text":
                self.desk.clipboard = ("text", "racer")

        self.desk.on_write = racer
        self.assertIs(self.replace(), False)
        self.assertEqual(self.desk.pastes, [])
        self.assertEqual(self.desk.clipboard, ("text", "racer"))

    def test_non_text_clipboard_refuses_before_writing(self):
        self.desk.focused = "0xaaa"
        self.desk.clipboard = ("image", b"\x89PNG")
        self.assertIs(self.replace(saved=None), False)
        self.assertEqual(self.desk.writes, [])
        self.assertEqual(self.desk.pastes, [])
        self.assertEqual(self.desk.clipboard,
                         ("image", b"\x89PNG"))

    def test_failed_focus_ack_refuses_even_if_active(self):
        self.desk.focused = "0xaaa"
        self.desk.focus_ack_fails = True
        self.assertIs(self.replace(), False)
        self.assertEqual(self.desk.writes, [])
        self.assertEqual(self.desk.pastes, [])

    def test_terminal_uses_shift_insert(self):
        self.desk.windows["0xaaa"]["tags"] = ["terminal*"]
        self.desk.focused = "0xbbb"
        win = self.desk.client("0xaaa")
        self.assertIs(self.replace(win=win), True)
        chords = [e[2] for e in self.desk.events if e[0] == "chord"]
        self.assertEqual(chords, [("SHIFT", "INSERT")])
        self.assertEqual(self.desk.pastes, [("0xaaa", "new text")])

    def test_empty_clipboard_success(self):
        cases = ((("text", ""), "", None),)
        for start, saved, end in cases:
            with self.subTest(start=start):
                self.desk.events.clear()
                self.desk.focused = "0xaaa"
                self.desk.clipboard = start
                self.assertIs(self.replace(saved=saved), True)
                self.assertEqual(self.desk.pastes,
                                 [("0xaaa", "new text")])
                self.assertEqual(self.desk.clipboard, end)

    def test_unreadable_or_empty_clipboard_refuses(self):
        cases = (("empty", None, False),
                 ("broken wl-paste", ("image", b"\x89PNG"), True))
        for label, start, broken in cases:
            with self.subTest(label):
                self.desk.events.clear()
                self.desk.writes.clear()
                self.desk.focused = "0xaaa"
                self.desk.clipboard = start
                self.desk.paste_fails = broken
                result = clipboard.replace_selection(
                    "new text", self.target, None)
                self.assertIs(result, False)
                self.assertEqual(self.desk.writes, [])
                self.assertEqual(self.desk.pastes, [])
                self.assertEqual(self.desk.clipboard, start)

    def test_closed_target_restores_only_our_capture(self):
        self.desk.focused = "0xaaa"
        selected, saved = clipboard.capture_selection(self.target)
        self.assertEqual(selected, "teh text")
        del self.desk.windows["0xaaa"]
        self.assertIs(clipboard.replace_selection("Fixed text", self.target, saved), False)
        self.assertEqual(self.desk.clipboard, ("text", "saved"))
        self.assertEqual(self.desk.pastes, [])

    def test_closed_target_keeps_newer_user_copy(self):
        self.desk.focused = "0xaaa"
        selected, saved = clipboard.capture_selection(self.target)
        self.assertEqual(selected, "teh text")
        self.desk.clipboard = ("text", "newer user copy")
        del self.desk.windows["0xaaa"]
        self.assertIs(clipboard.replace_selection("Fixed text", self.target, saved), False)
        self.assertEqual(self.desk.clipboard, ("text", "newer user copy"))

    def test_rejected_paste_chord_refuses_and_restores(self):
        self.desk.focused = "0xaaa"
        original_dispatch = hypr.dispatch

        def reject_keys(expression):
            return False if "send_key_state" in expression else original_dispatch(expression)

        with mock.patch.object(hypr, "dispatch", reject_keys):
            replaced = clipboard.replace_selection("Fixed text", self.target, "saved")
        self.assertIs(replaced, False)
        self.assertEqual(self.desk.pastes, [])
        self.assertEqual(self.desk.clipboard, ("text", "saved"))



class CaptureSelectionTests(ClipboardCase):
    def setUp(self):
        super().setUp()
        self.desk.focused = "0xaaa"
        self.desk.primary = None
        self.desk.clipboard = ("text", "unrelated")

    def test_unproven_copy_reports_no_selection(self):
        # An unchanged clipboard cannot tell "copied the same text" from "copied nothing".
        for label, mode, selection in (("ignored chord", "ignore", "teh text"),
                                       ("same-text copy", "normal", "unrelated")):
            with self.subTest(label):
                self.desk.windows["0xaaa"].update(copy_mode=mode,
selection=selection)
                self.desk.clipboard = ("text", "unrelated")
                text, saved = clipboard.capture_selection(self.target)
                self.assertEqual(text, "", "old clipboard must not become the selection")
                self.assertEqual(saved, "unrelated")
                self.assertEqual(self.desk.clipboard, ("text", "unrelated"))

    def test_blank_copy_restores_saved_clipboard(self):
        self.desk.windows["0xaaa"]["copy_mode"] = "blank"
        text, saved = clipboard.capture_selection(self.target)
        self.assertEqual(text, "")
        self.assertEqual(saved, "unrelated")
        self.assertEqual(self.desk.clipboard, ("text", "unrelated"))

    def test_primary_matching_clipboard_is_still_the_selection(self):
        """Guard: non-empty PRIMARY is used even when it equals the
        clipboard. It may be stale; it does not prove the source."""
        self.desk.primary = ("text", "teh text")
        self.desk.clipboard = ("text", "teh text")
        self.assertEqual(clipboard.capture_selection(self.target), ("teh text", "teh text"))
        self.assertEqual(self.desk.copies, [])

    def test_changed_clipboard_after_copy_is_the_selection(self):
        """Guard: the synthetic-copy fallback still works."""
        self.assertEqual(clipboard.capture_selection(self.target), ("teh text", "unrelated"))



class CaptureFocusTests(ClipboardCase):
    def test_ignored_copy_keeps_non_text_clipboard(self):
        self.desk.focused = "0xaaa"
        self.desk.windows["0xaaa"]["copy_mode"] = "ignore"
        self.desk.clipboard = ("image", b"\x89PNG")
        got = clipboard.capture_selection(self.target)
        self.assertEqual(got, ("", None))
        self.assertEqual(self.desk.clipboard,
                         ("image", b"\x89PNG"))
        self.assertEqual(self.desk.writes, [])

    def test_capture_refuses_wrong_window_before_copy(self):
        self.desk.focused = "0xbbb"
        selected, saved = clipboard.capture_selection(self.target)
        self.assertEqual((selected, saved), ("", "saved"))
        self.assertEqual(self.desk.copies, [])
        self.assertEqual(self.desk.clipboard, ("text", "saved"))

    def test_primary_refuses_wrong_window_too(self):
        self.desk.primary = ("text", "another window's selection")
        self.desk.focused = "0xbbb"
        selected, saved = clipboard.capture_selection(self.target)
        self.assertEqual((selected, saved), ("", "saved"))
        self.assertEqual(self.desk.copies, [])

    def test_capture_refuses_after_focus_moves_and_preserves_unknown_offer(self):
        self.desk.focused = "0xaaa"
        original_send = hypr.send_shortcut

        def send_then_steal(*args, **kwargs):
            result = original_send(*args, **kwargs)
            self.desk.focused = "0xbbb"
            return result

        with mock.patch.object(hypr, "send_shortcut", send_then_steal):
            selected, saved = clipboard.capture_selection(self.target)
        self.assertEqual((selected, saved), ("", "saved"))
        self.assertEqual(self.desk.clipboard, ("text", "teh text"))
        self.assertEqual(self.desk.writes, [])

    def test_newer_copy_during_focus_change_survives_capture_refusal(self):
        self.desk.focused = "0xaaa"
        original_send = hypr.send_shortcut

        def copy_then_user_switches(*args, **kwargs):
            accepted = original_send(*args, **kwargs)
            self.desk.focused = "0xbbb"
            self.desk.clipboard = ("text", "newer user copy")
            return accepted

        with mock.patch.object(hypr, "send_shortcut", copy_then_user_switches):
            selected, saved = clipboard.capture_selection(self.target)
        self.assertEqual((selected, saved), ("", "saved"))
        self.assertEqual(self.desk.copies, [("0xaaa",)])
        self.assertEqual(self.desk.clipboard, ("text", "newer user copy"))
        self.assertEqual(self.desk.writes, [])

    def test_rejected_copy_with_focus_change_and_no_new_copy_keeps_saved(self):
        self.desk.focused = "0xaaa"

        def reject_then_switch(*_args, **_kwargs):
            self.desk.focused = "0xbbb"
            return False

        with mock.patch.object(hypr, "send_shortcut", reject_then_switch):
            selected, saved = clipboard.capture_selection(self.target)
        self.assertEqual((selected, saved), ("", "saved"))
        self.assertEqual(self.desk.clipboard, ("text", "saved"))
        self.assertEqual(self.desk.copies, [])
        self.assertEqual(self.desk.writes, [])

    def test_focus_change_while_waiting_preserves_ambiguous_blank_copy(self):
        self.desk.focused = "0xaaa"
        self.desk.windows["0xaaa"]["copy_mode"] = "blank"
        original_sleep = self.clock.sleep
        waits = 0

        def sleep_then_switch(seconds):
            nonlocal waits
            original_sleep(seconds)
            waits += 1
            if waits == 2:
                self.desk.focused = "0xbbb"

        with mock.patch.object(clipboard.time, "sleep", sleep_then_switch):
            selected, saved = clipboard.capture_selection(self.target)
        self.assertEqual((selected, saved), ("", "saved"))
        self.assertEqual(self.desk.clipboard, ("text", "\n"))
        self.assertEqual(self.desk.writes, [])

    def test_rejected_copy_does_not_erase_newer_user_copy(self):
        self.desk.focused = "0xaaa"

        def rejected_copy(*_args, **_kwargs):
            self.desk.clipboard = ("text", "newer user copy")
            return False

        with mock.patch.object(hypr, "send_shortcut", rejected_copy):
            selected, saved = clipboard.capture_selection(self.target)
        self.assertEqual((selected, saved), ("", "saved"))
        self.assertEqual(self.desk.clipboard, ("text", "newer user copy"))



class HyprDispatchTests(ClipboardCase):
    def test_dispatch_uses_exit_status_even_for_empty_output(self):
        def command_result(status):
            return subprocess.CompletedProcess([], status, "", "")

        with mock.patch.object(hypr.subprocess, "run", return_value=command_result(0)):
            self.assertIs(hypr.dispatch("expression"), True)
        with mock.patch.object(hypr.subprocess, "run", return_value=command_result(1)):
            self.assertIs(hypr.dispatch("expression"), False)
        failed_stderr = subprocess.CompletedProcess([], 0, "", "error: rejected")
        with mock.patch.object(hypr.subprocess, "run", return_value=failed_stderr):
            self.assertIs(hypr.dispatch("expression"), False)
        with mock.patch.object(hypr.subprocess, "run", side_effect=OSError("missing")):
            self.assertIs(hypr.dispatch("expression"), False)

    def test_shortcut_reports_either_edge_failure_and_releases_key(self):
        for rejected in ("down", "up"):
            with self.subTest(rejected=rejected):
                calls = []

                def dispatch_edge(expression):
                    calls.append(expression)
                    return f'"{rejected}"' not in expression

                with mock.patch.object(hypr, "dispatch", dispatch_edge):
                    self.assertIs(hypr.send_shortcut("CTRL", "V"), False)
                self.assertEqual(len(calls), 2)
                self.assertIn('"up"', calls[1])



# -- popup -------------------------------------------------------------------

class RefusedReplaceUiTests(UiCase):
    def finish(self):
        self.press("1")
        self.feed(THREADS[-1], ["Fixed text"])
        GLIB.flush()

    def test_refused_replace_keeps_result_and_stops_auto_retry(self):
        self.cfg.auto_replace = True
        self.clip.replace_result = False
        self.press("1")
        self.feed(THREADS[-1], ["Fixed text"])
        GLIB.flush()

        self.assertEqual(self.clip.replaced, ["Fixed text"])
        self.assertEqual(self.app.quits, 0, "Quill quit and lost the result")
        self.assertTrue(self.win.get_visible(), "result view was not reopened")
        self.assertEqual(self.win.stack.get_visible_child_name(), "result")
        self.assertEqual(self.text(), "Fixed text")
        self.assertTrue(self.win.copy_button.get_sensitive())
        self.assertTrue(self.win.error_label.get_visible())
        self.assertTrue(self.win.error_label.get_label().strip())
        self.assertNotIn("Fixed text", self.clip.set_calls)

        self.win._run_action()  # Retry
        self.feed(THREADS[-1], ["Fixed again"])
        GLIB.flush()
        self.assertEqual(self.clip.replaced, ["Fixed text"], "auto-replace retried after refusal")
        self.assertEqual(self.text(), "Fixed again")
        self.assertTrue(self.win.get_visible())

    def test_manual_replace_refusal_reopens(self):
        self.clip.replace_result = False
        self.press("1")
        self.feed(THREADS[-1], ["Fixed text"])
        GLIB.flush()
        self.win._on_replace()
        GLIB.flush()
        self.assertEqual(self.app.quits, 0)
        self.assertTrue(self.win.get_visible())
        self.assertEqual(self.text(), "Fixed text")

    def test_refusal_message_makes_no_clipboard_claim(self):
        self.cfg.auto_replace = True
        self.clip.replace_result = False
        self.finish()
        label = self.win.error_label.get_label().lower()
        self.assertTrue(label)
        self.assertNotIn("clipboard", label)

    def test_failed_copy_keeps_result(self):
        self.clip.copy_result = False
        self.finish()
        self.win._on_copy()
        self.assertEqual(self.app.quits, 0)
        self.assertTrue(self.win.get_visible())
        self.assertEqual(self.text(), "Fixed text")
        self.assertTrue(self.win.error_label.get_visible())

    def test_copy_success_still_dismisses(self):
        self.finish()
        self.win._on_copy()
        self.assertEqual(self.clip.set_calls, ["Fixed text"])
        self.assertEqual(self.app.quits, 1)



# -- quill run ---------------------------------------------------------------

class CliRunTests(unittest.TestCase):
    def setUp(self):
        provider.streams[:] = []
        self.clip = FakeClipboardApi()
        self.hyp = FakeHypr()
        for attr, value in (("clipboard", self.clip), ("hypr", self.hyp)):
            patcher = mock.patch.object(cli, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {key: str(Path(self.tmp.name, key.lower())) for key in (
            "XDG_RUNTIME_DIR", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "HOME")}
        for path in env.values():
            Path(path).mkdir()
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg = config.Config()

    def run_cli(self, quiet=True):
        args = argparse.Namespace(action="fix", instruction=None, quiet=quiet)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.cmd_run(args, self.cfg)
        return rc, out.getvalue()

    def drafts(self):
        found = []
        for root, _dirs, files in os.walk(self.tmp.name):
            for name in files:
                try:
                    found.append(Path(root, name).read_text(encoding="utf-8",
errors="replace"))
                except OSError:
                    pass
        return found

    def test_refused_replace_fails_loudly_and_keeps_result(self):
        provider.streams.append(["Fixed text"])
        self.clip.replace_result = False
        rc, out = self.run_cli(quiet=True)

        self.assertNotEqual(rc, 0)
        self.assertTrue([n for n in self.hyp.notes if n["urgency"] == "critical"],
                        "quiet mode hid the failed replacement")
        exposed = "Fixed text" in out or any("Fixed text" in d for d in self.drafts())
        self.assertTrue(exposed, "generated result was lost")
        self.assertNotIn("Fixed text", self.clip.set_calls)

    def test_successful_replace_still_returns_zero(self):
        """Guard: the success path is unchanged."""
        provider.streams.append(["Fixed text"])
        rc, _out = self.run_cli(quiet=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.clip.replaced, ["Fixed text"])

    def test_draft_is_private_and_complete(self):
        provider.streams.append(["Fixed text"])
        self.clip.replace_result = False
        rc, out = self.run_cli(quiet=False)
        self.assertEqual(rc, 1)
        provider.streams.append(["Second text"])
        self.assertEqual(self.run_cli(quiet=False)[0], 1)
        folder = Path(os.environ["XDG_RUNTIME_DIR"], "quill")
        drafts = sorted(folder.glob("draft-*.txt"))
        self.assertEqual(len(drafts), 2)
        self.assertEqual(
            sorted(d.read_text(encoding="utf-8") for d in drafts),
            ["Fixed text", "Second text"])
        for draft in drafts:
            self.assertEqual(draft.stat().st_mode & 0o777, 0o600)
        self.assertIn("Fixed text", out)

    def test_draft_failure_keeps_stdout_and_alert(self):
        def broken(*_args, **_kwargs):
            raise OSError("disk full")

        for target in ("_runtime_dir", "mkstemp"):
            with self.subTest(target):
                self.hyp.notes.clear()
                provider.streams.append(["Fixed text"])
                self.clip.replace_result = False
                owner = cli if target == "_runtime_dir" else tempfile
                with mock.patch.object(owner, target, broken):
                    rc, out = self.run_cli(quiet=True)
                self.assertEqual(rc, 1)
                self.assertIn("Fixed text", out)
                notes = [n for n in self.hyp.notes
                         if n["urgency"] == "critical"]
                self.assertTrue(notes)
                self.assertIn("stdout", notes[-1]["body"])



if __name__ == "__main__":
    unittest.main(verbosity=2)
