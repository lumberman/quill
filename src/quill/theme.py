"""The current Omarchy theme, read as Quill's own colours.

Omarchy keeps the active palette at
``~/.local/state/omarchy/current/theme/colors.toml`` and re-renders per-app
config from ``*.tpl`` templates every time the theme changes. GTK apps are not
part of that pipeline: ``omarchy-theme-set-gnome`` only flips Adwaita between
its light and dark presets, so a GTK app on Omarchy is stock blue Adwaita on a
desktop that is otherwise, say, entirely blue-grey Lumon.

So Quill reads the palette itself, applying the same alias and fallback
cascade ``omarchy-theme-color`` uses. Resolving it the same way is the whole
point -- a colour Quill shows has to be the colour the bar, the menus and the
terminal are showing at that moment, not an approximation of it.

Nothing here is required. Off Omarchy, or before any theme has been set,
``load()`` returns None and the window keeps libadwaita's own colours.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Where omarchy-theme-set points the "current" symlink. Overridable so the
# themed window can be pointed at a palette without switching the desktop.
_DEFAULT_STATE = Path.home() / ".local" / "state" / "omarchy" / "current"


def state_dir() -> Path:
    override = os.environ.get("QUILL_THEME_STATE")
    return Path(override) if override else _DEFAULT_STATE

# Semantic keys Omarchy guarantees, in the order it derives them.
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def colors_path() -> Path:
    return state_dir() / "theme" / "colors.toml"


def name_path() -> Path:
    return state_dir() / "theme.name"


def _mix(start: str, end: str, amount: float) -> str:
    """Blend two hex colours, as omarchy-theme-color's mix_color does."""
    if not (_HEX.match(start) and _HEX.match(end)):
        return start
    amount = max(0.0, min(1.0, amount))
    out = []
    for index in (1, 3, 5):
        a = int(start[index:index + 2], 16)
        b = int(end[index:index + 2], 16)
        # int(x + 0.5), not round(): Python rounds halves to even, awk does
        # not, and the two disagree on exactly the .5 cases mix_color hits.
        out.append(int(a * (1 - amount) + b * amount + 0.5))
    return "#%02x%02x%02x" % tuple(out)


@dataclass(frozen=True)
class Palette:
    """A resolved Omarchy palette. Missing keys are derived, never guessed."""

    name: str
    mode: str
    colors: dict[str, str]

    @property
    def is_dark(self) -> bool:
        return self.mode != "light"

    def get(self, key: str, fallback: str = "") -> str:
        return self.colors.get(key) or fallback

    def __getitem__(self, key: str) -> str:
        return self.colors[key]


