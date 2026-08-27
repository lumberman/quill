"""Regression: pointer shortcuts survive being edited.

    python3 tests/test_chords.py

"Change" used to send every shortcut through the key grabber, which can only
produce a keyboard chord. Pressing it on the SUPER+SHIFT+right-click trigger
therefore destroyed that trigger, and nothing in the window could bring a
mouse binding back. Two smaller bugs came out with it: keycaps showed the raw
"mouse:273", and the clash check compared bindings by description, so the two
popup triggers -- which share one description -- could silently take each
other's chord.
"""

import shutil
import tempfile
from pathlib import Path

from quill import keybindings as kb

BLOCK = """\
-- >>> quill >>>
o.bind("SUPER + I", "Quill: Open the edit menu", "/home/x/.local/bin/quill menu")
o.bind("SUPER + SHIFT + mouse:273", "Quill: Open the edit menu", "/home/x/.local/bin/quill menu")
o.bind("SUPER + SHIFT + I", "Quill: Fix grammar in place", "/home/x/.local/bin/quill run fix")
-- <<< quill <<<
"""

FAILURES = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        FAILURES.append(label)


def main() -> int:
    check("mouse:273 reads as Right-click",
          kb.pretty("SUPER + SHIFT + mouse:273"), "SUPER + SHIFT + Right-click")
    check("wheel names survive", kb.pretty_key("mouse_down"), "Wheel down")
    check("keys are left alone", kb.pretty("SUPER + I"), "SUPER + I")
    check("no stray spacing", kb.pretty("SUPER+I"), "SUPER + I")
    check("pointer chords are recognised", kb.is_pointer("SUPER + mouse:272"), True)
    check("key chords are not", kb.is_pointer("SUPER + M"), False)

    # Identity is the chord, not the description: both popup triggers are
    # described as "Quill: Open the edit menu".
    ptr = kb.Binding(chord="SUPER + SHIFT + mouse:273",
                     description="Quill: Open the edit menu",
                     command="quill menu", line=2)
    check("an unchanged chord does not clash with itself",
          kb.is_bound_elsewhere(ptr.chord, ptr), None)

    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "bindings.lua"
        path.write_text(BLOCK, encoding="utf-8")

        kb.set_chord(ptr, "CTRL + ALT + mouse:274", path=path)
        after = [b.chord for b in kb.read(path) if kb.is_pointer(b.chord)]
        check("a pointer shortcut stays a pointer shortcut",
              after, ["CTRL + ALT + mouse:274"])
        check("nothing else moved", len(kb.read(path)), 3)

        # Clearing the pointer trigger must be reversible from the window.
        ptr = next(b for b in kb.read(path) if kb.is_pointer(b.chord))
        kb.remove(ptr, path=path)
        check("clearing leaves one trigger",
              [b.chord for b in kb.read(path) if kb.is_menu_trigger(b)],
              ["SUPER + I"])
        kb.add("SUPER + SHIFT + mouse:273", "Quill: Open the edit menu",
               "/home/x/.local/bin/quill menu", path=path)
        check("and it can be added back",
              [b.chord for b in kb.read(path) if kb.is_pointer(b.chord)],
              ["SUPER + SHIFT + mouse:273"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall good")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
