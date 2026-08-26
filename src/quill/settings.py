"""The settings window: a normal libadwaita preferences window.

Separate from ui.py on purpose. The popup is a layer surface that must appear
at the cursor and vanish; this is an ordinary application window, and mixing
the two would mean one class juggling two very different lifecycles.
"""

from __future__ import annotations

import threading
from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import config as config_mod  # noqa: E402
from . import credentials, ollama, openrouter, provider  # noqa: E402
from .actions import DEFAULT_ACTIONS, Action  # noqa: E402
from .config import OLLAMA, OPENROUTER, Config  # noqa: E402

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

        self._build_provider_group()
        self._build_openrouter_group()
        self._build_model_group()
        self._build_behaviour_group()
        self._build_actions_group()
        self._sync_account_row()
        self._sync_provider()


    # -- provider ----------------------------------------------------------
    def _build_provider_group(self) -> None:
        group = Adw.PreferencesGroup(title="Where edits run")
        self.page.add(group)

        self.provider_row = Adw.ComboRow(title="Provider")
        self.provider_row.set_model(Gtk.StringList.new([
            "On this machine (Ollama)",
            "OpenRouter (cloud)",
        ]))
        self.provider_row.set_selected(1 if self.cfg.uses_openrouter else 0)
        self.provider_row.connect("notify::selected", lambda *_: self._sync_provider())
        group.add(self.provider_row)

        self.privacy_row = Adw.ActionRow()
        self.privacy_row.set_use_markup(False)
        self.privacy_row.set_title("Privacy")
        group.add(self.privacy_row)

        self.status_row = Adw.ActionRow()
        self.status_row.set_use_markup(False)
        self.status_row.set_title("Status")
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_valign(Gtk.Align.CENTER)
        refresh.add_css_class("flat")
        refresh.set_tooltip_text("Re-check the backend")
        refresh.connect("clicked", lambda *_: self._refresh_status(reload_models=True))
        self.status_row.add_suffix(refresh)
        group.add(self.status_row)


    def _provider_value(self) -> str:
        return OPENROUTER if self.provider_row.get_selected() == 1 else OLLAMA

    def _sync_provider(self) -> None:
        """Grey out the group that is not in use, rather than hiding it."""
        using_openrouter = self._provider_value() == OPENROUTER
        self.openrouter_group.set_sensitive(using_openrouter)
        self.model_group.set_sensitive(not using_openrouter)
        self.privacy_row.set_subtitle(
            "Selected text is sent to OpenRouter and to whichever provider "
            "serves the model. Free models are often trained on."
            if using_openrouter else
            "Nothing leaves this machine."
        )
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

    # -- model -------------------------------------------------------------
    # `think` is deliberately not exposed here. There is one correct value for
    # an editing tool and the switch was only ever a foot-gun; it stays
    # hand-editable in config.toml for anyone who needs it.
    def _build_model_group(self) -> None:
        group = Adw.PreferencesGroup(title="Local model (Ollama)")
        self.model_group = group
        self.page.add(group)

        self.model_row = Adw.ComboRow(title="Model")
        self.model_row.set_subtitle("Anything you have pulled in Ollama")
        group.add(self.model_row)
        self._reload_model_list()

        self.host_row = Adw.EntryRow(title="Ollama host")
        self.host_row.set_text(self.cfg.host)
        group.add(self.host_row)

        self.keep_alive_row = Adw.ComboRow(title="Keep in memory")
        self.keep_alive_row.set_subtitle(KEEP_ALIVE_HINT)
        self.keep_alive_values = list(KEEP_ALIVE_PRESETS)
        if self.cfg.keep_alive not in self.keep_alive_values:
            self.keep_alive_values.insert(0, self.cfg.keep_alive)
        self.keep_alive_row.set_model(Gtk.StringList.new(self.keep_alive_values))
        self.keep_alive_row.set_selected(self.keep_alive_values.index(self.cfg.keep_alive))
        group.add(self.keep_alive_row)

        self.ctx_row = Adw.SpinRow(
            title="Context window",
            adjustment=Gtk.Adjustment(
                lower=1024, upper=131072, step_increment=1024, page_increment=4096,
                value=self.cfg.num_ctx,
            ),
        )
        group.add(self.ctx_row)

    def _reload_model_list(self) -> None:
        names = ollama.installed_models(self.cfg)
        # Keep the configured model selectable even when Ollama is unreachable
        # or the model was removed, so opening settings never silently changes it.
        if self.cfg.model not in names:
            names.insert(0, self.cfg.model)
        self.model_names = names or [self.cfg.model]
        self.model_row.set_model(Gtk.StringList.new(self.model_names))
        try:
            self.model_row.set_selected(self.model_names.index(self.cfg.model))
        except ValueError:
            self.model_row.set_selected(0)

    def _refresh_status(self, reload_models: bool = False) -> None:
        if not hasattr(self, "status_row"):
            return  # called from _sync_provider before the model group exists
        if reload_models:
            self._reload_model_list()
        probe = self._collect()
        usable, reason = provider.ready(probe)
        self.status_row.set_subtitle(reason)
        if usable:
            self.status_row.remove_css_class("error")
        else:
            self.status_row.add_css_class("error")

    def _selected_model(self) -> str:
        index = self.model_row.get_selected()
        if 0 <= index < len(self.model_names):
            return self.model_names[index]
        return self.cfg.model

    # -- behaviour ---------------------------------------------------------
    def _build_behaviour_group(self) -> None:
        group = Adw.PreferencesGroup(title="Behaviour")
        self.page.add(group)

        self.auto_row = Adw.SwitchRow(
            title="Replace without reviewing",
            subtitle="Paste as soon as the model finishes, skipping the result panel.",
        )
        self.auto_row.set_active(self.cfg.auto_replace)
        group.add(self.auto_row)

        self.clipboard_row = Adw.SwitchRow(
            title="Restore the clipboard",
            subtitle="Put back whatever was on the clipboard after replacing text.",
        )
        self.clipboard_row.set_active(self.cfg.restore_clipboard)
        group.add(self.clipboard_row)

        self.timeout_row = Adw.SpinRow(
            title="Request timeout (seconds)",
            adjustment=Gtk.Adjustment(
                lower=10, upper=600, step_increment=10, page_increment=30,
                value=self.cfg.request_timeout,
            ),
        )
        group.add(self.timeout_row)

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
    app.connect("activate", lambda a: SettingsWindow(a, cfg).present())
    return app.run([])