def _parse(path: Path) -> dict[str, str]:
    """colors.toml, tolerating the odd hand-written theme.

    tomllib first because it is correct; a line parser after it because a
    third-party theme with one stray line should cost the user their accent
    colour, not the entire palette.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    try:
        loaded = tomllib.loads(raw.decode("utf-8"))
        return {k: str(v) for k, v in loaded.items() if isinstance(v, (str, int, float))}
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        pass

    found: dict[str, str] = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip().strip("\"'")] = value.strip().strip("\"'").split("#")[0].strip()
    return found


def _resolve(found: dict[str, str]) -> dict[str, str]:
    """Fill in what a theme leaves out, the way Omarchy fills it in.

    Only the branches that matter for a GTK window are reproduced; the ANSI
    colorN compatibility layer is included because plenty of older themes
    define nothing else.
    """
    c = dict(found)

    # Legacy short names.
    for canonical, short in (
        ("background", "bg"), ("dark_background", "dark_bg"),
        ("darker_background", "darker_bg"), ("lighter_background", "lighter_bg"),
        ("foreground", "fg"), ("dark_foreground", "dark_fg"),
        ("light_foreground", "light_fg"), ("bright_foreground", "bright_fg"),
    ):
        c.setdefault(canonical, c.get(short, ""))

    # Themes predating the semantic palette define only ANSI names.
    c["background"] = c.get("background") or c.get("color0", "")
    c["foreground"] = c.get("foreground") or c.get("color7", "")
    for name, ansi in (
        ("red", "color1"), ("green", "color2"), ("yellow", "color3"),
        ("blue", "color4"), ("magenta", "color5"), ("cyan", "color6"),
        ("bright_red", "color9"), ("bright_green", "color10"),
        ("bright_yellow", "color11"), ("bright_blue", "color12"),
        ("bright_magenta", "color13"), ("bright_cyan", "color14"),
    ):
        c[name] = c.get(name) or c.get(ansi, "")
    c["magenta"] = c.get("magenta") or c.get("purple", "")

    background = c.get("background") or "#1a1a1a"
    foreground = c.get("foreground") or "#eeeeee"
    c["background"], c["foreground"] = background, foreground

    c["bright_foreground"] = (c.get("bright_foreground")
                              or c.get("color15") or foreground)
    c["light_foreground"] = c.get("light_foreground") or foreground
    c["lighter_background"] = (c.get("lighter_background")
                               or c.get("color0") or background)
    c["dark_foreground"] = (c.get("dark_foreground")
                            or c.get("color8") or foreground)
    c["muted"] = c.get("muted") or c.get("color8") or c["dark_foreground"]
    c["selection"] = (c.get("selection") or c.get("selection_background")
                      or c.get("color8") or background)
    c["accent"] = c.get("accent") or c.get("blue") or c.get("cyan") or foreground
    c["orange"] = c.get("orange") or c.get("yellow") or c["accent"]
    c["dark_background"] = c.get("dark_background") or _mix(background, "#000000", 0.25)
    c["darker_background"] = (c.get("darker_background")
                              or _mix(background, "#000000", 0.50))
    for base in ("red", "green", "yellow", "blue", "cyan", "magenta"):
        c[base] = c.get(base) or c["accent"]
        c[f"bright_{base}"] = c.get(f"bright_{base}") or _mix(c[base], "#ffffff", 0.20)
    c["brown"] = c.get("brown") or _mix(c["orange"], "#000000", 0.50)
    c["purple"] = c.get("purple") or c["magenta"]
    c["bright_purple"] = c.get("bright_purple") or c["bright_magenta"]
    c["selection_background"] = c.get("selection_background") or c["selection"]
    c["selection_foreground"] = (c.get("selection_foreground")
                                 or c["bright_foreground"])

    # ...and the ANSI names, for consumers that still ask for colorN.
    for ansi, canonical in (
        ("color0", "background"), ("color1", "red"), ("color2", "green"),
        ("color3", "yellow"), ("color4", "blue"), ("color5", "magenta"),
        ("color6", "cyan"), ("color7", "foreground"), ("color8", "muted"),
        ("color9", "bright_red"), ("color10", "bright_green"),
        ("color11", "bright_yellow"), ("color12", "bright_blue"),
        ("color13", "bright_magenta"), ("color14", "bright_cyan"),
        ("color15", "bright_foreground"),
    ):
        c.setdefault(ansi, c.get(canonical, ""))
        c[ansi] = c[ansi] or c.get(canonical, "")

    # Omarchy writes the short names back so old templates keep working.
    for canonical, short in (
        ("background", "bg"), ("dark_background", "dark_bg"),
        ("darker_background", "darker_bg"), ("lighter_background", "lighter_bg"),
        ("foreground", "fg"), ("dark_foreground", "dark_fg"),
        ("light_foreground", "light_fg"), ("bright_foreground", "bright_fg"),
    ):
        if c.get(canonical):
            c[short] = c[canonical]

    return {k: v for k, v in c.items() if v}


def _mode(colors: dict[str, str], directory: Path) -> str:
    """Theme mode, by the same precedence Omarchy uses."""
    declared = colors.get("mode") or colors.get("theme_type")
    if declared in ("light", "dark"):
        return declared
    if (directory / "light.mode").exists():
        return "light"
    background = colors.get("background", "")
    if _HEX.match(background):
        total = sum(int(background[i:i + 2], 16) for i in (1, 3, 5))
        return "light" if total > 382 else "dark"
    return "dark"


def load(path: Path | None = None) -> Palette | None:
    """The palette in force right now, or None when Omarchy is not themed."""
    if path is None and os.environ.get("QUILL_NO_THEME"):
        return None            # asked for stock libadwaita
    path = path or colors_path()
    found = _parse(path)
    if not found:
        return None
    colors = _resolve(found)
    if "background" not in colors or "foreground" not in colors:
        return None
    try:
        name = name_path().read_text(encoding="utf-8").strip()
    except OSError:
        name = path.parent.name
    return Palette(name=name, mode=_mode(found, path.parent), colors=colors)


@lru_cache(maxsize=1)
def mono_family() -> str:
    """Whatever `monospace` resolves to, which is what the shell renders in.

    Omarchy's Style.qml asks fc-match the same question rather than naming a
    font, so a user who changed their terminal font gets it here too.
    """
    try:
        out = subprocess.run(["fc-match", "-f", "%{family[0]}", "monospace"],
                             capture_output=True, text=True, timeout=5, check=False)
        family = out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        family = ""
    return family or "monospace"


@lru_cache(maxsize=1)
def corner_radius() -> int:
    """Hyprland's window rounding, so Quill's corners match its own frame.

    The shell reads decoration:rounding for exactly this reason; a settings
    window with 12px cards inside an 8px window looks like two designs.
    """
    try:
        out = subprocess.run(["hyprctl", "getoption", "decoration:rounding", "-j"],
                             capture_output=True, text=True, timeout=5, check=False)
        import json
        value = int(json.loads(out.stdout).get("int", 8))
    except (subprocess.SubprocessError, OSError, ValueError, TypeError):
        return 8
    return max(0, min(24, value))


def available() -> bool:
    return colors_path().exists() and not os.environ.get("QUILL_NO_THEME")


def _luminance(hex_color: str) -> float:
    """WCAG relative luminance, for picking readable text over a fill."""
    if not _HEX.match(hex_color):
        return 0.0
    channels = []
    for index in (1, 3, 5):
        value = int(hex_color[index:index + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928
                        else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(one: str, two: str) -> float:
    a, b = _luminance(one), _luminance(two)
    light, dark = max(a, b), min(a, b)
    return (light + 0.05) / (dark + 0.05)


#: WCAG AA for body text. Below this a filled button is not readable.
_READABLE = 4.5


def on_color(palette: Palette, fill: str) -> str:
    """Text that can actually be read on `fill`.

    Omarchy's own GTK example hardcodes the theme background as the label
    colour of an accent-filled button. That works on the dark themes it was
    written against and fails on the light ones -- rose-pine's accent is a
    mid teal, and no colour in that palette clears 4.5:1 against it. So the
    palette is tried first, and only when none of it is readable does this
    fall back to the black or white that is.
    """
    candidates = [palette.get(key) for key in
                  ("background", "bright_foreground", "foreground")]
    candidates = [c for c in candidates if c]
    if not candidates:
        return "#ffffff"
    best = max(candidates, key=lambda c: _contrast(fill, c))
    if _contrast(fill, best) >= _READABLE:
        return best
    return ("#ffffff" if _contrast(fill, "#ffffff") > _contrast(fill, "#000000")
            else "#000000")


def stylesheet(palette: Palette | None = None,
               radius: int | None = None,
               font: str | None = None) -> str:
    """Quill's CSS, painted in the current theme.

    libadwaita documents its named colours as overridable with
    @define-color, so re-pointing them at the palette re-skins every stock
    widget -- rows, switches, entries, the header bar -- without Quill having
    to restyle each one. What is left below is the geometry and the handful of
    classes that are Quill's own.
    """
    if palette is None:
        palette = load()
    if palette is None:
        return _STRUCTURE.format(radius=8, small=6, tiny=5, font="inherit")

    radius = corner_radius() if radius is None else radius
    font = mono_family() if font is None else font
    c = palette.get
    accent = c("accent")

    defines = f"""
