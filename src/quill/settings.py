"""The settings window: a normal libadwaita preferences window.

Separate from ui.py on purpose. The popup is a layer surface that must appear
at the cursor and vanish; this is an ordinary application window, and mixing
the two would mean one class juggling two very different lifecycles.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import config as config_mod  # noqa: E402
from . import codex, credentials, hypr, models, ollama, openai_api, openrouter  # noqa: E402
from . import clipboard, provider, sanitize  # noqa: E402
from .actions import DEFAULT_ACTIONS, Action, build_messages  # noqa: E402
from .config import CODEX, OLLAMA, OPENAI, OPENROUTER, Config  # noqa: E402

CSS = """
.quill-hero {
  padding: 22px 12px 18px 12px;
}
/* The tutorial is a section in its own right, not loose content under a
   heading, so it gets a container of its own. */
.quill-tutorial {
  padding: 14px 16px 16px 16px;
}
/* Status lines that report rather than invite a click. */
.quill-secondary {
  padding: 8px 4px 0 4px;
}
/* Keycaps. The thicker bottom border is what reads as a physical key. */
.quill-keycap {
  background-color: #0b0b0d;
  color: #f2f2f4;
  border: 1px solid alpha(#ffffff, 0.16);
  border-bottom: 3px solid #000000;
  border-radius: 8px;
  padding: 5px 12px;
  font-weight: 800;
}
.quill-keycap-lg {
  font-size: 2.1em;
  padding: 10px 22px;
  border-radius: 12px;
  border-bottom-width: 5px;
}
.quill-plus {
  font-size: 1.3em;
  font-weight: 700;
  opacity: 0.45;
}
.quill-plus-lg { font-size: 2.0em; }
.quill-sample text {
  background: transparent;
}
.quill-sample-frame {
  border: 1px solid alpha(currentColor, 0.16);
  border-radius: 9px;
  padding: 8px 10px;
}
.quill-result-frame {
  border: 1px solid alpha(@accent_bg_color, 0.45);
  background-color: alpha(@accent_bg_color, 0.10);
  border-radius: 9px;
  padding: 8px 10px;
}
.quill-was {
  opacity: 0.55;
  text-decoration: line-through;
}
.quill-step {
  font-weight: 700;
  font-size: 0.78em;
  opacity: 0.55;
  letter-spacing: 0.8px;
}
"""

KEEP_ALIVE_HINT = ("How long the model stays in VRAM. Longer keeps repeat "
                   "edits instant; 0 frees the GPU immediately.")
KEEP_ALIVE_PRESETS = ["0", "5m", "30m", "1h", "24h", "-1"]


class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app, cfg: Config):
        super().__init__(application=app, title="Quill Settings")
        self.cfg = cfg
        # Edited as plain dataclasses and only written back on Save, so
        # Cancel-by-closing leaves the file untouched.
        self.draft_actions: list[Action] = list(cfg.actions)
        # _collect() reads every widget, so anything that calls it must wait
        # until construction has finished. Guarding on individual widgets meant
        # tracking build order by hand, which broke as soon as it changed.
        self._ready = False

        self.set_default_size(660, 780)

        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()

        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._on_save)
        header.pack_end(save)

        toolbar.add_top_bar(header)
        toolbar.set_content(self.toasts)
        self.set_content(toolbar)

        self.page = Adw.PreferencesPage()
        self.toasts.set_child(self.page)

        self._build_hero()
        self._build_tutorial()
        self._build_shortcuts_group()
        self._build_provider_group()
        self._build_openai_group()
        self._build_codex_group()
        self._build_openrouter_group()
        self._build_model_group()
        self._build_behaviour_group()
        self._build_actions_group()

        self._ready = True
        self._sync_account_row()
        self._sync_provider()



    # -- shortcuts ---------------------------------------------------------
    # In-app bindings are fixed, so they are listed literally; the Hyprland
    # chords are read live, because the user may well have rebound them.
    IN_APP_SHORTCUTS = [
        ("Click / right-click the bar icon", "Settings / run an edit"),
        ("1\u20139,  \u2191\u2193 then \u21b5", "Pick an edit in the menu"),
        ("\u21b5 / Esc", "Replace the selection / close"),
    ]

    @staticmethod
    def _keycaps(chord: str, large: bool = False) -> Gtk.Box:
        """Render "Super + Shift + I" as separate keys rather than a string."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                      spacing=10 if large else 6)
        box.set_halign(Gtk.Align.CENTER)
        parts = [p.strip() for p in chord.split("+") if p.strip()]
        for index, part in enumerate(parts):
            if index:
                plus = Gtk.Label(label="+")
                plus.add_css_class("quill-plus")
                if large:
                    plus.add_css_class("quill-plus-lg")
                plus.set_valign(Gtk.Align.CENTER)
                box.append(plus)
            cap = Gtk.Label(label=part)
            cap.add_css_class("quill-keycap")
            if large:
                cap.add_css_class("quill-keycap-lg")
            cap.set_valign(Gtk.Align.CENTER)
            box.append(cap)
        return box

    def _build_hero(self) -> None:
        """The one thing to remember, at the size of the thing to remember."""
        group = Adw.PreferencesGroup()
        self.page.add(group)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("quill-hero")
        box.set_halign(Gtk.Align.CENTER)

        binds = hypr.binds_matching("quill")
        primary = next((c for c, w in binds if "menu" in w.lower()), "Super + I")
        secondary = next((c for c, w in binds if "menu" not in w.lower()), None)

        box.append(self._keycaps(primary, large=True))

        caption = Gtk.Label(label="Select text in any app, then press this")
        caption.add_css_class("body")
        caption.add_css_class("dim-label")
        box.append(caption)

        if secondary:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_halign(Gtk.Align.CENTER)
            row.set_margin_top(14)
            row.append(self._keycaps(secondary))
            note = Gtk.Label(label="fix grammar in place, no popup")
            note.add_css_class("caption")
            note.add_css_class("dim-label")
            note.set_valign(Gtk.Align.CENTER)
            row.append(note)
            box.append(row)

        group.add(box)

    # -- interactive tutorial ----------------------------------------------
    SAMPLE = "i has recieved you're mesage yesterday and we was gonna reply."

    def _build_tutorial(self) -> None:
        """A rehearsal of the real thing, not a simulation of it.

        The steps are driven by what the user actually does: selecting the text
        arms step two, and the buffer changing under the paste completes it.
        Nothing here fakes the pipeline -- pressing the real chord runs the real
        popup, which pastes back into this very field.
        """
        group = Adw.PreferencesGroup(
            title="Try it here",
            description="Three steps, using the real shortcut on real text.",
        )
        self.page.add(group)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("card")
        box.add_css_class("quill-tutorial")
        box.set_margin_top(6)

        # --- step 1 -------------------------------------------------------
        self.step1 = self._step_header(1, "Select the text below")
        box.append(self.step1)

        self.sample_view = Gtk.TextView()
        self.sample_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.sample_view.add_css_class("quill-sample")
        buffer = self.sample_view.get_buffer()
        buffer.set_text(self.SAMPLE)
        self.tutorial_before = self.SAMPLE
        buffer.connect("notify::has-selection", lambda *_: self._tutorial_selection())
        buffer.connect("changed", lambda *_: self._tutorial_changed())
        frame = Gtk.Box()
        frame.add_css_class("quill-sample-frame")
        self.sample_view.set_hexpand(True)
        frame.append(self.sample_view)
        box.append(frame)

        # --- step 2 -------------------------------------------------------
        self.step2 = self._step_header(2, "Press the shortcut")
        self.step2.set_margin_top(10)
        box.append(self.step2)

        binds = hypr.binds_matching("quill")
        chord = next((c for c, w in binds if "menu" in w.lower()), "Super + I")
        press = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        press.set_margin_start(26)
        press.append(self._keycaps(chord))
        hint = Gtk.Label(label='then choose "Fix Spelling & Grammar"')
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        hint.set_valign(Gtk.Align.CENTER)
        press.append(hint)
        box.append(press)

        self.reset_button = Gtk.Button(label="Reset the sample")
        self.reset_button.add_css_class("flat")
        self.reset_button.set_halign(Gtk.Align.START)
        self.reset_button.set_margin_start(22)
        self.reset_button.set_visible(False)
        self.reset_button.connect("clicked", lambda *_: self._reset_tutorial())
        box.append(self.reset_button)

        # --- step 3 -------------------------------------------------------
        self.step3 = self._step_header(3, "Quill replaces what you selected")
        self.step3.set_margin_top(10)
        box.append(self.step3)

        self.diff_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.diff_box.add_css_class("quill-result-frame")
        self.diff_before = Gtk.Label(xalign=0, wrap=True)
        self.diff_before.add_css_class("caption")
        self.diff_before.add_css_class("quill-was")
        self.diff_after = Gtk.Label(xalign=0, wrap=True)
        self.diff_after.add_css_class("body")
        self.diff_box.append(self.diff_before)
        self.diff_box.append(self.diff_after)
        box.append(self.diff_box)

        self.diff_placeholder = Gtk.Label(
            label="Nothing yet — do steps 1 and 2.", xalign=0)
        self.diff_placeholder.add_css_class("caption")
        self.diff_placeholder.add_css_class("dim-label")
        box.append(self.diff_placeholder)

        group.add(box)
        self._set_step_state(1, "todo")
        self._set_step_state(2, "waiting")
        self._set_step_state(3, "waiting")
        self.diff_box.set_visible(False)

    def _step_header(self, number: int, text: str) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Image.new_from_icon_name("radio-symbolic")
        icon.set_valign(Gtk.Align.CENTER)
        label = Gtk.Label(label=f"{number}  {text.upper()}", xalign=0)
        label.add_css_class("quill-step")
        row.append(icon)
        row.append(label)
        row._icon = icon
        row._label = label
        return row

    def _set_step_state(self, number: int, state: str) -> None:
        row = {1: self.step1, 2: self.step2, 3: self.step3}[number]
        icon, label = row._icon, row._label
        for css in ("accent", "success", "dim-label"):
            icon.remove_css_class(css)
            label.remove_css_class(css)
        if state == "done":
            icon.set_from_icon_name("emblem-ok-symbolic")
            icon.add_css_class("success")
            label.add_css_class("success")
        elif state == "todo":
            icon.set_from_icon_name("go-next-symbolic")
            icon.add_css_class("accent")
            label.add_css_class("accent")
        else:
            icon.set_from_icon_name("radio-symbolic")
            icon.add_css_class("dim-label")
            label.add_css_class("dim-label")

    def _tutorial_selection(self) -> None:
        """Selecting the sample is what arms step two."""
        buffer = self.sample_view.get_buffer()
        if not buffer.get_has_selection():
            return
        start, end = buffer.get_selection_bounds()
        selected = buffer.get_text(start, end, False)
        if len(selected.strip()) < 4:
            return
        self.tutorial_before = selected
        self._set_step_state(1, "done")
        self._set_step_state(2, "todo")
        self.reset_button.set_visible(True)

    def _tutorial_changed(self) -> None:
        """The buffer changing under the paste is what completes step three."""
        buffer = self.sample_view.get_buffer()
        current = buffer.get_text(*buffer.get_bounds(), False)
        if current.strip() == self.SAMPLE.strip() or not current.strip():
            return
        if not self.tutorial_before or self.tutorial_before.strip() == current.strip():
            return
        self._set_step_state(2, "done")
        self._set_step_state(3, "done")
        self.diff_placeholder.set_visible(False)
        self.diff_box.set_visible(True)
        self.diff_before.set_label(f"was:  {self.tutorial_before.strip()}")
        self.diff_after.set_label(f"now:  {current.strip()}")

    def _reset_tutorial(self) -> None:
        self.sample_view.get_buffer().set_text(self.SAMPLE)
        self.tutorial_before = self.SAMPLE
        self._set_step_state(1, "todo")
        self._set_step_state(2, "waiting")
        self._set_step_state(3, "waiting")
        self.diff_box.set_visible(False)
        self.diff_placeholder.set_visible(True)
        self.reset_button.set_visible(False)

    def _build_shortcuts_group(self) -> None:
        """Reference, not a setting.

        This used to be eight always-open rows, which pushed the thing people
        actually come here to change below the fold. It is now one row: the two
        chords that matter are in the subtitle, the rest is one click away.
        """
        group = Adw.PreferencesGroup()
        self.page.add(group)

        expander = Adw.ExpanderRow(title="Shortcuts")
        expander.set_use_markup(False)
        expander.add_prefix(Gtk.Image.new_from_icon_name("input-keyboard-symbolic"))
        group.add(expander)

        # Several chords can share one description; list them on one line
        # rather than repeating the description per chord.
        grouped: dict[str, list[str]] = {}
        for chord, what in hypr.binds_matching("quill"):
            label = what[0].upper() + what[1:] if what else "Open Quill"
            grouped.setdefault(label, []).append(chord)
        rows = [(",  ".join(chords), label) for label, chords in grouped.items()]
        if not rows:
            rows.append(("Super + I", "Not currently bound"))

        headline = "  ·  ".join(f"{chord} — {label.lower()}"
                                for chord, label in rows[:2])
        expander.set_subtitle(headline or "Keyboard and mouse")

        for chord, what in rows + self.IN_APP_SHORTCUTS:
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(what)
            label = Gtk.Label(label=chord)
            label.set_valign(Gtk.Align.CENTER)
            label.add_css_class("monospace")
            label.add_css_class("dim-label")
            row.add_suffix(label)
            expander.add_row(row)

    # -- provider ----------------------------------------------------------
    def _build_provider_group(self) -> None:
        group = Adw.PreferencesGroup(title="Where edits run")
        self.page.add(group)

        self.provider_row = Adw.ComboRow(title="Provider")
        self.provider_row.add_prefix(
            Gtk.Image.new_from_icon_name("network-server-symbolic"))
        self.provider_order = [OLLAMA, OPENAI, CODEX, OPENROUTER]
        self.provider_row.set_model(Gtk.StringList.new([
            "On this machine (Ollama)",
            "OpenAI-compatible server",
            "ChatGPT subscription (Codex CLI)",
            "OpenRouter (cloud)",
        ]))
        try:
            self.provider_row.set_selected(self.provider_order.index(self.cfg.provider))
        except ValueError:
            self.provider_row.set_selected(0)
        self.provider_row.connect("notify::selected", lambda *_: self._sync_provider())
        group.add(self.provider_row)

        # These report state; they are not settings. Adding a non-row widget to
        # a PreferencesGroup places it below the card, which is exactly the
        # "secondary" weight they should carry.
        secondary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        secondary.add_css_class("quill-secondary")

        status_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.status_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        self.status_icon.set_valign(Gtk.Align.START)
        self.status_label = Gtk.Label(xalign=0, wrap=True)
        self.status_label.add_css_class("caption")
        self.status_label.set_hexpand(True)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_valign(Gtk.Align.CENTER)
        refresh.add_css_class("flat")
        refresh.set_tooltip_text("Re-check the backend")
        refresh.connect("clicked", lambda *_: self._refresh_all())
        status_line.append(self.status_icon)
        status_line.append(self.status_label)
        status_line.append(refresh)
        secondary.append(status_line)

        privacy_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        privacy_icon = Gtk.Image.new_from_icon_name("security-high-symbolic")
        privacy_icon.set_valign(Gtk.Align.START)
        privacy_icon.add_css_class("dim-label")
        self.privacy_label = Gtk.Label(xalign=0, wrap=True)
        self.privacy_label.add_css_class("caption")
        self.privacy_label.add_css_class("dim-label")
        self.privacy_label.set_hexpand(True)
        privacy_line.append(privacy_icon)
        privacy_line.append(self.privacy_label)
        secondary.append(privacy_line)

        group.add(secondary)


    def _provider_value(self) -> str:
        index = self.provider_row.get_selected()
        if 0 <= index < len(self.provider_order):
            return self.provider_order[index]
        return OLLAMA

    def _sync_provider(self) -> None:
        """Show only the backend in use.

        Everything still exists, so _collect() can read it; it is just not on
        screen competing with the settings the user opened this window for.
        """
        active = self._provider_value()
        self.openrouter_group.set_visible(active == OPENROUTER)
        self.openai_group.set_visible(active == OPENAI)
        self.codex_group.set_visible(active == CODEX)
        self.model_group.set_visible(active == OLLAMA)

        probe = replace(self.cfg, provider=active,
                        openai_base_url=self._openai_base_url())
        if probe.is_local:
            note = "Nothing leaves this machine."
        elif active == CODEX:
            note = ("Selected text is sent to OpenAI through the Codex CLI, "
                    "using your ChatGPT subscription rather than metered tokens.")
        elif active == OPENROUTER:
            note = ("Selected text is sent to OpenRouter and to whichever "
                    "provider serves the model. Free models are often trained on.")
        else:
            note = f"Selected text is sent to {self._openai_base_url()}."
        self.privacy_label.set_label(note)
        self._refresh_status()

    # -- openrouter --------------------------------------------------------
    def _build_openrouter_group(self) -> None:
        self.openrouter_group = Adw.PreferencesGroup(
            title="OpenRouter",
            description="Sign in with your OpenRouter account, or paste an API key.",
        )
        self.page.add(self.openrouter_group)

        self.account_row = Adw.ActionRow()
        self.account_row.set_use_markup(False)
        self.account_row.set_title("Account")
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_valign(Gtk.Align.CENTER)
        self.signin_button = Gtk.Button(label="Sign in")
        self.signin_button.add_css_class("suggested-action")
        self.signin_button.connect("clicked", lambda *_: self._sign_in())
        self.signout_button = Gtk.Button(label="Disconnect")
        self.signout_button.connect("clicked", lambda *_: self._sign_out())
        buttons.append(self.signout_button)
        buttons.append(self.signin_button)
        self.account_row.add_suffix(buttons)
        self.openrouter_group.add(self.account_row)

        self.key_row = Adw.PasswordEntryRow(title="…or paste an API key")
        apply_key = Gtk.Button(label="Save key")
        apply_key.set_valign(Gtk.Align.CENTER)
        apply_key.connect("clicked", lambda *_: self._save_pasted_key())
        self.key_row.add_suffix(apply_key)
        self.openrouter_group.add(self.key_row)

        self.free_row = Adw.SwitchRow(
            title="Free models only",
            subtitle="Free models are rate-limited and may be slower.",
        )
        self.free_row.set_active(self.cfg.openrouter_free_only)
        self.free_row.connect("notify::active", lambda *_: self._reload_openrouter_models())
        self.openrouter_group.add(self.free_row)

        self.or_model_row = Adw.ComboRow(title="Model")
        self.openrouter_group.add(self.or_model_row)
        self._reload_openrouter_models()

    def _reload_openrouter_models(self) -> None:
        try:
            found = openrouter.models(free_only=self.free_row.get_active())
            ids = [m["id"] for m in found]
        except openrouter.OpenRouterError:
            ids = []
        if self.cfg.openrouter_model not in ids:
            ids.insert(0, self.cfg.openrouter_model)
        self.or_model_ids = ids
        self.or_model_row.set_model(Gtk.StringList.new(ids))
        try:
            self.or_model_row.set_selected(ids.index(self.cfg.openrouter_model))
        except ValueError:
            self.or_model_row.set_selected(0)
        self.or_model_row.set_subtitle(f"{len(ids)} available")

    def _selected_openrouter_model(self) -> str:
        index = self.or_model_row.get_selected()
        if 0 <= index < len(self.or_model_ids):
            return self.or_model_ids[index]
        return self.cfg.openrouter_model

    def _sync_account_row(self) -> None:
        source = openrouter.key_source()
        connected = bool(source)
        self.account_row.set_subtitle(
            f"Connected · key stored in {source}" if connected
            else "Not connected"
        )
        self.signin_button.set_label("Sign in again" if connected else "Sign in")
        self.signout_button.set_sensitive(connected)

    def _sign_in(self) -> None:
        self.signin_button.set_sensitive(False)
        self._toast("Opening your browser…")

        def worker():
            try:
                openrouter.login()
                GLib.idle_add(self._sign_in_done, None)
            except openrouter.OpenRouterError as exc:
                GLib.idle_add(self._sign_in_done, str(exc))

        # The loopback server blocks until the redirect arrives, so it cannot
        # run on the main loop without freezing the window.
        threading.Thread(target=worker, daemon=True).start()

    def _sign_in_done(self, error: str | None) -> bool:
        self.signin_button.set_sensitive(True)
        self._toast(error or "Connected to OpenRouter")
        self._sync_account_row()
        self._reload_openrouter_models()
        self._refresh_status()
        return False

    def _sign_out(self) -> None:
        openrouter.forget_key()
        self._sync_account_row()
        self._refresh_status()
        self._toast("Disconnected. Revoke the key at openrouter.ai/settings/keys")

    def _save_pasted_key(self) -> None:
        value = self.key_row.get_text().strip()
        if not value:
            self._toast("Paste a key first")
            return
        where = openrouter.store_key(value)
        self.key_row.set_text("")
        self._toast(f"Key saved to {where}")
        self._sync_account_row()
        self._reload_openrouter_models()
        self._refresh_status()


    # -- openai-compatible -------------------------------------------------
    def _build_openai_group(self) -> None:
        self.openai_group = Adw.PreferencesGroup(
            title="OpenAI-compatible server",
            description=("LM Studio, llama.cpp, vLLM, LocalAI — or api.openai.com. "
                         "If you are pointing this at Ollama, prefer the Ollama "
                         "provider instead: this API has no way to switch off "
                         "reasoning, which makes short edits much slower."),
        )
        self.page.add(self.openai_group)

        self.preset_row = Adw.ComboRow(title="Preset")
        self.preset_names = ["Custom…"] + list(openai_api.PRESETS)
        self.preset_row.set_model(Gtk.StringList.new(self.preset_names))
        self.preset_row.connect("notify::selected", lambda *_: self._apply_preset())
        self.openai_group.add(self.preset_row)

        self.base_url_row = Adw.EntryRow(title="Base URL")
        self.base_url_row.set_text(self.cfg.openai_base_url)
        self.base_url_row.connect("changed", lambda *_: self._sync_openai_key_row())
        self.openai_group.add(self.base_url_row)

        self.openai_model_row = Adw.EntryRow(title="Model")
        self.openai_model_row.set_text(self.cfg.openai_model)
        self.openai_group.add(self.openai_model_row)

        self.openai_key_row = Adw.PasswordEntryRow(title="API key")
        save_key = Gtk.Button(label="Save key")
        save_key.set_valign(Gtk.Align.CENTER)
        save_key.connect("clicked", lambda *_: self._save_openai_key())
        self.openai_key_row.add_suffix(save_key)
        self.openai_group.add(self.openai_key_row)

        fetch = Gtk.Button(label="List models")
        fetch.set_valign(Gtk.Align.CENTER)
        fetch.connect("clicked", lambda *_: self._list_openai_models())
        self.openai_models_row = Adw.ActionRow()
        self.openai_models_row.set_use_markup(False)
        self.openai_models_row.set_title("Available models")
        self.openai_models_row.set_subtitle("Not checked yet")
        self.openai_models_row.add_suffix(fetch)
        self.openai_group.add(self.openai_models_row)

        self._sync_openai_key_row()

    def _openai_base_url(self) -> str:
        return self.base_url_row.get_text().strip() or self.cfg.openai_base_url

    def _apply_preset(self) -> None:
        index = self.preset_row.get_selected()
        if index <= 0:
            return
        name = self.preset_names[index]
        self.base_url_row.set_text(openai_api.PRESETS[name])
        self._sync_openai_key_row()

    def _sync_openai_key_row(self) -> None:
        needs = openai_api.needs_key(self._openai_base_url())
        stored = openai_api.key_source()
        self.openai_key_row.set_visible(True)
        if stored:
            self.openai_key_row.set_title(f"API key (stored in {stored})")
        else:
            self.openai_key_row.set_title(
                "API key" if needs else "API key (usually not needed locally)")

    def _save_openai_key(self) -> None:
        value = self.openai_key_row.get_text().strip()
        if not value:
            openai_api.forget_key()
            self._toast("API key cleared")
        else:
            where = openai_api.store_key(value)
            self._toast(f"Key saved to {where}")
        self.openai_key_row.set_text("")
        self._sync_openai_key_row()
        self._refresh_status()

    def _list_openai_models(self) -> None:
        probe = self._collect()
        try:
            names = openai_api.models(probe)
        except openai_api.OpenAIError as exc:
            self.openai_models_row.set_subtitle(str(exc))
            return
        self.openai_models_row.set_subtitle(
            ", ".join(names[:6]) + (f" … ({len(names)} total)" if len(names) > 6 else "")
            if names else "The server reported no models"
        )

    # -- codex -------------------------------------------------------------
    def _build_codex_group(self) -> None:
        self.codex_group = Adw.PreferencesGroup(
            title="ChatGPT subscription",
            description=(
                "Runs edits through OpenAI's own Codex CLI, signed in with your "
                "ChatGPT account. That bills against your plan instead of "
                "metered API tokens."
            ),
        )
        self.page.add(self.codex_group)

        self.codex_status_row = Adw.ActionRow()
        self.codex_status_row.set_use_markup(False)
        self.codex_status_row.set_title("Codex CLI")
        signin = Gtk.Button(label="Sign in")
        signin.set_valign(Gtk.Align.CENTER)
        signin.connect("clicked", lambda *_: self._codex_login())
        self.codex_status_row.add_suffix(signin)
        self.codex_group.add(self.codex_status_row)

        self.codex_model_row = Adw.EntryRow(title="Model (blank = Codex default)")
        self.codex_model_row.set_text(self.cfg.codex_model)
        self.codex_group.add(self.codex_model_row)

        speed = Adw.ActionRow()
        speed.set_use_markup(False)
        speed.set_title("Note")
        speed.set_subtitle(
            "Slower than the other backends — an agent process starts per edit, "
            "so expect a few seconds, and the result appears all at once."
        )
        self.codex_group.add(speed)
        self._sync_codex_row()

    def _sync_codex_row(self) -> None:
        self.codex_status_row.set_subtitle(codex.describe())

    def _codex_login(self) -> None:
        # Codex owns this flow; launching its own login is better than
        # reimplementing an OAuth dance we would have to keep in sync.
        exe = codex.binary()
        if not exe:
            self._toast("Codex CLI is not installed")
            return
        Gio.Subprocess.new(
            ["omarchy-launch-floating-terminal-with-presentation", f"{exe} login"],
            Gio.SubprocessFlags.NONE,
        )
        self._toast("Finish signing in, then press the refresh button")

    # -- model -------------------------------------------------------------
    # `think` is deliberately not exposed here. There is one correct value for
    # an editing tool and the switch was only ever a foot-gun; it stays
    # hand-editable in config.toml for anyone who needs it.
    def _build_model_group(self) -> None:
        group = Adw.PreferencesGroup(title="Local model")
        self.model_group = group
        self.page.add(group)

        self.model_row = Adw.ComboRow(title="Model")
        self.model_row.set_use_markup(False)
        self.model_row.add_prefix(
            Gtk.Image.new_from_icon_name("applications-science-symbolic"))
        self.model_row.connect("notify::selected", lambda *_: self._on_model_changed())
        group.add(self.model_row)

        # Shown when a model Quill has actually measured is not installed, with
        # the one command needed to get it.
        self.pull_row = Adw.ActionRow()
        self.pull_row.set_use_markup(False)
        copy_pull = Gtk.Button(icon_name="edit-copy-symbolic")
        copy_pull.set_valign(Gtk.Align.CENTER)
        copy_pull.add_css_class("flat")
        copy_pull.set_tooltip_text("Copy the pull command")
        copy_pull.connect("clicked", lambda *_: self._copy_pull_command())
        self.pull_row.add_suffix(copy_pull)
        group.add(self.pull_row)

        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.detail_box.add_css_class("quill-secondary")
        detail_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        detail_icon.set_valign(Gtk.Align.START)
        detail_icon.add_css_class("dim-label")
        self.detail_label = Gtk.Label(xalign=0, wrap=True)
        self.detail_label.add_css_class("caption")
        self.detail_label.add_css_class("dim-label")
        self.detail_label.set_hexpand(True)
        self.detail_box.append(detail_icon)
        self.detail_box.append(self.detail_label)

        # Tuning knobs almost nobody touches, folded away by default.
        advanced = Adw.ExpanderRow(title="Advanced")
        advanced.add_prefix(Gtk.Image.new_from_icon_name("preferences-system-symbolic"))
        group.add(advanced)

        self.host_row = Adw.EntryRow(title="Ollama host")
        self.host_row.set_text(self.cfg.host)
        advanced.add_row(self.host_row)

        self.keep_alive_row = Adw.ComboRow(title="Keep in memory")
        self.keep_alive_row.set_subtitle(KEEP_ALIVE_HINT)
        self.keep_alive_values = list(KEEP_ALIVE_PRESETS)
        if self.cfg.keep_alive not in self.keep_alive_values:
            self.keep_alive_values.insert(0, self.cfg.keep_alive)
        self.keep_alive_row.set_model(Gtk.StringList.new(self.keep_alive_values))
        self.keep_alive_row.set_selected(self.keep_alive_values.index(self.cfg.keep_alive))
        advanced.add_row(self.keep_alive_row)

        self.ctx_row = Adw.SpinRow(
            title="Context window",
            adjustment=Gtk.Adjustment(
                lower=1024, upper=131072, step_increment=1024, page_increment=4096,
                value=self.cfg.num_ctx,
            ),
        )
        advanced.add_row(self.ctx_row)

        group.add(self.detail_box)

        self._reload_model_list()

    def _reload_model_list(self) -> None:
        installed = ollama.installed_models(self.cfg)
        # Keep the configured model selectable even when Ollama is unreachable
        # or the model was removed, so opening settings never silently changes it.
        if self.cfg.model not in installed:
            installed.append(self.cfg.model)
        self.model_ids = models.ordered(installed)
        self.model_row.set_model(
            Gtk.StringList.new([models.label_for(m) for m in self.model_ids]))
        try:
            self.model_row.set_selected(self.model_ids.index(self.cfg.model))
        except ValueError:
            self.model_row.set_selected(0)
        self._on_model_changed()
        self._sync_pull_row(installed)

    def _on_model_changed(self) -> None:
        """Explain the trade-off for whatever is selected, in place."""
        selected = self._selected_model()
        self.model_row.set_subtitle(models.describe(selected))
        self.detail_label.set_label(models.detail(selected) or models.COMPARISON)
        note = models.note_for(selected)
        if note and note.tier == models.UNSUITED:
            self.model_row.add_css_class("error")
        else:
            self.model_row.remove_css_class("error")
        self._refresh_status()

    def _sync_pull_row(self, installed: list[str]) -> None:
        missing = models.missing_recommendation(installed)
        if not missing:
            self.pull_row.set_visible(False)
            return
        note = models.note_for(missing)
        self.pull_row.set_visible(True)
        self.pull_row.set_title(f"Not installed: {missing}")
        self.pull_row.set_subtitle(
            f"{note.summary}\nollama pull {missing}" if note
            else f"ollama pull {missing}")
        self._pull_command = f"ollama pull {missing}"

    def _copy_pull_command(self) -> None:
        command = getattr(self, "_pull_command", "")
        if command:
            clipboard.set_clipboard(command)
            self._toast("Command copied")

    def _refresh_all(self) -> None:
        self._sync_codex_row()
        self._sync_openai_key_row()
        self._refresh_status(reload_models=True)

    def _refresh_status(self, reload_models: bool = False) -> None:
        if not self._ready:
            return
        if reload_models:
            self._reload_model_list()
        probe = self._collect()
        usable, reason = provider.ready(probe)
        self.status_label.set_label(reason)
        if usable:
            self.status_label.remove_css_class("error")
            self.status_label.add_css_class("dim-label")
            self.status_icon.remove_css_class("error")
            self.status_icon.add_css_class("dim-label")
            self.status_icon.set_from_icon_name("emblem-ok-symbolic")
        else:
            self.status_label.remove_css_class("dim-label")
            self.status_label.add_css_class("error")
            self.status_icon.remove_css_class("dim-label")
            self.status_icon.add_css_class("error")
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")

    def _selected_model(self) -> str:
        """The model id, not the label shown in the dropdown."""
        index = self.model_row.get_selected()
        if 0 <= index < len(self.model_ids):
            return self.model_ids[index]
        return self.cfg.model

    # -- behaviour ---------------------------------------------------------
    def _build_behaviour_group(self) -> None:
        group = Adw.PreferencesGroup(title="Behaviour")
        self.page.add(group)

        self.auto_row = Adw.SwitchRow(
            title="Replace without reviewing",
            subtitle="Paste as soon as the model finishes, skipping the result panel.",
        )
        self.auto_row.add_prefix(Gtk.Image.new_from_icon_name("document-edit-symbolic"))
        self.auto_row.set_active(self.cfg.auto_replace)
        group.add(self.auto_row)

        self.clipboard_row = Adw.SwitchRow(
            title="Restore the clipboard",
            subtitle="Put back whatever was on the clipboard after replacing text.",
        )
        self.clipboard_row.add_prefix(Gtk.Image.new_from_icon_name("edit-copy-symbolic"))
        self.clipboard_row.set_active(self.cfg.restore_clipboard)
        group.add(self.clipboard_row)

        advanced = Adw.ExpanderRow(title="Advanced")
        advanced.add_prefix(Gtk.Image.new_from_icon_name("preferences-system-symbolic"))
        group.add(advanced)

        self.timeout_row = Adw.SpinRow(
            title="Give up after (seconds)",
            adjustment=Gtk.Adjustment(
                lower=10, upper=600, step_increment=10, page_increment=30,
                value=self.cfg.request_timeout,
            ),
        )
        advanced.add_row(self.timeout_row)

    # -- actions -----------------------------------------------------------
    def _build_actions_group(self) -> None:
        self.actions_group = Adw.PreferencesGroup(
            title="Menu",
            description="What appears in the popup, in order.",
        )
        self.page.add(self.actions_group)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add = Gtk.Button(label="Add")
        add.connect("clicked", lambda *_: self._add_action())
        reset = Gtk.Button(label="Reset")
        reset.set_tooltip_text("Restore the built-in menu")
        reset.connect("clicked", lambda *_: self._reset_actions())
        box.append(reset)
        box.append(add)
        self.actions_group.set_header_suffix(box)

        self.action_rows: list[Adw.ExpanderRow] = []
        self._rebuild_action_rows()

    def _rebuild_action_rows(self) -> None:
        for row in self.action_rows:
            self.actions_group.remove(row)
        self.action_rows = []

        for index, action in enumerate(self.draft_actions):
            self.actions_group.add(self._action_row(index, action))

    def _action_row(self, index: int, action: Action) -> Adw.ExpanderRow:
        row = Adw.ExpanderRow()
        # Titles are Pango markup by default and these are user strings, so a
        # bare "&" in "Fix Spelling & Grammar" is a parse error. Disable markup
        # before setting the title, not after -- the constructor parses eagerly.
        row.set_use_markup(False)
        row.set_title(action.label or action.id)
        row.set_subtitle(action.id)
        self.action_rows.append(row)

        label_row = Adw.EntryRow(title="Label")
        label_row.set_text(action.label)
        label_row.connect("changed", lambda w, i=index: self._set_action(i, label=w.get_text()))
        label_row.connect("changed", lambda w, r=row: r.set_title(w.get_text() or "Untitled"))
        row.add_row(label_row)

        instruction_row = Adw.ActionRow(title="Instruction")
        instruction_row.set_activatable(False)
        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_size_request(-1, 96)
        view.add_css_class("card")
        view.set_top_margin(6)
        view.set_bottom_margin(6)
        view.set_left_margin(6)
        view.set_right_margin(6)
        buffer = view.get_buffer()
        buffer.set_text(action.instruction)
        buffer.connect("changed", lambda b, i=index: self._set_action(
            i, instruction=b.get_text(*b.get_bounds(), False)))
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(view)
        scroller.set_margin_top(6)
        scroller.set_margin_bottom(6)
        instruction_row.set_child(scroller)
        row.add_row(instruction_row)

        temp_row = Adw.SpinRow(
            title="Temperature",
            subtitle="0 keeps corrections literal; higher is more inventive.",
            digits=2,
            adjustment=Gtk.Adjustment(
                lower=0.0, upper=2.0, step_increment=0.05, page_increment=0.2,
                value=action.temperature,
            ),
        )
        temp_row.connect("changed", lambda w, i=index: self._set_action(
            i, temperature=float(w.get_value())))
        row.add_row(temp_row)

        ask_row = Adw.SwitchRow(
            title="Ask me what to do",
            subtitle="Prompts for a one-off instruction instead of using the text above.",
        )
        ask_row.set_active(action.prompts_for_input)
        ask_row.connect("notify::active", lambda w, _p, i=index: self._set_action(
            i, prompts_for_input=w.get_active()))
        row.add_row(ask_row)

        controls = Adw.ActionRow()
        controls.set_activatable(False)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_margin_top(6)
        buttons.set_margin_bottom(6)
        up = Gtk.Button(icon_name="go-up-symbolic")
        up.set_sensitive(index > 0)
        up.connect("clicked", lambda *_, i=index: self._move_action(i, -1))
        down = Gtk.Button(icon_name="go-down-symbolic")
        down.set_sensitive(index < len(self.draft_actions) - 1)
        down.connect("clicked", lambda *_, i=index: self._move_action(i, 1))
        remove = Gtk.Button(icon_name="user-trash-symbolic")
        remove.add_css_class("destructive-action")
        remove.connect("clicked", lambda *_, i=index: self._remove_action(i))
        buttons.append(up)
        buttons.append(down)
        buttons.append(remove)
        controls.set_child(buttons)
        row.add_row(controls)
        return row

    def _set_action(self, index: int, **fields) -> None:
        if 0 <= index < len(self.draft_actions):
            self.draft_actions[index] = replace(self.draft_actions[index], **fields)

    def _move_action(self, index: int, delta: int) -> None:
        target = index + delta
        if not (0 <= index < len(self.draft_actions) and 0 <= target < len(self.draft_actions)):
            return
        actions = self.draft_actions
        actions[index], actions[target] = actions[target], actions[index]
        self._rebuild_action_rows()

    def _remove_action(self, index: int) -> None:
        if len(self.draft_actions) <= 1:
            self._toast("The menu needs at least one edit")
            return
        del self.draft_actions[index]
        self._rebuild_action_rows()

    def _add_action(self) -> None:
        existing = {a.id for a in self.draft_actions}
        base = "custom_edit"
        new_id = base
        counter = 2
        while new_id in existing:
            new_id = f"{base}_{counter}"
            counter += 1
        self.draft_actions.append(Action(
            id=new_id, label="New edit",
            instruction="Rewrite the text.", temperature=0.3,
        ))
        self._rebuild_action_rows()

    def _reset_actions(self) -> None:
        self.draft_actions = list(DEFAULT_ACTIONS)
        self._rebuild_action_rows()
        self._toast("Menu reset to the built-in edits")

    # -- save --------------------------------------------------------------
    def _toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast.new(message))

    def _collect(self) -> Config:
        cfg = replace(self.cfg)
        cfg.provider = self._provider_value()
        cfg.model = self._selected_model()
        cfg.openrouter_model = self._selected_openrouter_model()
        cfg.openrouter_free_only = self.free_row.get_active()
        cfg.openai_base_url = self._openai_base_url()
        cfg.openai_model = (self.openai_model_row.get_text().strip()
                            or self.cfg.openai_model)
        cfg.codex_model = self.codex_model_row.get_text().strip()
        cfg.host = self.host_row.get_text().strip() or self.cfg.host
        index = self.keep_alive_row.get_selected()
        cfg.keep_alive = (self.keep_alive_values[index]
                          if 0 <= index < len(self.keep_alive_values)
                          else self.cfg.keep_alive)
        cfg.num_ctx = int(self.ctx_row.get_value())
        cfg.auto_replace = self.auto_row.get_active()
        cfg.restore_clipboard = self.clipboard_row.get_active()
        cfg.request_timeout = float(self.timeout_row.get_value())
        cfg.actions = list(self.draft_actions)
        return cfg

    def _on_save(self, _button) -> None:
        cfg = self._collect()
        try:
            path = config_mod.save(cfg)
        except OSError as exc:
            self._toast(f"Could not save: {exc}")
            return
        self.cfg = cfg
        self._toast(f"Saved to {path}")
        self._refresh_status()


def run(cfg: Config) -> int:
    app = Adw.Application(application_id="com.omarchy.Quill.Settings",
                          flags=Gio.ApplicationFlags.FLAGS_NONE)

    provider_css = Gtk.CssProvider()
    provider_css.load_from_data(CSS.encode("utf-8"))
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, provider_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def on_activate(application):
        # A unique application id means a second launch focuses this window
        # instead of opening a rival one.
        existing = application.get_active_window()
        if existing is not None:
            existing.present()
            return
        try:
            SettingsWindow(application, cfg).present()
        except Exception:
            # Without this the app keeps running windowless, holding the DBus
            # name, and every later `quill settings` silently activates the
            # corpse instead of opening a window.
            traceback.print_exc()
            application.quit()

    app.connect("activate", on_activate)
    return app.run([])
