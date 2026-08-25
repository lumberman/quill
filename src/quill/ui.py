"""The popup itself: a layer-shell surface placed at the mouse cursor."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Pango", "1.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from . import clipboard, hypr, ollama, state  # noqa: E402
from .actions import Action, build_messages  # noqa: E402
from .config import Config  # noqa: E402

MENU_WIDTH = 340
RESULT_WIDTH = 560
RESULT_HEIGHT = 420
EDGE_PAD = 12

CSS = """
window.quill { background: transparent; }

.quill-card {
  background-color: @theme_bg_color;
  border: 1px solid alpha(currentColor, 0.16);
  border-radius: 12px;
  box-shadow: 0 8px 28px alpha(black, 0.45);
}

.quill-header {
  padding: 10px 14px 6px 14px;
  border-bottom: 1px solid alpha(currentColor, 0.10);
}

.quill-snippet { font-size: 0.85em; opacity: 0.72; }
.quill-meta { font-size: 0.78em; opacity: 0.5; }

.quill-list { background: transparent; padding: 6px; }

.quill-list row {
  border-radius: 8px;
  padding: 7px 10px;
  min-height: 0;
}
.quill-list row:selected { background-color: alpha(@accent_bg_color, 0.9); }

.quill-key {
  font-family: monospace;
  font-size: 0.78em;
  opacity: 0.42;
  min-width: 14px;
}

.quill-footer {
  padding: 8px 12px;
  border-top: 1px solid alpha(currentColor, 0.10);
}

.quill-result { font-size: 0.95em; }
.quill-result text { background: transparent; }