/* --- {palette.name or "current"} theme, resolved exactly as omarchy-theme-color
   resolves it. Every name below is one libadwaita documents as overridable. */
@define-color window_bg_color {c("background")};
@define-color window_fg_color {c("foreground")};
@define-color view_bg_color {c("background")};
@define-color view_fg_color {c("foreground")};
@define-color card_bg_color {c("lighter_background")};
@define-color card_fg_color {c("foreground")};
@define-color card_shade_color alpha({c("darker_background")}, 0.36);
@define-color headerbar_bg_color {c("background")};
@define-color headerbar_fg_color {c("foreground")};
@define-color headerbar_border_color alpha({c("foreground")}, 0.15);
@define-color headerbar_backdrop_color {c("background")};
@define-color headerbar_shade_color transparent;
@define-color popover_bg_color {c("dark_background")};
@define-color popover_fg_color {c("foreground")};
@define-color dialog_bg_color {c("dark_background")};
@define-color dialog_fg_color {c("foreground")};
@define-color sidebar_bg_color {c("dark_background")};
@define-color sidebar_fg_color {c("foreground")};
@define-color shade_color alpha({c("darker_background")}, 0.36);
@define-color scrollbar_outline_color transparent;
@define-color borders alpha({c("foreground")}, 0.4);

