"""Installing the themed stylesheet, and keeping it current.

theme.py deliberately knows nothing about GTK so it can be read and tested
without a display. This is the other half: it hands the generated CSS to a
display, tells libadwaita whether the palette is light or dark, and re-does
both when the desktop theme changes underneath a window that is already open.
"""

from __future__ import annotations

from gi.repository import Adw, Gdk, Gio, Gtk

from . import theme


def apply(display: Gdk.Display | None = None) -> Gtk.CssProvider | None:
    """Paint every window on `display` in the current theme."""
    display = display or Gdk.Display.get_default()
    if display is None:
        return None

    provider = Gtk.CssProvider()
    provider.load_from_data(theme.stylesheet().encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _match_color_scheme()
    return provider


def _match_color_scheme() -> None:
    """Follow the theme's own light/dark, not the desktop-wide preference.

    omarchy-theme-set-gnome sets prefer-dark from the same colours, so the two
    normally agree -- but Quill can be launched during a theme switch, and
    following the palette it is actually painting with is the honest choice.
    """
    palette = theme.load()
    if palette is None:
        return
    manager = Adw.StyleManager.get_default()
    manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK if palette.is_dark
                             else Adw.ColorScheme.FORCE_LIGHT)


def watch(provider: Gtk.CssProvider | None) -> Gio.FileMonitor | None:
    """Re-skin an open window when the desktop theme changes.

    omarchy-theme-set repoints ~/.local/state/omarchy/current/theme at the new
    theme, so the directory is what changes; watching colors.toml through the
    symlink would monitor the old theme's file. The returned monitor has to be
    kept alive by the caller or GTK collects it and the watch stops.
    """
    if provider is None:
        return None
    directory = Gio.File.new_for_path(str(theme.state_dir()))
    try:
        monitor = directory.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
    except Exception:
        return None

    def changed(*_args) -> None:
        theme.corner_radius.cache_clear()
        theme.mono_family.cache_clear()
        try:
            provider.load_from_data(theme.stylesheet().encode("utf-8"))
        except Exception:
            return          # a half-written colors.toml; the next event wins
        _match_color_scheme()

    monitor.connect("changed", changed)
    return monitor