.quill-error { color: @error_color; font-size: 0.86em; }
"""


def _snippet(text: str, limit: int = 90) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class QuillWindow(Adw.ApplicationWindow):
    def __init__(self, app, cfg: Config, text: str, win: dict | None,
                 saved_clipboard: str | None):
        super().__init__(application=app)
        self.cfg = cfg
        self.source_text = text
        self.target_window = win
        self.saved_clipboard = saved_clipboard
        self.cancel_event: threading.Event | None = None
        self.current_action: Action | None = None
        self.current_custom = ""
        self._result_chars: list[str] = []

        self.add_css_class("quill")
        self.set_decorated(False)
        self.set_resizable(False)

        self._init_layer_shell()
        self._build()
        self._install_keys()

    # -- layer shell -------------------------------------------------------
    def _init_layer_shell(self) -> None:
        LayerShell.init_for_window(self)
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_namespace(self, "quill")
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
        # -1 opts out of other layers' exclusive zones, so the bar does not
        # push the popup down away from the cursor.
        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
        self._place_on_cursor_monitor()
        self._position(MENU_WIDTH, 320)

    def _place_on_cursor_monitor(self) -> None:
        """Pin the surface to the output the pointer is on.

        Without this the compositor picks the focused output, which is the wrong
        one whenever the pointer has wandered to a second monitor.
        """
        self._cursor = hypr.cursor_pos()
        self._monitor = hypr.monitor_at(*self._cursor) or {}
        connector = self._monitor.get("name")
        if not connector:
            return
        display = Gdk.Display.get_default()
        if display is None:
            return
        for gdk_monitor in display.get_monitors():
            if gdk_monitor.get_connector() == connector:
                LayerShell.set_monitor(self, gdk_monitor)
                return

    def _position(self, width: int, height: int) -> None:
        """Offset from the monitor's top-left, clamped to stay fully on screen."""
        scale = self._monitor.get("scale") or 1.0
        mon_w = int(self._monitor.get("width", 1920) / scale)
        mon_h = int(self._monitor.get("height", 1080) / scale)
        x = self._cursor[0] - self._monitor.get("x", 0)
        y = self._cursor[1] - self._monitor.get("y", 0)

        x = max(EDGE_PAD, min(x, mon_w - width - EDGE_PAD))
        # Prefer opening below the cursor; flip above when it would overflow.
        if y + height + EDGE_PAD > mon_h:
            y = max(EDGE_PAD, y - height)
        y = max(EDGE_PAD, min(y, mon_h - height - EDGE_PAD))

        LayerShell.set_margin(self, LayerShell.Edge.LEFT, int(x))
        LayerShell.set_margin(self, LayerShell.Edge.TOP, int(y))

    # -- widgets -----------------------------------------------------------
    def _build(self) -> None:
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card.add_css_class("quill-card")
        self.set_content(self.card)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.add_css_class("quill-header")
        self.snippet_label = Gtk.Label(label=_snippet(self.source_text), xalign=0)
        self.snippet_label.add_css_class("quill-snippet")
        self.snippet_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.snippet_label.set_max_width_chars(40)
        words = len(self.source_text.split())
        self.meta_label = Gtk.Label(
            label=f"{words} word{'s' if words != 1 else ''} · {self.cfg.model}", xalign=0
        )
        self.meta_label.add_css_class("quill-meta")
        header.append(self.snippet_label)
        header.append(self.meta_label)
        self.card.append(header)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(90)
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(False)
        self.card.append(self.stack)

        self.stack.add_named(self._build_menu(), "menu")
        self.stack.add_named(self._build_result(), "result")
        self.stack.set_visible_child_name("menu")

    def _build_menu(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("quill-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.listbox.connect("row-activated", self._on_row_activated)

        for index, action in enumerate(self.cfg.actions):
            row = Gtk.ListBoxRow()
            row.action = action
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            key = Gtk.Label(label=str(index + 1) if index < 9 else "", xalign=0.5)
            key.add_css_class("quill-key")
            label = Gtk.Label(label=action.label, xalign=0)
            label.set_hexpand(True)
            line.append(key)
            line.append(label)
            row.set_child(line)
            self.listbox.append(row)

        box.append(self.listbox)

        self.custom_entry = Gtk.Entry()
        self.custom_entry.set_placeholder_text("Tell Quill what to do…")
        self.custom_entry.set_margin_start(10)
        self.custom_entry.set_margin_end(10)
        self.custom_entry.set_margin_bottom(10)
        self.custom_entry.set_visible(False)
        self.custom_entry.connect("activate", self._on_custom_activate)
        box.append(self.custom_entry)

        footer = Gtk.Label(label="↑↓ choose · ⏎ run · esc close", xalign=0)
        footer.add_css_class("quill-meta")
        footer.add_css_class("quill-footer")
        box.append(footer)

        first = self.listbox.get_row_at_index(0)
        if first:
            self.listbox.select_row(first)
        box.set_size_request(MENU_WIDTH, -1)
        return box

    def _build_result(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_min_content_height(70)
        scroller.set_max_content_height(RESULT_HEIGHT - 110)
        scroller.set_vexpand(True)
        scroller.set_margin_start(12)
        scroller.set_margin_end(12)
        scroller.set_margin_top(10)

        # Editable on purpose: a near-miss is faster to fix here than to redo.
        self.result_view = Gtk.TextView()
        self.result_view.add_css_class("quill-result")
        self.result_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.result_buffer = self.result_view.get_buffer()
        scroller.set_child(self.result_view)
        box.append(scroller)

        self.error_label = Gtk.Label(label="", xalign=0, wrap=True)
        self.error_label.add_css_class("quill-error")
        self.error_label.set_margin_start(12)
        self.error_label.set_margin_end(12)
        self.error_label.set_visible(False)
        box.append(self.error_label)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("quill-footer")

        self.spinner = Gtk.Spinner()
        footer.append(self.spinner)
        self.status_label = Gtk.Label(label="", xalign=0)
        self.status_label.add_css_class("quill-meta")
        self.status_label.set_hexpand(True)
        footer.append(self.status_label)

        self.retry_button = Gtk.Button(label="Retry")
        self.retry_button.connect("clicked", lambda *_: self._run_action())
        footer.append(self.retry_button)

        self.copy_button = Gtk.Button(label="Copy")
        self.copy_button.connect("clicked", lambda *_: self._on_copy())
        footer.append(self.copy_button)

        self.replace_button = Gtk.Button(label="Replace")
        self.replace_button.add_css_class("suggested-action")
        self.replace_button.connect("clicked", lambda *_: self._on_replace())
        footer.append(self.replace_button)

        box.append(footer)
        box.set_size_request(RESULT_WIDTH, -1)
        return box

    # -- keyboard ----------------------------------------------------------
    def _install_keys(self) -> None:
        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_key)
        self.add_controller(controller)

    def _on_key(self, _controller, keyval, _keycode, _state) -> bool:
        name = Gdk.keyval_name(keyval) or ""
        if name == "Escape":
            if self.stack.get_visible_child_name() == "result":
                self._cancel_stream()
                self.stack.set_visible_child_name("menu")
                self._position(MENU_WIDTH, 320)
                return True
            self._dismiss()
            return True

        if self.stack.get_visible_child_name() == "menu":
            if self.custom_entry.get_visible() and self.custom_entry.has_focus():
                return False
            if name in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
                row = self.listbox.get_row_at_index(int(name) - 1)
                if row:
                    self.listbox.select_row(row)
                    self._on_row_activated(self.listbox, row)
                return True
        else:
            if name == "Return" and not self.result_view.has_focus():
                self._on_replace()
                return True
        return False

    # -- flow --------------------------------------------------------------
    def _on_row_activated(self, _listbox, row) -> None:
        action = getattr(row, "action", None)
        if action is None:
            return
        if action.prompts_for_input and not self.custom_entry.get_text().strip():
            self.current_action = action
            self.custom_entry.set_visible(True)
            self.custom_entry.grab_focus()
            return
        self.current_action = action
        self.current_custom = self.custom_entry.get_text()
        self._start_result_view()

    def _on_custom_activate(self, entry) -> None:
        if not entry.get_text().strip():
            return
        self.current_custom = entry.get_text()
        self._start_result_view()

    def _start_result_view(self) -> None:
        self.stack.set_visible_child_name("result")
        # Clamp against the worst-case height: the real one is not known
        # until the answer has streamed in.
        self._position(RESULT_WIDTH, RESULT_HEIGHT)
        self._run_action()

    def _run_action(self) -> None:
        action = self.current_action
        if action is None:
            return
        self._cancel_stream()
        self._result_chars = []
        self.result_buffer.set_text("")
        self.error_label.set_visible(False)
        self.spinner.start()
        self.status_label.set_label(f"{action.label}…")
        self.replace_button.set_sensitive(False)

        state.write(state.WORKING, action.label)
        self.cancel_event = threading.Event()
        messages = build_messages(action, self.source_text, self.current_custom)
        thread = threading.Thread(
            target=self._stream_worker,
            args=(messages, action.temperature, self.cancel_event),
            daemon=True,
        )
        thread.start()

    def _stream_worker(self, messages, temperature, cancel) -> None:
        try:
            for delta in ollama.stream_chat(self.cfg, messages, temperature, cancel):
                if cancel.is_set():
                    return
                GLib.idle_add(self._append_delta, delta)
        except ollama.OllamaError as exc:
            GLib.idle_add(self._on_stream_error, str(exc))
            return
        if not cancel.is_set():
            GLib.idle_add(self._on_stream_done)

    def _append_delta(self, delta: str) -> bool:
        self._result_chars.append(delta)
        # Sanitise as we go so reasoning blocks never flash on screen.
        shown = ollama.clean_output("".join(self._result_chars))
        self.result_buffer.set_text(shown)
        end = self.result_buffer.get_end_iter()
        self.result_view.scroll_to_iter(end, 0.0, False, 0.0, 0.0)
        return False

    def _on_stream_done(self) -> bool:
        state.write(state.IDLE)
        self.spinner.stop()
        final = ollama.clean_output("".join(self._result_chars), self.source_text)
        self.result_buffer.set_text(final)
        self.status_label.set_label("⏎ to replace")
        self.replace_button.set_sensitive(True)
        if self.cfg.auto_replace:
            self._on_replace()
            return False
        self.replace_button.grab_focus()
        return False

    def _on_stream_error(self, message: str) -> bool:
        state.write(state.IDLE)
        self.spinner.stop()
        self.status_label.set_label("Failed")
        self.error_label.set_label(message)
        self.error_label.set_visible(True)
        self.replace_button.set_sensitive(False)
        return False

    def _cancel_stream(self) -> None:
        state.write(state.IDLE)
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.cancel_event = None
        self.spinner.stop()

    def _result_text(self) -> str:
        start, end = self.result_buffer.get_bounds()
        return self.result_buffer.get_text(start, end, False)

    def _on_copy(self) -> None:
        text = self._result_text()
        if text.strip():
            clipboard.set_clipboard(text)
            hypr.notify("Copied to clipboard")
        self._dismiss(restore_clipboard=False)

    def _on_replace(self) -> None:
        text = self._result_text()
        if not text.strip():
            return
        self._cancel_stream()
        # The paste chord follows keyboard focus, and this surface currently owns
        # it, so the window must be gone before the chord is sent.
        self._close_surface()
        GLib.timeout_add(60, self._do_replace, text)

    def _do_replace(self, text: str) -> bool:
        clipboard.replace_selection(
            text,
            self.target_window,
            self.saved_clipboard,
            restore_clipboard=self.cfg.restore_clipboard,
        )
        self.get_application().quit()
        return False

    def _close_surface(self) -> None:
        self.set_visible(False)

    def _dismiss(self, restore_clipboard: bool = True) -> None:
        self._cancel_stream()
        self._close_surface()
        # capture_selection may have overwritten the clipboard via the copy
        # fallback; put it back even when the user changes their mind.
        if restore_clipboard and self.cfg.restore_clipboard and self.saved_clipboard is not None:
            clipboard.set_clipboard(self.saved_clipboard)
        app = self.get_application()
        if app is not None:
            app.quit()


def run(cfg: Config, text: str, win: dict | None, saved_clipboard: str | None) -> int:
    # NON_UNIQUE: single-instance is handled by the pidfile in cli.py, and a
    # unique id would make a relaunch activate the stale process instead.
    app = Adw.Application(application_id="com.omarchy.Quill",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def on_activate(application):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        window = QuillWindow(application, cfg, text, win, saved_clipboard)
        window.present()
        window.listbox.grab_focus()

    app.connect("activate", on_activate)
    return app.run([])
