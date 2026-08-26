"""A focusable text field for verifying the capture -> replace round trip.

    python3 tests/paste_harness.py "starting text"

Mirrors its buffer to /tmp/quill_harness.txt so a test script can assert on
what actually landed in the widget, and writes its Hyprland address to
/tmp/quill_harness.addr.

Focus it explicitly before injecting any keys:

    hyprctl dispatch "hl.dsp.focus({ window = \"address:$ADDR\" })"

and assert `hyprctl activewindow` really is the harness before continuing. A new
window does not always win focus, and a Ctrl+A plus paste aimed at the wrong
window edits whatever the user happened to have open.
"""

import json
import pathlib
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

OUT = "/tmp/quill_harness.txt"
START = sys.argv[1] if len(sys.argv) > 1 else "hello"


def on_activate(app):
    win = Gtk.ApplicationWindow(application=app, title="Quill Paste Harness")
    win.set_default_size(700, 200)
    view = Gtk.TextView()
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    buf = view.get_buffer()
    buf.set_text(START)
    win.set_child(view)
    win.present()
    view.grab_focus()

    def publish_address():
        """Let the test focus this window by address rather than hoping."""
        try:
            clients = json.loads(subprocess.run(
                ["hyprctl", "clients", "-j"], capture_output=True, text=True,
                timeout=5, check=False).stdout)
        except Exception:
            return True
        for client in clients:
            if client.get("class") == "com.omarchy.QuillHarness":
                pathlib.Path("/tmp/quill_harness.addr").write_text(
                    client["address"])
                return False
        return True

    GLib.timeout_add(250, publish_address)

    def mirror():
        start, end = buf.get_bounds()
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write(buf.get_text(start, end, False))
        return True

    GLib.timeout_add(300, mirror)


app = Gtk.Application(application_id="com.omarchy.QuillHarness")
app.connect("activate", on_activate)
app.run([])
