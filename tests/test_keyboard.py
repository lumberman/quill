"""The settings window has to be usable with no mouse at all.

    python3 tests/test_keyboard.py

It was not. One Tab landed in the Playground sample and every Tab after it
inserted a tab character, because GtkTextView accepts Tab by default: focus
went in and could never come out. Twenty-six controls sat behind that one
trap.

This asserts the properties that make a control reachable rather than
simulating Tab. Simulating it was tried and dropped: gtk_widget_child_focus
in a loop stalls on the first row of a scrolled page -- it stalls on commits
where pressing Tab for real walks the whole window -- so the walk reported
failures the window did not have. Tab, Shift+Tab, arrow keys, Return, Space,
Ctrl+S and Escape were checked by driving the compositor by hand.

Needs a display; skips without one.
"""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quill import config, settings, style  # noqa: E402

FAILURES = []

#: Controls that must be reachable. Not the whole order -- that changes with
#: which provider is selected -- but the ones whose absence means a section
#: cannot be operated at all.
REQUIRED = [
    "Button:Save",
    "ActionRow:On this machine",
    "ActionRow:OpenRouter",
    "SwitchRow:Replace without reviewing",
    "SwitchRow:Restore the clipboard",
    "Button:Add",
    "ActionRow:Fix Spelling & Grammar",
]


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def describe(widget) -> str:
    if widget is None:
        return "None"
    name = type(widget).__name__
    text = ""
    if isinstance(widget, Adw.PreferencesRow):
        text = widget.get_title()
    elif isinstance(widget, Gtk.ToggleButton):
        text = widget.get_label() or ""
    elif isinstance(widget, Gtk.Button):
        child = widget.get_child()
        if isinstance(child, Adw.ButtonContent):
            text = child.get_label()
        else:
            text = widget.get_label() or widget.get_icon_name() or ""
    return f"{name}:{text}"


def focusables(window) -> list[str]:
    """Every control that can take focus, in tree order.

    Reachability, without pretending to be the focus engine: a widget that is
    on screen and focusable is one Tab can land on.
    """
    found = []

    def visit(widget):
        child = widget.get_first_child()
        while child is not None:
            if child.get_mapped() and child.get_focusable():
                found.append(describe(child))
            visit(child)
            child = child.get_next_sibling()

    visit(window)
    return found


def descendants(widget, kind):
    found = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, kind):
            found.append(child)
        found.extend(descendants(child, kind))
        child = child.get_next_sibling()
    return found


def shortcuts_of(window) -> set[str]:
    names = set()
    for controller in window.observe_controllers():
        if not isinstance(controller, Gtk.ShortcutController):
            continue
        for index in range(controller.get_n_items()):
            trigger = controller.get_item(index).get_trigger()
            if trigger is not None:
                names.add(trigger.to_string())
    return names


def run(window) -> None:
    reachable = focusables(window)
    check("plenty of controls can take focus", len(reachable) >= 20,
          f"only {len(reachable)}: {reachable}")

    for wanted in REQUIRED:
        check(f"{wanted} can take focus", wanted in reachable,
              f"have {reachable}")

    # The trap that started this.
    views = descendants(window, Gtk.TextView)
    trapped = [v for v in views if v.get_accepts_tab()]
    check(f"no text view swallows Tab ({len(views)} checked)", not trapped)

    # Rows that only carry other widgets should not take a stop of their own.
    empty = [r for r in descendants(window, Adw.PreferencesRow)
             if r.get_mapped() and not r.get_title() and r.get_focusable()]
    check("container-only rows take no tab stop", not empty,
          f"{len(empty)} focusable rows with no title")

    # Choice rows are picked with Return, so they must say they are activatable.
    providers = [r for r in descendants(window, Adw.ActionRow)
                 if r.get_title() in ("On this machine", "OpenRouter")]
    check("choice rows are activatable and focusable",
          bool(providers) and all(r.get_activatable() and r.get_focusable()
                                  for r in providers))

    keys = shortcuts_of(window)
    for accelerator in ("Escape", "<Control>s", "<Control>w"):
        check(f"{accelerator} is bound", accelerator in keys, f"have {sorted(keys)}")


def main() -> int:
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        print("SKIP: no display")
        return 0

    app = Adw.Application(application_id="com.omarchy.Quill.KeyboardTest")

    def activate(application):
        style.apply()
        window = settings.SettingsWindow(application, config.load())
        window.set_default_size(760, 900)
        window.present()

        def go():
            try:
                run(window)
            finally:
                application.quit()
            return False

        # Wait for a frame to be drawn, not for a stopwatch. Before the page
        # is allocated, child_focus cannot move past the first row and the
        # walk reports a stall that is the test's fault, not the window's --
        # which is exactly what a timeout produced whenever the machine was
        # busy enough to delay the first frame.
        def on_map(*_args):
            handle = []

            def after_frame(widget, _clock):
                widget.remove_tick_callback(handle[0])
                GLib.timeout_add(150, go)
                return GLib.SOURCE_REMOVE

            handle.append(window.add_tick_callback(after_frame))

        if window.get_mapped():
            on_map()
        else:
            window.connect("map", on_map)

    app.connect("activate", activate)
    app.run([])

    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall good")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