@define-color accent_bg_color {accent};
@define-color accent_fg_color {on_color(palette, accent)};
@define-color accent_color {accent};
@define-color success_bg_color {c("green")};
@define-color success_fg_color {on_color(palette, c("green"))};
@define-color success_color {c("bright_green")};
@define-color warning_bg_color {c("yellow")};
@define-color warning_fg_color {on_color(palette, c("yellow"))};
@define-color warning_color {c("bright_yellow")};
@define-color error_bg_color {c("red")};
@define-color error_fg_color {on_color(palette, c("red"))};
@define-color error_color {c("bright_red")};
@define-color destructive_bg_color {c("red")};
@define-color destructive_fg_color {on_color(palette, c("red"))};
@define-color destructive_color {c("bright_red")};

@define-color quill_selection {c("selection")};
@define-color quill_muted {c("muted")};
@define-color quill_keycap_top {c("lighter_background")};
@define-color quill_keycap_bottom {c("dark_background")};
@define-color quill_keycap_edge {c("darker_background")};
@define-color quill_keycap_text {c("bright_foreground")};
"""
    return defines + _STRUCTURE.format(
        radius=radius, small=max(0, radius - 2), tiny=max(0, radius - 3),
        font=f'"{font}", monospace')


# Geometry and Quill's own classes. Written against the @define-color names
# above so the same rules work unthemed, where libadwaita supplies them.
_STRUCTURE = """
window, popover, .background {{
  font-family: {font};
}}

/* --- the shell's panel grammar ----------------------------------------
   Every Omarchy panel -- bluetooth, network, audio, the menu -- is built the
   same way, and none of it is Adwaita's way:

     * no cards. Content sits on the panel background and sections are told
       apart by a hairline rule, not by a box drawn around each one;
     * sections are announced by a small tracked capital label, dimmed;
     * rows are flat and transparent, with exactly one filled row per group
       marking what is current, its label in the accent colour;
     * hierarchy is carried by brightness. Almost nothing is bold, and
       nothing is bold *and* large -- dimming does the work that weight and
       boxes do in Adwaita;
     * small exclusive choices are outlined buttons in a row, spaced apart,
       with the chosen one filled.

   These rules put that grammar over libadwaita's widgets. */

list.boxed-list, .card {{
  background: none;
  border: none;
  box-shadow: none;
}}
/* libadwaita separates boxed-list rows with a bottom border; the shell's
   lists have none, and the spacing does the separating. */
