"""Quill must resolve an Omarchy palette exactly as Omarchy does.

    python3 tests/test_theme.py

Every theme ships a partial colors.toml -- some define only ANSI colorN names,
some leave the derived shades out entirely -- and Omarchy fills the gaps with
a specific cascade of aliases, fallbacks and colour mixes. Quill reimplements
that cascade in Python, so the only test worth running is the differential
one: for every theme on this machine, does Quill produce the same value for
every key as `omarchy-theme-color --all`?

It caught two real bugs. `brown` was never derived, and `mix` rounded halves
to even where awk rounds them up, which put one theme's brown one unit out.

Skips cleanly where Omarchy is not installed.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from quill import theme

THEME_DIRS = [
    Path.home() / ".config" / "omarchy" / "themes",
    Path("/usr/share/omarchy/themes"),
]

FAILURES = []


def omarchy_says(colors: Path) -> dict[str, str]:
    out = subprocess.run(
        ["omarchy-theme-color", "--file", str(colors), "--all"],
        capture_output=True, text=True, timeout=30, check=False)
    found = {}
    for line in out.stdout.splitlines():
        key, _, value = line.partition("\t")
        if key:
            found[key] = value
    return found


def check_theme(colors: Path) -> None:
    name = colors.parent.name
    palette = theme.load(colors)
    if palette is None:
        print(f"FAIL: {name} — Quill could not read it")
        FAILURES.append(name)
        return

    expected = omarchy_says(colors)
    if not expected:
        print(f"FAIL: {name} — omarchy-theme-color returned nothing")
        FAILURES.append(name)
        return

    wrong = []
    for key, value in expected.items():
        # theme_type is the legacy spelling of mode; cursor is terminal-only.
        if key in ("cursor", "theme_type"):
            continue
        mine = palette.colors.get(key)
        if mine is None or mine.lower() != value.lower():
            wrong.append(f"{key}={mine!r} not {value!r}")

    # Whatever colour is painted on a filled button has to be readable on it.
    unreadable = []
    for key in ("accent", "red", "green", "yellow", "blue"):
        fill = palette.get(key)
        if not fill:
            continue
        ratio = theme._contrast(fill, theme.on_color(palette, fill))
        if ratio < theme._READABLE:
            unreadable.append(f"{key} {ratio:.2f}:1")

    if wrong or unreadable:
        FAILURES.append(name)
        print(f"FAIL: {name} ({palette.mode})")
        for line in (wrong + unreadable)[:6]:
            print(f"        {line}")
    else:
        print(f"PASS: {name} ({palette.mode}) — {len(expected)} keys match")


def main() -> int:
    if not shutil.which("omarchy-theme-color"):
        print("SKIP: Omarchy is not installed here")
        return 0

    themes = [d / "colors.toml"
              for base in THEME_DIRS if base.is_dir()
              for d in sorted(base.iterdir()) if (d / "colors.toml").is_file()]
    if not themes:
        print("SKIP: no themes with a colors.toml")
        return 0

    for colors in themes:
        check_theme(colors)

    print(f"\n{len(FAILURES)} of {len(themes)} themes differ"
          if FAILURES else f"\nall {len(themes)} themes resolve identically")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
