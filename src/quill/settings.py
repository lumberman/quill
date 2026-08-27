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
from . import branding, claudecode, codex, credentials, hypr, keybindings  # noqa: E402
from . import models, ollama  # noqa: E402
from . import openai_api, openrouter  # noqa: E402
from . import clipboard, provider, sanitize  # noqa: E402
from .actions import DEFAULT_ACTIONS, Action, build_messages  # noqa: E402
from .config import CLAUDECODE, CODEX, OLLAMA, OPENAI, OPENROUTER, Config  # noqa: E402

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
.quill-badge {
  font-size: 0.74em;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background-color: alpha(currentColor, 0.13);
  letter-spacing: 0.4px;
}
.quill-secondary {
  padding: 8px 4px 0 4px;
}
/* Keycaps. A thick border-bottom renders as a flange outside the rounded
   corners, so the depth comes from stacked box-shadows instead: one hard
   offset for the key's side wall, one soft one for the shadow it casts. The
   inset highlight along the top edge is what stops a black key reading as a
   hole on a dark background. */
.quill-keycap {
  background-image: linear-gradient(180deg, #26262b 0%, #131316 60%, #0e0e11 100%);
  color: #f4f4f6;
  border: 1px solid alpha(#ffffff, 0.13);
  border-radius: 7px;
  padding: 5px 11px;
  min-width: 15px;
  font-weight: 700;
  box-shadow: inset 0 1px 0 alpha(#ffffff, 0.13),
              0 2px 0 #08080a,
              0 3px 5px alpha(#000000, 0.5);
}
.quill-keycap-lg {
  font-size: 1.75em;
  padding: 9px 20px;
  min-width: 34px;
  border-radius: 11px;
  box-shadow: inset 0 1.5px 0 alpha(#ffffff, 0.15),
              0 4px 0 #08080a,
              0 6px 10px alpha(#000000, 0.55);
}
.quill-plus {
  font-size: 1.15em;
  font-weight: 700;
  opacity: 0.4;
}
.quill-plus-lg { font-size: 1.6em; }
.quill-sample,
.quill-sample text,
.quill-sample text selection {
  background-color: transparent;
  background-image: none;
}
.quill-sample text selection {
  background-color: alpha(@accent_bg_color, 0.45);
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

        self._build_brand()
        self._build_hero()
        self._build_tutorial()
        self._build_shortcuts_group()
        self._build_provider_group()
        self._build_openai_group()
        self._build_codex_group()
        self._build_claude_group()
        self._build_openrouter_group()
        self._build_model_group()
        self._build_behaviour_group()
        self._build_actions_group()

        self._ready = True
        self._sync_account_row()
        self._sync_provider()
        self._refresh_provider_badges()



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
                      spacing=9 if large else 6)
        # Room for the drop shadow, or the next widget clips it.
        box.set_margin_bottom(7 if large else 4)
        box.set_margin_top(2)
        parts = [p.strip() for p in chord.split("+") if p.strip()]
        for index, part in enumerate(parts):
            if index:
                plus = Gtk.Label(label="+")
                plus.add_css_class("quill-plus")
                if large:
                    plus.add_css_class("quill-plus-lg")
                plus.set_valign(Gtk.Align.CENTER)
                box.append(plus)
            cap = Gtk.Label(label=keybindings.pretty_key(part))
            cap.add_css_class("quill-keycap")
            if large:
                cap.add_css_class("quill-keycap-lg")
            cap.set_valign(Gtk.Align.CENTER)
            box.append(cap)
        return box

    def _build_brand(self) -> None:
        group = Adw.PreferencesGroup()
        self.page.add(group)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.set_halign(Gtk.Align.START)
        row.set_margin_top(14)

        icon = branding.icon_path()
        if icon:
            mark = Gtk.Image.new_from_file(icon)
            mark.set_pixel_size(52)
            row.append(mark)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=branding.NAME, xalign=0)
        name.add_css_class("title-1")
        tagline = Gtk.Label(label=branding.TAGLINE, xalign=0)
        tagline.add_css_class("caption")
        tagline.add_css_class("dim-label")
        text.append(name)
        text.append(tagline)
        row.append(text)

        group.add(row)

    def _build_hero(self) -> None:
        """Keys on the left, what they do on the right.

        A two-column grid rather than a centred stack: the chords line up under
        each other, and each description reads straight across from the keys it
        belongs to instead of sitting underneath them.
        """
        group = Adw.PreferencesGroup()
        self.page.add(group)

        self.hero_grid = Gtk.Grid()
        grid = self.hero_grid
        grid.add_css_class("quill-hero")
        grid.set_column_spacing(18)
        grid.set_row_spacing(10)
        grid.set_halign(Gtk.Align.START)
        group.add(grid)
        self._fill_hero()

    def _fill_hero(self) -> None:
        grid = self.hero_grid
        child = grid.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            grid.remove(child)
            child = nxt

        binds = hypr.binds_matching("quill")
        primary = next((c for c, w in binds if "menu" in w.lower()), "Super + I")
        secondary = next((c for c, w in binds if "menu" not in w.lower()), None)

        rows = [(primary, "Select text in any app, then press this")]
        if secondary:
            rows.append((secondary, "Fix grammar in place, without the popup"))

        # One size for both: they are two equal ways to do the same job, and
        # sizing one of them up implied a hierarchy that is not there.
        for index, (chord, text) in enumerate(rows):
            keys = self._keycaps(chord)
            keys.set_halign(Gtk.Align.START)
            keys.set_valign(Gtk.Align.CENTER)
            grid.attach(keys, 0, index, 1, 1)

            label = Gtk.Label(label=text, xalign=0)
            label.set_valign(Gtk.Align.CENTER)
            label.add_css_class("body")
            grid.attach(label, 1, index, 1, 1)

    SAMPLE = (
        "i has recieved you're mesage yesterday and we was gonna reply.\n"
        "the subttitle translation is not finished yet, so pleese focuse on "
        "the assigment first.\n"
        "i will be back to you about chinese subtitles later."
    )

    def _build_tutorial(self) -> None:
        """A rehearsal of the real thing, not a simulation of it.

        The steps are driven by what the user actually does: selecting the text
        arms step two, and the buffer changing under the paste completes it.
        Nothing here fakes the pipeline -- pressing the real chord runs the real
        popup, which pastes back into this very field.
        """
        group = Adw.PreferencesGroup(
            title="Playground",
            description="Practise on this text; your own documents are untouched.",
        )
        self.page.add(group)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("card")
        box.add_css_class("quill-tutorial")
        box.set_margin_top(6)

        # --- step 1 -------------------------------------------------------
        self.step1 = self._step_header(1, "Select some of the text below")
        box.append(self.step1)

        self.sample_view = Gtk.TextView()
        self.sample_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.sample_view.add_css_class("quill-sample")
        # Multiline on purpose: selecting a phrase out of a paragraph is the
        # real case, not selecting one tidy sentence.
        self.sample_view.set_size_request(-1, 96)
        self.sample_view.set_top_margin(4)
        self.sample_view.set_bottom_margin(4)
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

        hint = Gtk.Label(
            label="A few words is enough — Quill only ever touches what you selected.",
            xalign=0)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        box.append(hint)

        # --- step 2, shown from the start so the whole job is visible ------
        self.step2_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.step2_box.set_margin_top(10)
        box.append(self.step2_box)

        self.step2 = self._step_header(2, "Press either shortcut")
        self.step2_box.append(self.step2)

        # Both modes, side by side, because they are the two things to learn:
        # one opens a menu, the other just fixes it.
        binds = hypr.binds_matching("quill")
        menu_chord = next((c for c, w in binds if "menu" in w.lower()), "Super + I")
        quick_chord = next((c for c, w in binds if "menu" not in w.lower()), None)

        modes = [(menu_chord, "Opens a menu — pick any edit")]
        if quick_chord:
            modes.append((quick_chord, "Fixes grammar straight away, no menu"))

        picker = Gtk.Grid()
        picker.set_column_spacing(12)
        picker.set_row_spacing(8)
        picker.set_margin_start(26)
        for index, (chord, what) in enumerate(modes):
            keys = self._keycaps(chord)
            keys.set_halign(Gtk.Align.END)
            keys.set_valign(Gtk.Align.CENTER)
            picker.attach(keys, 0, index, 1, 1)
            label = Gtk.Label(label=what, xalign=0)
            label.set_valign(Gtk.Align.CENTER)
            label.add_css_class("caption")
            label.add_css_class("dim-label")
            picker.attach(label, 1, index, 1, 1)
        self.step2_box.append(picker)

        self.reset_button = Gtk.Button(label="Reset the sample")
        self.reset_button.add_css_class("flat")
        self.reset_button.set_halign(Gtk.Align.START)
        self.reset_button.set_margin_start(22)
        self.reset_button.connect("clicked", lambda *_: self._reset_tutorial())
        self.step2_box.append(self.reset_button)

        # --- step 3, hidden until the text is actually replaced ------------
        self.step3_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.step3_box.set_margin_top(10)
        self.step3_box.set_visible(False)
        box.append(self.step3_box)

        self.step3 = self._step_header(3, "Quill replaces what you selected")
        self.step3_box.append(self.step3)

        self.diff_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.diff_box.add_css_class("quill-result-frame")
        self.diff_before = Gtk.Label(xalign=0, wrap=True)
        self.diff_before.add_css_class("caption")
        self.diff_before.add_css_class("quill-was")
        self.diff_after = Gtk.Label(xalign=0, wrap=True)
        self.diff_after.add_css_class("body")
        self.diff_box.append(self.diff_before)
        self.diff_box.append(self.diff_after)
        self.step3_box.append(self.diff_box)

        group.add(box)
        self._set_step_state(1, "todo")
        self._set_step_state(2, "todo")

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
        # Keep the whole buffer too, so the replacement can be isolated later.
        self._buffer_before = buffer.get_text(*buffer.get_bounds(), False)
        self._set_step_state(1, "done")

    def _tutorial_changed(self) -> None:
        """A replacement completes step three -- but only a real one.

        Comparing the whole buffer against the selection meant every keystroke
        in the field looked like a replacement, so typing a character produced
        a "was/now" diff of the text against itself. The precise test is that
        the fragment the user selected is no longer present.
        """
        buffer = self.sample_view.get_buffer()
        current = buffer.get_text(*buffer.get_bounds(), False)
        if not current.strip() or not self.tutorial_before:
            return
        selected = self.tutorial_before.strip()
        if not selected or selected in current:
            return
        self._set_step_state(2, "done")
        self.step3_box.set_visible(True)
        was, now = self._changed_span(current)
        self.diff_before.set_label(f"was:  {was}")
        self.diff_after.set_label(f"now:  {now}")

    def _changed_span(self, current: str) -> tuple[str, str]:
        """(old, new) for the text that changed, snapped to word boundaries.

        A character-level diff is minimal but unreadable: replacing
        "recieved you're mesage" with "received your message" shares "rec" and
        "ge", so the raw span reads "eived your mes". Widening to whitespace
        keeps whole words on both sides.
        """
        before = getattr(self, "_buffer_before", "")
        if not before:
            return self.tutorial_before.strip(), current.strip()

        start = 0
        limit = min(len(before), len(current))
        while start < limit and before[start] == current[start]:
            start += 1
        tail = 0
        while (tail < len(before) - start and tail < len(current) - start
               and before[-1 - tail] == current[-1 - tail]):
            tail += 1

        # Widen to whitespace so words are not cut in half.
        while start > 0 and not before[start - 1].isspace():
            start -= 1
        def widen(text: str, end_offset: int) -> int:
            index = len(text) - end_offset
            while index < len(text) and not text[index].isspace():
                index += 1
            return len(text) - index
        old_tail = widen(before, tail)
        new_tail = widen(current, tail)

        was = before[start:len(before) - old_tail].strip()
        now = current[start:len(current) - new_tail].strip()
        return (was or self.tutorial_before.strip(), now or current.strip())

    def _reset_tutorial(self) -> None:
        self.sample_view.get_buffer().set_text(self.SAMPLE)
        self.tutorial_before = self.SAMPLE
        self._set_step_state(1, "todo")
        self._set_step_state(2, "todo")
        self.step3_box.set_visible(False)

    def _build_shortcuts_group(self) -> None:
        """Reference and editor. The chords come from bindings.lua itself."""
        self.shortcuts_group = Adw.PreferencesGroup()
        self.page.add(self.shortcuts_group)

        self.shortcuts_expander = Adw.ExpanderRow(title="Keys inside the popup")
        self.shortcuts_expander.set_use_markup(False)
        self.shortcuts_expander.add_prefix(
            Gtk.Image.new_from_icon_name("input-keyboard-symbolic"))
        self.shortcuts_group.add(self.shortcuts_expander)

        self.shortcut_rows: list[Adw.ActionRow] = []
        self._fill_shortcut_rows()

    def _fill_shortcut_rows(self) -> None:
        """Only the in-app keys. The bindings live with the edits they run."""
        for row in self.shortcut_rows:
            self.shortcuts_expander.remove(row)
        self.shortcut_rows = []
        for chord, what in self.IN_APP_SHORTCUTS:
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(what)
            label = Gtk.Label(label=chord)
            label.set_valign(Gtk.Align.CENTER)
            label.add_css_class("monospace")
            label.add_css_class("dim-label")
            row.add_suffix(label)
            self.shortcuts_expander.add_row(row)
            self.shortcut_rows.append(row)

    def _capture_chord(self, binding, creating: bool = False) -> None:
        """Modal that waits for a real key press, then writes it out."""
        if keybindings.is_pointer(binding.chord):
            self._pick_pointer_chord(binding, creating)
            return
        dialog = Adw.AlertDialog(
            heading=("Shortcut for: " + binding.label) if creating
                    else f"Change: {binding.label}",
            body="Press the keys you want. Escape cancels.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.set_close_response("cancel")

        holder = {"chord": None}
        preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        current = Gtk.Label(
            label=keybindings.pretty(binding.chord) or "Press a combination")
        current.add_css_class("title-4")
        note = Gtk.Label(label="", wrap=True)
        note.add_css_class("caption")
        preview.append(current)
        preview.append(note)
        dialog.set_extra_child(preview)

        def accept(chord: str) -> None:
            shown = keybindings.pretty(chord)
            clash = keybindings.is_bound_elsewhere(chord, binding)
            current.set_label(shown)
            if clash:
                holder["chord"] = None
                note.set_label(f"{shown} is already used by “{clash}”. Try another.")
                note.remove_css_class("success")
                note.add_css_class("error")
            else:
                holder["chord"] = chord
                note.set_label("Press Enter to keep it, or try another.")
                note.remove_css_class("error")
                note.add_css_class("success")

        def on_key(_c, keyval, _code, state):
            from gi.repository import Gdk
            named = Gdk.keyval_name(keyval) or ""
            if named == "Escape":
                dialog.close()
                return True
            # Bare Enter confirms; Enter with a modifier held is a chord.
            if named in ("Return", "KP_Enter") and not keybindings.has_modifiers(state):
                if holder["chord"]:
                    dialog.close()
                    self._apply_chord(binding, holder["chord"], creating)
                return True
            chord = keybindings.chord_from_event(keyval, state)
            if chord is None:
                return True            # still holding modifiers
            accept(chord)
            return True

        controller = Gtk.EventControllerKey()
        # CAPTURE, so the dialog sees the chord before any widget acts on it.
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", on_key)
        dialog.add_controller(controller)
        dialog.present(self)

    #: The mouse buttons worth binding, in the order they are offered.
    POINTER_BUTTONS = ("mouse:272", "mouse:273", "mouse:274",
                       "mouse:275", "mouse:276")

    def _pick_pointer_chord(self, binding, creating: bool = False) -> None:
        """Choose modifiers and a button, rather than capturing a click.

        A pointer chord cannot be captured the way a key can: Hyprland grabs
        SUPER+click for move and resize, and Quill's own trigger is a click
        too, so the press is consumed before any dialog sees it. Picking is
        the only method that works for every combination.
        """
        dialog = Adw.AlertDialog(
            heading=("Shortcut for: " + binding.label) if creating
                    else f"Change: {binding.label}",
            body="Pick the modifiers and the button.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.set_default_response("save")

        parts = [p.strip() for p in binding.chord.split("+") if p.strip()]
        held = {p.upper() for p in parts}
        button = next((p.lower() for p in parts if p.lower().startswith("mouse")),
                      "mouse:273")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        shown = Gtk.Label()
        shown.add_css_class("title-4")
        note = Gtk.Label(label="", wrap=True)
        note.add_css_class("caption")

        mods_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        mods_box.add_css_class("linked")
        mods_box.set_halign(Gtk.Align.CENTER)
        toggles = {}
        for mod in keybindings.MOD_ORDER:
            toggle = Gtk.ToggleButton(label=mod.title())
            toggle.set_active(mod in held)
            toggles[mod] = toggle
            mods_box.append(toggle)

        labels = [keybindings.pretty_key(b) for b in self.POINTER_BUTTONS]
        drop = Gtk.DropDown.new_from_strings(labels)
        drop.set_selected(self.POINTER_BUTTONS.index(button)
                          if button in self.POINTER_BUTTONS else 1)
        drop.set_halign(Gtk.Align.CENTER)

        body.append(shown)
        body.append(mods_box)
        body.append(drop)
        body.append(note)
        dialog.set_extra_child(body)

        def chord() -> str:
            mods = [m for m in keybindings.MOD_ORDER if toggles[m].get_active()]
            return " + ".join(mods + [self.POINTER_BUTTONS[drop.get_selected()]])

        def refresh(*_args) -> None:
            value = chord()
            shown.set_label(keybindings.pretty(value))
            clash = keybindings.is_bound_elsewhere(value, binding)
            note.set_label(f"Already used by \u201c{clash}\u201d." if clash else "")
            note.remove_css_class("error")
            if clash:
                note.add_css_class("error")
            dialog.set_response_enabled("save", not clash)

        for toggle in toggles.values():
            toggle.connect("toggled", refresh)
        drop.connect("notify::selected", refresh)
        refresh()

        def on_response(_d, response: str) -> None:
            if response == "save":
                self._apply_chord(binding, chord(), creating)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _apply_chord(self, binding, chord: str, creating: bool = False) -> None:
        try:
            if creating:
                keybindings.add(chord, binding.description, binding.command)
            else:
                keybindings.set_chord(binding, chord)
        except OSError as exc:
            self._toast(str(exc))
            return
        if keybindings.reload():
            self._toast(f"{binding.label} is now {keybindings.pretty(chord)}")
        else:
            self._toast("Saved — run 'hyprctl reload' to apply")
        self._rebuild_action_rows()
        self._refresh_hero()

    def _refresh_hero(self) -> None:
        """Repopulate the hero grid so its keycaps match what was just saved."""
        if getattr(self, "hero_grid", None) is not None:
            self._fill_hero()

    # -- provider ----------------------------------------------------------
    def _build_provider_group(self) -> None:
        group = Adw.PreferencesGroup(title="Where edits run")
        self.page.add(group)

        self.provider_order = [OLLAMA, OPENAI, CODEX, CLAUDECODE, OPENROUTER]
        self._provider_id = (self.cfg.provider if self.cfg.provider in self.provider_order
                             else OLLAMA)
        options = [
            (OLLAMA, "On this machine",
             "Ollama — nothing leaves this computer", ""),
            (OPENAI, "OpenAI-compatible server",
             "LM Studio, llama.cpp, vLLM, or api.openai.com", ""),
            (CODEX, "ChatGPT subscription",
             "Codex CLI — uses your plan, not API tokens", ""),
            (CLAUDECODE, "Claude subscription",
             "Claude Code — uses your plan, not API tokens", ""),
            (OPENROUTER, "OpenRouter", "Cloud, with a free tier", ""),
        ]
        self._radio_picker(group, options, self._provider_id,
                           self._on_provider_picked)

        # These report state; they are not settings. Adding a non-row widget to
        # a PreferencesGroup places it below the card, which is exactly the
        # "secondary" weight they should carry.
        secondary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        secondary.add_css_class("quill-secondary")

        status_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_line.set_hexpand(True)
        self.status_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        # CENTER, not START: the icon should sit on the text's optical centre.
        self.status_icon.set_valign(Gtk.Align.CENTER)
        self.status_label = Gtk.Label(xalign=0, wrap=True)
        self.status_label.set_valign(Gtk.Align.CENTER)
        self.status_label.add_css_class("caption")
        self.status_label.set_hexpand(True)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_valign(Gtk.Align.CENTER)
        refresh.add_css_class("flat")
        refresh.set_tooltip_text("Re-check the backend")
        refresh.connect("clicked", lambda *_: self._refresh_all())
        status_line.append(self.status_icon)
        status_line.append(self.status_label)
        secondary.append(status_line)

        privacy_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        privacy_line.set_hexpand(True)
        self.privacy_icon = Gtk.Image.new_from_icon_name("security-high-symbolic")
        self.privacy_icon.set_valign(Gtk.Align.CENTER)
        self.privacy_label = Gtk.Label(xalign=0, wrap=True)
        self.privacy_label.set_valign(Gtk.Align.CENTER)
        self.privacy_label.add_css_class("caption")
        self.privacy_label.set_hexpand(True)
        privacy_line.append(self.privacy_icon)
        privacy_line.append(self.privacy_label)
        secondary.append(privacy_line)

        secondary.append(refresh)
        group.add(secondary)


    def _provider_state(self, value: str) -> tuple[str, str]:
        """(badge, css class) for a provider row.

        Deliberately avoids network calls: opening settings should not wait on
        OpenRouter to answer. Presence of a key or a signed-in CLI is enough to
        say "available"; whether it actually works is the Status line's job.
        """
        selected = self._provider_value()

        if value == CODEX:
            if not codex.available():
                return "NOT INSTALLED", "dim-label"
            mode = codex.auth_mode()
            if mode is None:
                return "NOT SIGNED IN", "warning"
            if mode != "chatgpt":
                return "API KEY — BILLED", "warning"
            ready = True
        elif value == CLAUDECODE:
            if not claudecode.available():
                return "NOT INSTALLED", "dim-label"
            if not claudecode.signed_in():
                return "NOT SIGNED IN", "warning"
            ready = True
        elif value == OLLAMA:
            ready = ollama.is_up(self.cfg)
            if not ready:
                return "NOT RUNNING", "warning"
        elif value == OPENROUTER:
            if not openrouter.has_key():
                return "NOT CONNECTED", "dim-label"
            ready = True
        else:
            base = self._openai_base_url() if hasattr(self, "base_url_row") \
                else self.cfg.openai_base_url
            if openai_api.needs_key(base):
                # Remote: do not stall the window on a round trip. A stored key
                # is the most that can be claimed without asking the server.
                if not openai_api.key():
                    return "NEEDS A KEY", "dim-label"
                ready = True
            else:
                # Loopback: a closed port refuses immediately, so this is cheap
                # and stops the badge claiming AVAILABLE with nothing listening.
                probe = replace(self.cfg, openai_base_url=base)
                if not openai_api.is_up(probe):
                    return "NOT RUNNING", "warning"
                ready = True

        if not ready:
            return "", "dim-label"
        # "Configured" is the one in use; "Available" is detected but idle.
        return ("CONFIGURED", "success") if value == selected else ("AVAILABLE", "accent")

    def _refresh_provider_badges(self) -> None:
        for value, badge in getattr(self, "_badges", {}).items():
            if value not in self.provider_order:
                continue
            text, tone = self._provider_state(value)
            badge.set_label(text)
            badge.set_visible(bool(text))
            for css in ("success", "accent", "warning", "dim-label"):
                badge.remove_css_class(css)
            badge.add_css_class(tone)

    def _on_provider_picked(self, value: str) -> None:
        self._provider_id = value
        self._sync_provider()
        self._refresh_provider_badges()

    def _provider_value(self) -> str:
        return getattr(self, "_provider_id", OLLAMA)

    def _sync_provider(self) -> None:
        """Show only the backend in use.

        Everything still exists, so _collect() can read it; it is just not on
        screen competing with the settings the user opened this window for.
        """
        active = self._provider_value()
        self.openrouter_group.set_visible(active == OPENROUTER)
        self.openai_group.set_visible(active == OPENAI)
        self.codex_group.set_visible(active == CODEX)
        self.claude_group.set_visible(active == CLAUDECODE)
        self.model_group.set_visible(active == OLLAMA)

        probe = replace(self.cfg, provider=active,
                        openai_base_url=self._openai_base_url())
        # Short enough to sit beside the status line, and honest about the one
        # thing that actually matters: whether the text leaves the machine.
        if probe.is_local:
            note, tone = "Nothing leaves this machine", "success"
        elif active == CODEX:
            note, tone = "Sent to OpenAI on your ChatGPT plan", "warning"
        elif active == CLAUDECODE:
            note, tone = "Sent to Anthropic on your Claude plan", "warning"
        elif active == OPENROUTER:
            note, tone = "Sent to OpenRouter — free models may train on it", "warning"
        else:
            note, tone = f"Sent to {self._openai_base_url()}", "warning"
        self.privacy_label.set_label(note)
        for widget in (self.privacy_icon, self.privacy_label):
            for css in ("success", "warning", "dim-label"):
                widget.remove_css_class(css)
            widget.add_css_class(tone)
        self.privacy_icon.set_from_icon_name(
            "security-high-symbolic" if tone == "success"
            else "dialog-warning-symbolic")
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
        self._refresh_provider_badges()
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

    def _radio_picker(self, group, options, current, on_pick):
        """Always-visible radio rows. Same control as the model list."""
        first: Gtk.CheckButton | None = None
        radios: dict[str, Gtk.CheckButton] = {}
        self._badges: dict[str, Gtk.Label] = getattr(self, "_badges", {})
        for option in options:
            value, title, subtitle = option[0], option[1], option[2]
            badge_text = option[3] if len(option) > 3 else None
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(title)
            if subtitle:
                row.set_subtitle(subtitle)
            if badge_text is not None:
                badge = Gtk.Label(label="")
                badge.set_valign(Gtk.Align.CENTER)
                badge.add_css_class("quill-badge")
                row.add_suffix(badge)
                self._badges[value] = badge
            radio = Gtk.CheckButton()
            radio.set_valign(Gtk.Align.CENTER)
            if first is None:
                first = radio
            else:
                radio.set_group(first)
            radio.set_active(value == current)
            radio.connect("toggled",
                          lambda r, v=value: on_pick(v) if r.get_active() else None)
            row.add_prefix(radio)
            row.set_activatable_widget(radio)
            group.add(row)
            radios[value] = radio
        return radios

    # -- claude code -------------------------------------------------------
    def _build_claude_group(self) -> None:
        self.claude_group = Adw.PreferencesGroup(
            title="Claude subscription",
            description=("Runs edits through Anthropic's Claude Code CLI, signed "
                         "in with your Claude account. That uses your plan "
                         "instead of metered API tokens."),
        )
        self.page.add(self.claude_group)

        self.claude_status_row = Adw.ActionRow()
        self.claude_status_row.set_use_markup(False)
        self.claude_status_row.set_title("Claude Code")
        signin = Gtk.Button(label="Sign in")
        signin.set_valign(Gtk.Align.CENTER)
        signin.connect("clicked", lambda *_: self._claude_login())
        self.claude_status_row.add_suffix(signin)
        self.claude_group.add(self.claude_status_row)

        self._claude_model = self.cfg.claude_model or claudecode.DEFAULT_MODEL

        def pick(value):
            self._claude_model = value
            self._refresh_status()

        self._radio_picker(
            self.claude_group,
            [(m, claudecode.MODEL_LABELS.get(m, m),
              "Recommended — quickest to come back" if m == claudecode.DEFAULT_MODEL else "")
             for m in claudecode.MODELS],
            self._claude_model, pick)
        self._sync_claude_row()

    def _sync_claude_row(self) -> None:
        self.claude_status_row.set_subtitle(claudecode.describe())

    def _claude_login(self) -> None:
        exe = claudecode.binary()
        if not exe:
            self._toast("Claude Code is not installed")
            return
        Gio.Subprocess.new(
            ["omarchy-launch-floating-terminal-with-presentation", exe],
            Gio.SubprocessFlags.NONE)
        self._toast("Sign in there, then press refresh")

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

        self._codex_model = self.cfg.codex_model or codex.DEFAULT_MODEL

        def pick_model(value):
            self._codex_model = value
            self._refresh_status()

        self._radio_picker(
            self.codex_group,
            [(m, codex.MODEL_LABELS.get(m, m),
              "Recommended — measured 3.7s against 4.4s for Sol"
              if m == codex.DEFAULT_MODEL else "")
             for m in codex.MODELS],
            self._codex_model, pick_model)

        self._codex_effort = self.cfg.codex_effort or "low"

        def pick_effort(value):
            self._codex_effort = value
            self._refresh_status()

        self._radio_picker(
            self.codex_group,
            [(e, codex.EFFORT_LABELS.get(e, e),
              "Recommended for editing" if e == "low" else "")
             for e in codex.EFFORTS],
            self._codex_effort, pick_effort)

        self.codex_model_row = Adw.EntryRow(title="Model override (advanced)")
        self.codex_model_row.set_text("")

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

        # Radio rows, not a dropdown. There are only ever a handful of models,
        # the label carries the reason to pick one, and a collapsed ComboRow
        # truncated that to "Best quality - Gemma ...".
        self.model_group_card = group
        self.model_radio_group: Gtk.CheckButton | None = None
        self.model_radios: dict[str, Gtk.CheckButton] = {}
        self.model_row_widgets: list[Adw.ActionRow] = []
        self.other_expander: Adw.ExpanderRow | None = None
        self._downloads: dict[str, threading.Event] = {}

        # Kept for the case where every recommended model is missing AND
        # Ollama is unreachable, when a copyable command is all we can offer.
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
        # Keep a choice already made in this window; only fall back to the
        # saved config when there is nothing to keep.
        wanted = getattr(self, "_selected_id", None) or self.cfg.model
        self._selected_id = (wanted if wanted in self.model_ids
                             else (self.cfg.model if self.cfg.model in self.model_ids
                                   else (self.model_ids[0] if self.model_ids
                                         else self.cfg.model)))

        for row in self.model_row_widgets:
            self.model_group_card.remove(row)
        if self.other_expander is not None:
            self.model_group_card.remove(self.other_expander)
            self.other_expander = None
        self.model_row_widgets = []
        self.model_radios = {}
        self.model_radio_group = None

        def unsuited(model: str) -> bool:
            note = models.note_for(model)
            return note is not None and note.tier == models.UNSUITED

        # Quill ships no models, so the recommended ones are listed even when
        # absent -- otherwise a fresh install shows an empty box and no way out.
        recommended = [m for m in self.model_ids if not unsuited(m)]
        for model in models.RECOMMENDED:
            if model not in recommended:
                recommended.append(model)
        others = [m for m in self.model_ids if unsuited(m)]
        self._installed = set(installed)

        for model in recommended:
            row = self._model_row(model)
            self.model_group_card.add(row)
            self.model_row_widgets.append(row)

        # The ones Quill measured and rejected are still selectable, but they
        # do not belong in the same list as the answers.
        if others:
            self.other_expander = Adw.ExpanderRow(
                title="Other models you have installed",
                subtitle="Quill tested these and does not recommend them")
            self.other_expander.set_use_markup(False)
            self.other_expander.add_prefix(
                Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
            for model in others:
                self.other_expander.add_row(self._model_row(model))
                if model == self._selected_id:
                    self.other_expander.set_expanded(True)
            self.model_group_card.add(self.other_expander)

        self._on_model_changed()
        self._sync_pull_row(installed)

    def _model_row(self, model: str) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_use_markup(False)
        row.set_title(models.label_for(model))

        if model not in getattr(self, "_installed", set()):
            return self._download_row(row, model)

        row.set_subtitle(models.describe(model))
        radio = Gtk.CheckButton()
        radio.set_valign(Gtk.Align.CENTER)
        if self.model_radio_group is None:
            self.model_radio_group = radio
        else:
            radio.set_group(self.model_radio_group)
        radio.set_active(model == self._selected_id)
        radio.connect("toggled", self._on_radio_toggled, model)

        row.add_prefix(radio)
        # Clicking anywhere on the row picks it, not just the small circle.
        row.set_activatable_widget(radio)
        self.model_radios[model] = radio
        return row

    def _download_row(self, row: Adw.ActionRow, model: str) -> Adw.ActionRow:
        """A model that is not on disk yet: offer to fetch it."""
        size = models.download_size(model)
        row.set_subtitle(f"Not downloaded · {size} to fetch" if size
                         else "Not downloaded")

        progress = Gtk.ProgressBar()
        progress.set_valign(Gtk.Align.CENTER)
        progress.set_size_request(120, -1)
        progress.set_visible(False)
        row.add_suffix(progress)

        button = Gtk.Button()
        button.set_child(Adw.ButtonContent(icon_name="folder-download-symbolic",
                                          label="Download"))
        button.add_css_class("suggested-action")
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self._download_model(
            model, row, progress, button))
        row.add_suffix(button)
        return row

    def _download_model(self, model: str, row, progress, button) -> None:
        if not ollama.is_up(self.cfg):
            self._toast("Ollama is not running — start it first")
            return
        button.set_sensitive(False)
        progress.set_visible(True)
        progress.set_fraction(0.0)
        row.set_subtitle("Starting…")
        cancel = threading.Event()
        self._downloads[model] = cancel

        def worker():
            try:
                ollama.pull(self.cfg, model,
                            on_progress=lambda status, fraction: GLib.idle_add(
                                self._download_progress, row, progress,
                                status, fraction),
                            cancel=cancel)
                GLib.idle_add(self._download_done, model, None)
            except ollama.OllamaError as exc:
                GLib.idle_add(self._download_done, model, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _download_progress(self, row, progress, status: str,
                           fraction: float | None) -> bool:
        if fraction is None:
            # Ollama is doing something it cannot size, e.g. verifying a digest.
            progress.pulse()
            row.set_subtitle(status or "Working…")
        else:
            progress.set_fraction(fraction)
            row.set_subtitle(f"{status} — {fraction * 100:.0f}%")
        return False

    def _download_done(self, model: str, error: str | None) -> bool:
        self._downloads.pop(model, None)
        if error:
            self._toast(error)
        else:
            self._toast(f"{models.friendly_name(model)} is ready")
            # Select what was just fetched: it is what the user wanted.
            self._selected_id = model
        self._reload_model_list()
        return False

    def _on_radio_toggled(self, radio: Gtk.CheckButton, model: str) -> None:
        if radio.get_active():
            self._selected_id = model
            self._on_model_changed()

    def _on_model_changed(self) -> None:
        """Each row carries its own stats; this is the longer explanation."""
        selected = self._selected_model()
        self.detail_label.set_label(models.detail(selected) or models.COMPARISON)
        self._refresh_status()

    def _sync_pull_row(self, installed: list[str]) -> None:
        missing = models.missing_recommendation(installed)
        if missing and ollama.is_up(self.cfg):
            # The row above can fetch it; no need for a command to copy.
            self.pull_row.set_visible(False)
            return
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
        self._sync_claude_row()
        self._sync_openai_key_row()
        self._refresh_provider_badges()
        self._refresh_status(reload_models=True)

    def _refresh_status(self, reload_models: bool = False) -> None:
        if not self._ready:
            return
        if reload_models:
            self._reload_model_list()
        probe = self._collect()
        usable, reason = provider.ready(probe)
        self.status_label.set_label(reason)
        tone = "success" if usable else "error"
        for widget in (self.status_icon, self.status_label):
            for css in ("success", "error", "dim-label"):
                widget.remove_css_class(css)
            widget.add_css_class(tone)
        self.status_icon.set_from_icon_name(
            "emblem-ok-symbolic" if usable else "dialog-warning-symbolic")

    def _selected_model(self) -> str:
        return getattr(self, "_selected_id", self.cfg.model)

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
            title="Edits and shortcuts",
            description=("What appears in the popup, in order. Give any edit its "
                         "own shortcut to run it without the popup."),
        )
        self.page.add(self.actions_group)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        reset = Gtk.Button()
        reset.set_child(Adw.ButtonContent(icon_name="view-refresh-symbolic",
                                          label="Reset"))
        reset.add_css_class("flat")
        reset.set_tooltip_text("Restore the built-in menu")
        reset.connect("clicked", lambda *_: self._reset_actions())
        add = Gtk.Button()
        add.set_child(Adw.ButtonContent(icon_name="list-add-symbolic",
                                        label="Add"))
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self._add_action())
        box.append(reset)
        box.append(add)
        self.actions_group.set_header_suffix(box)

        self.action_rows: list[Adw.ExpanderRow] = []
        self._rebuild_action_rows()

    def _rebuild_action_rows(self) -> None:
        for row in self.action_rows:
            self.actions_group.remove(row)
        self.action_rows = []

        # Read once; every row below asks this map what chord it owns.
        self._binds = keybindings.read()
        self._binds_by_action = {}
        for binding in self._binds:
            key = keybindings.action_id(binding)
            if key:
                self._binds_by_action.setdefault(key, binding)

        for index, action in enumerate(self.draft_actions):
            self.actions_group.add(self._action_row(index, action))

    def _attach_chord_controls(self, row, binding, action_id_for_new) -> None:
        """Show the chord this row owns, with buttons to change or clear it."""
        if binding is not None:
            keys = self._keycaps(binding.chord)
            keys.set_valign(Gtk.Align.CENTER)
            row.add_suffix(keys)

            change = Gtk.Button()
            change.set_child(Adw.ButtonContent(icon_name="document-edit-symbolic",
                                               label="Change"))
            change.add_css_class("flat")
            change.set_valign(Gtk.Align.CENTER)
            change.connect("clicked", lambda _b, bind=binding: self._capture_chord(bind))
            row.add_suffix(change)

            if action_id_for_new is not None:
                clear = Gtk.Button(icon_name="edit-clear-symbolic")
                clear.add_css_class("flat")
                clear.set_valign(Gtk.Align.CENTER)
                clear.set_tooltip_text("Remove this shortcut")
                clear.connect("clicked",
                              lambda _b, bind=binding: self._clear_chord(bind))
                row.add_suffix(clear)
            return

        assign = Gtk.Button()
        assign.set_child(Adw.ButtonContent(icon_name="list-add-symbolic",
                                           label="Add shortcut"))
        assign.add_css_class("flat")
        assign.set_valign(Gtk.Align.CENTER)
        assign.connect("clicked",
                       lambda _b, aid=action_id_for_new: self._capture_new_chord(aid))
        row.add_suffix(assign)

    def _capture_new_chord(self, action_id: str) -> None:
        action = next((a for a in self.draft_actions if a.id == action_id), None)
        if action is None:
            return
        placeholder = keybindings.Binding(
            chord="", description=f"Quill: {action.label}",
            command=keybindings.quill_command(f"run {action_id}"), line=-1)
        self._capture_chord(placeholder, creating=True)

    def _clear_chord(self, binding) -> None:
        try:
            keybindings.remove(binding)
        except OSError as exc:
            self._toast(str(exc))
            return
        keybindings.reload()
        self._toast("Shortcut removed")
        self._rebuild_action_rows()
        self._refresh_hero()

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

        shortcut_row = Adw.ActionRow()
        shortcut_row.set_use_markup(False)
        shortcut_row.set_title("Shortcut")
        shortcut_row.set_subtitle("Runs this edit directly, without the popup")
        self._attach_chord_controls(
            shortcut_row, self._binds_by_action.get(action.id), action.id)
        row.add_row(shortcut_row)

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
        cfg.codex_model = (self.codex_model_row.get_text().strip()
                           or getattr(self, "_codex_model", cfg.codex_model))
        cfg.codex_effort = getattr(self, "_codex_effort", cfg.codex_effort)
        cfg.claude_model = getattr(self, "_claude_model", cfg.claude_model)
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