list.boxed-list > row,
list.boxed-list > row:not(:last-child),
row.expander list > row,
row.expander list > row:not(:last-child) {{
  border-bottom-width: 0;
  box-shadow: none;
}}

/* A section: hairline, then tracked capitals. */
preferencesgroup > box > box.header {{
  border-top: 1px solid alpha(@window_fg_color, 0.13);
  padding-top: 20px;
  margin-top: 12px;
  margin-bottom: 2px;
}}
preferencesgroup.quill-plain > box > box.header {{
  border-top: none;
  padding-top: 0;
  margin-top: 0;
}}
preferencesgroup > box > box.header label.heading {{
  font-size: 0.8em;
  font-weight: 400;
  letter-spacing: 0.14em;
  opacity: 0.5;
}}
preferencesgroup > box > box.header label.body.dimmed {{
  opacity: 0.45;
  margin-top: 6px;
}}

/* Rows: flat, and only the current one is filled. */
row {{
  background-color: transparent;
  border-radius: {small}px;
}}
row.activatable:hover {{
  background-color: alpha(@window_fg_color, 0.06);
}}
row.activatable:active {{
  background-color: alpha(@window_fg_color, 0.18);
}}
row.quill-current {{
  background-color: alpha(@window_fg_color, 0.09);
  box-shadow: inset 0 0 0 1px alpha(@window_fg_color, 0.20);
}}
row.quill-current > box.header > box.title > label.title {{
  color: @accent_color;
}}

/* Outlined, spaced, one of them filled: the shell's segmented control. */
.quill-segment button {{
  background: none;
  background-image: none;
  border: 1px solid alpha(@window_fg_color, 0.28);
  border-radius: {small}px;
  padding: 7px 14px;
  font-weight: 400;
}}
.quill-segment button:hover {{
  background-color: alpha(@window_fg_color, 0.06);
}}
.quill-segment button:checked {{
  background-color: alpha(@window_fg_color, 0.10);
  border-color: alpha(@window_fg_color, 0.55);
  color: @accent_color;
}}

button {{
  border-radius: {small}px;
}}
/* Actions inside a row are outlined, like the shell's segment buttons.
   A bare bold word floating in a row reads as text, not as something to
   press. */
button.quill-outline {{
  background: none;
  background-image: none;
  border: 1px solid alpha(@window_fg_color, 0.28);
  padding: 6px 12px;
  font-weight: 400;
}}
button.quill-outline:hover {{
  background-color: alpha(@window_fg_color, 0.08);
  border-color: alpha(@window_fg_color, 0.5);
}}
button.flat:hover {{
  background-color: alpha(@window_fg_color, 0.08);
}}
entry, spinbutton, textview {{
  border-radius: {small}px;
}}
progressbar > trough {{
  min-height: 4px;
  border-radius: 999px;
  background-color: alpha(@window_fg_color, 0.14);
}}
progressbar > trough > progress {{
  min-height: 4px;
  border-radius: 999px;
  background-color: @window_fg_color;
}}

/* The panel header: a big glyph, the name, and a tracked-capital line of
   status under it -- "MAX 5X", "UNTANGLING WIRES", "WIRING BITS". */
.quill-app-name {{
  font-size: 1.55em;
  font-weight: 400;
}}
.quill-app-status {{
  font-size: 0.78em;
  letter-spacing: 0.14em;
  opacity: 0.5;
}}
.quill-rule {{
  min-height: 1px;
  background-color: alpha(@window_fg_color, 0.13);
}}

/* Status pills: state as a word, at the weight of a label. */
.quill-badge {{
  font-size: 0.74em;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background-color: alpha(currentColor, 0.13);
  letter-spacing: 0.4px;
}}
.quill-secondary {{
  padding: 8px 0 0 0;
}}
.quill-hero {{
  padding: 20px 0 16px 0;
}}
.quill-tutorial {{
  padding: 16px 16px 18px 16px;
  background-color: alpha(@window_fg_color, 0.045);
  border-radius: {radius}px;
}}

