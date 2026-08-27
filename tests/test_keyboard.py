"""The settings window has to be usable with no mouse at all.

    python3 tests/test_keyboard.py

It was not. One Tab landed in the Playground sample and every Tab after it
inserted a tab character, because GtkTextView accepts Tab by default: focus
went in and could never come out. Twenty-seven controls sat behind that one
trap, unreachable.

The walk below is the same call GtkWindow's own Tab handler makes, so what it
reports is what pressing Tab does. Driving the real thing through the
compositor gave an identical order.

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


def walk(window, limit: int = 200) -> tuple[list[str], bool]:
    """Tab stops in order, and whether focus ever stopped advancing."""
    stops, stalled = [], False
    window.set_focus(None)
    for _ in range(limit):
        if not window.child_focus(Gtk.DirectionType.TAB_FORWARD):
            break
        stops.append(describe(window.get_focus()))
        if len(stops) > 2 and stops[-1] == stops[-2] == stops[-3]:
            stalled = True
            break
    return stops, stalled


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
    stops, stalled = walk(window)
    check("Tab never stops advancing", not stalled,
          f"stuck on {stops[-1]!r}" if stalled else "")

    # The order wraps, so one lap is everything up to the first stop coming
    # round again. Deduping on any repeat would cut the lap short: two groups
    # both have a row called "Advanced".
    unique = stops[:1]
    for stop in stops[1:]:
        if stop == stops[0]:
            break
        unique.append(stop)
    check("Tab reaches more than a handful of controls", len(unique) >= 20,
          f"only {len(unique)}: {unique}")
    check("Tab wraps back to the start", len(stops) > len(unique))

    for wanted in REQUIRED:
        check(f"Tab reaches {wanted}", wanted in unique)

    # The trap that started this.
    views = descendants(window, Gtk.TextView)
    trapped = [v for v in views if v.get_accepts_tab()]
    check(f"no text view swallows Tab ({len(views)} checked)", not trapped)

    # Rows that only carry other widgets should not take a stop of their own.
    # Only the ones on screen: an unmapped row cannot be tabbed to anyway.
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
        window.present()

        def later():
            try:
                run(window)
            finally:
                application.quit()
            return False

        GLib.timeout_add(600, later)

    app.connect("activate", activate)
    app.run([])

    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall good")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
