"""Regression: a cancelled request must not touch the one that replaced it.

    python3 tests/test_stale_callbacks.py

Each stream worker queues its deltas, error and completion on the GTK main
loop. Retry, a new action, Escape or dismissal cancel the worker, but
callbacks it already queued still ran: old text leaked into the retry, an
old error marked it failed, and an old completion could auto-replace the
selection with the cancelled output. The fake main loop below holds those
callbacks so each test decides exactly when they run.
"""

from __future__ import annotations

import unittest
from unittest import mock

from fakes import GLIB, STUBS, THREADS, ProviderError, UiCase


class StaleCallbackTests(UiCase):
    def test_finished_request_cannot_touch_retry(self):
        self.press("1")
        old = THREADS[-1]
        self.feed(old, ["OLD ", "TEXT"])
        self.win._run_action()  # Retry
        new = THREADS[-1]
        self.assertIsNot(old, new)

        GLIB.flush()
        self.assertEqual(self.text(), "", "old deltas leaked into the retry")
        self.assertFalse(self.win.replace_button.get_sensitive(), "old completion enabled Replace")
        self.assertTrue(self.win.spinner.get_spinning(), "old completion stopped the spinner")
        self.assertEqual(self.win.status_label.get_label(), "Fix Spelling & Grammar…")

        self.feed(new, ["NEW"])
        GLIB.flush()
        self.assertEqual(self.text(), "NEW")
        self.assertTrue(self.win.replace_button.get_sensitive())

    def test_old_error_cannot_fail_retry(self):
        self.press("1")
        old = THREADS[-1]
        self.feed(old, ProviderError("old failure"))
        self.win._run_action()
        new = THREADS[-1]

        GLIB.flush()
        self.assertFalse(self.win.error_label.get_visible(), "old error shown on the retry")
        self.assertNotEqual(self.win.status_label.get_label(), "Failed")
        self.assertTrue(self.win.spinner.get_spinning())

        self.feed(new, ["NEW"])
        GLIB.flush()
        self.assertEqual(self.text(), "NEW")
        self.assertFalse(self.win.error_label.get_visible())

    def test_late_failure_after_cancel_is_ignored(self):
        self.press("1")
        old = THREADS[-1]
        win = self.win

        class LateFailure:
            def __iter__(self):
                yield "OLD"
                win._run_action()  # Retry while A is mid-stream
                raise ProviderError("cancelled")

        self.feed(old, LateFailure())
        new = THREADS[-1]
        self.assertIsNot(old, new)
        GLIB.flush()
        self.assertEqual(self.text(), "")
        self.assertFalse(self.win.error_label.get_visible())
        self.assertTrue(self.win.spinner.get_spinning())
        self.feed(new, ["NEW"])
        GLIB.flush()
        self.assertEqual(self.text(), "NEW")

    def test_old_callbacks_drained_after_new_completion(self):
        writes = []
        patcher = mock.patch.object(
            STUBS["state"], "write", lambda *a, **k: writes.append(a))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.press("1")
        self.feed(THREADS[-1], ["OLD"])
        held = GLIB.queue[:]
        GLIB.queue.clear()
        self.win._run_action()
        self.feed(THREADS[-1], ["NEW"])
        GLIB.flush()
        writes.clear()
        GLIB.queue.extend(held)
        GLIB.flush()
        self.assertEqual(self.text(), "NEW")
        self.assertEqual(self.win.status_label.get_label(),
                         "⏎ to replace")
        self.assertFalse(self.win.spinner.get_spinning())
        self.assertTrue(self.win.replace_button.get_sensitive())
        self.assertFalse(self.win.error_label.get_visible())
        self.assertEqual(writes, [])
        self.assertEqual(self.clip.replaced, [])

    def test_escape_and_action_change_drop_old_auto_replace(self):
        self.cfg.auto_replace = True
        self.press("1")
        old = THREADS[-1]
        self.feed(old, ["OLD"])

        self.press("Escape")
        GLIB.flush()
        self.assertEqual(self.clip.replaced, [], "cancelled request pasted its output")
        self.assertTrue(self.win.get_visible())
        self.assertEqual(self.win.stack.get_visible_child_name(), "menu")

        self.press("2")
        new = THREADS[-1]
        self.assertIsNot(old, new)
        GLIB.flush()
        self.assertEqual(self.clip.replaced, [])

        self.feed(new, ["NEW"])
        GLIB.flush()
        self.assertEqual(self.clip.replaced, ["NEW"])

    def test_dismiss_drops_queued_callbacks(self):
        self.cfg.auto_replace = True
        self.press("1")
        self.feed(THREADS[-1], ["OLD"])
        self.win._dismiss()
        GLIB.flush()
        self.assertEqual(self.clip.replaced, [], "dismissed request pasted its output")
        self.assertEqual(self.app.quits, 1)



class CurrentRequestTests(UiCase):
    """Guards: the live request behaves as before."""

    def test_current_request_streams_and_auto_replaces(self):
        """Guard: the live request keeps sanitising and auto-replacing."""
        self.cfg.auto_replace = True
        self.press("1")
        self.feed(THREADS[-1], ["<think>plan</think>", "Fixed text"])
        GLIB.flush()
        self.assertEqual(self.clip.replaced, ["Fixed text"])
        self.assertEqual(self.app.quits, 1)
        self.assertFalse(self.win.get_visible())

    def test_current_error_still_shows(self):
        self.press("1")
        self.feed(THREADS[-1], ProviderError("model down"))
        GLIB.flush()
        self.assertTrue(self.win.error_label.get_visible())
        self.assertEqual(self.win.error_label.get_label(),
                         "model down")
        self.assertEqual(self.win.status_label.get_label(), "Failed")



if __name__ == "__main__":
    unittest.main(verbosity=2)