/* Keycaps. A thick border-bottom renders as a flange outside the rounded
   corners, so the depth comes from stacked box-shadows instead: one hard
   offset for the key's side wall, one soft one for the shadow it casts. The
   colours are the theme's own darker shades, so a key reads as a key in
   every palette rather than as a black rectangle in most of them. */
.quill-keycap {{
  background-image: linear-gradient(180deg,
                    @quill_keycap_top 0%, @quill_keycap_bottom 100%);
  color: @quill_keycap_text;
  border: 1px solid alpha(@window_fg_color, 0.22);
  border-radius: {tiny}px;
  padding: 5px 11px;
  min-width: 15px;
  font-weight: 700;
  box-shadow: inset 0 1px 0 alpha(@window_fg_color, 0.14),
              0 2px 0 @quill_keycap_edge,
              0 3px 5px alpha(@quill_keycap_edge, 0.55);
}}
.quill-keycap-lg {{
  font-size: 1.75em;
  padding: 9px 20px;
  min-width: 34px;
  border-radius: {small}px;
  box-shadow: inset 0 1.5px 0 alpha(@window_fg_color, 0.16),
              0 4px 0 @quill_keycap_edge,
              0 6px 10px alpha(@quill_keycap_edge, 0.6);
}}
.quill-plus {{
  font-size: 1.15em;
  font-weight: 700;
  opacity: 0.4;
}}
.quill-plus-lg {{ font-size: 1.6em; }}

/* Editable blocks of text: the playground sample and every edit's prompt. */
.quill-sample,
.quill-sample text,
.quill-sample text selection {{
  background-color: transparent;
  background-image: none;
}}
.quill-sample text selection {{
  background-color: @quill_selection;
  color: @window_fg_color;
}}
.quill-sample-frame {{
  border: 1px solid alpha(@window_fg_color, 0.35);
  border-radius: {small}px;
  padding: 8px 10px;
}}
.quill-result-frame {{
  border: 1px solid alpha(@accent_color, 0.45);
  background-color: alpha(@accent_color, 0.10);
  border-radius: {small}px;
  padding: 8px 10px;
}}
.quill-was {{
  opacity: 0.55;
  text-decoration: line-through;
}}
.quill-step {{
  font-weight: 700;
  font-size: 0.78em;
  opacity: 0.55;
  letter-spacing: 0.8px;
}}

/* --- the popup ---------------------------------------------------------
   Omarchy's own menus are a flat card on the theme background with the
   Hyprland active-border colour around it, and mark the current row with a
   wash of the foreground plus accent-coloured text rather than a filled bar.
   Matching that is most of what makes the popup read as part of the desktop
   rather than as a GTK application that happens to be open. */
window.quill {{ background: transparent; }}

.quill-card {{
  background-color: @window_bg_color;
  border: 2px solid @accent_color;
  border-radius: {radius}px;
  box-shadow: 0 8px 28px alpha(black, 0.45);
}}
.quill-header {{
  padding: 10px 14px 6px 14px;
  border-bottom: 1px solid alpha(@window_fg_color, 0.12);
}}
.quill-snippet {{ font-size: 0.85em; opacity: 0.72; }}
.quill-meta {{ font-size: 0.78em; opacity: 0.5; }}

.quill-list {{ background: transparent; padding: 6px; }}
.quill-list row {{
  border-radius: {small}px;
  padding: 7px 10px;
  min-height: 0;
}}
.quill-list row:selected {{
  background-color: alpha(@window_fg_color, 0.10);
  color: @accent_color;
}}
.quill-key {{
  font-size: 0.78em;
  opacity: 0.42;
  min-width: 14px;
}}
.quill-footer {{
  padding: 8px 12px;
  border-top: 1px solid alpha(@window_fg_color, 0.12);
}}
.quill-result {{ font-size: 0.95em; }}
.quill-result text {{ background: transparent; }}
.quill-error {{ color: @error_color; font-size: 0.86em; }}
"""
