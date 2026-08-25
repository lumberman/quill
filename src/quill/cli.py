"""Command line entry points."""

from __future__ import annotations

import argparse
import atexit
import os
import signal
import sys
import threading
from pathlib import Path

from . import clipboard, hypr, ollama, state
from .actions import build_messages
from .config import Config, config_path, load


def _runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    path = Path(base) / "quill"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pid_file() -> Path:
    return _runtime_dir() / "menu.pid"


def _dismiss_existing() -> bool:
    """Toggle behaviour: a second press closes the open menu, like omasnap."""
    pid_file = _pid_file()
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        return False
    except PermissionError:
        return False
    pid_file.unlink(missing_ok=True)
    return True


def _preflight(cfg: Config) -> bool:
    if not ollama.is_up(cfg):
        hypr.notify(
            "Quill: Ollama is not running",
            "Start it with:  systemctl --user start ollama\n"
            "or system-wide:  sudo systemctl start ollama",
            urgency="critical",
        )
        return False
    if not ollama.has_model(cfg):
        hypr.notify(
            f"Quill: model '{cfg.model}' is not installed",
            f"Install it with:  ollama pull {cfg.model}",
            urgency="critical",
        )
        return False
    return True


def cmd_menu(args, cfg: Config) -> int:
    if _dismiss_existing():
        return 0
    _pid_file().write_text(str(os.getpid()))
    # However this process ends -- replace, escape, crash -- the bar must
    # not be left showing a spinner.
    atexit.register(state.write, state.IDLE)
    try:
        win = hypr.active_window()
        text, saved = clipboard.capture_selection(win)
        if not text.strip():
            hypr.notify("Quill: nothing selected", "Select some text, then try again.")
            return 1
        if not _preflight(cfg):
            if saved is not None and cfg.restore_clipboard:
                clipboard.set_clipboard(saved)
            return 1

        from . import ui  # imported lazily so non-GUI commands stay light

        return ui.run(cfg, text, win, saved)
    finally:
        _pid_file().unlink(missing_ok=True)


def cmd_run(args, cfg: Config) -> int:
    """Apply one action straight to the selection, no menu."""
    action = cfg.action(args.action)
    if action is None:
        print(f"Unknown action: {args.action}", file=sys.stderr)
        return 2
    win = hypr.active_window()
    text, saved = clipboard.capture_selection(win)
    if not text.strip():
        hypr.notify("Quill: nothing selected", "Select some text, then try again.")
        return 1
    if not _preflight(cfg):
        return 1

    messages = build_messages(action, text, args.instruction or "")
    try:
        with state.working(action.label):
            chunks = list(ollama.stream_chat(cfg, messages, action.temperature))
    except ollama.OllamaError as exc:
        hypr.notify("Quill failed", str(exc), urgency="critical")
        return 1

    result = ollama.clean_output("".join(chunks), text)
    if not result.strip():
        hypr.notify("Quill: empty result")
        return 1
    clipboard.replace_selection(result, win, saved,
                                restore_clipboard=cfg.restore_clipboard)
    return 0


def cmd_filter(args, cfg: Config) -> int:
    """stdin -> stdout. Lets other tools borrow Quill's model and prompts.

    This is the shape voxtype's [output.post_process] expects, so dictation can
    be cleaned up by the same local model that powers the popup.
    """
    text = sys.stdin.read()
    if not text.strip():
        return 0
    action = cfg.action(args.action)
    if action is None:
        print(text, end="")
        return 2
    if not ollama.is_up(cfg) or not ollama.has_model(cfg):
        # Never swallow the caller's text just because the model is unavailable.
        print(text, end="")
        return 1
    messages = build_messages(action, text, args.instruction or "")
    try:
        with state.working(action.label):
            chunks = list(ollama.stream_chat(cfg, messages, action.temperature))
    except ollama.OllamaError:
        print(text, end="")
        return 1
    result = ollama.clean_output("".join(chunks), text)
    print(result or text, end="")
    return 0


def cmd_doctor(args, cfg: Config) -> int:
    ok = True
    print(f"config       {config_path()}"
          f"{'' if config_path().exists() else '  (not created, using defaults)'}")
    print(f"host         {cfg.host}")
    print(f"model        {cfg.model}")

    import shutil
    for tool in ("hyprctl", "wl-copy", "wl-paste", "notify-send", "ollama"):
        found = shutil.which(tool)
        print(f"{tool:<12} {found or 'MISSING'}")
        if not found and tool != "ollama":
            ok = False

    up = ollama.is_up(cfg)
    print(f"ollama api   {'reachable' if up else 'NOT reachable'}")
    ok = ok and up

    if up:
        names = ollama.installed_models(cfg)
        present = ollama.has_model(cfg)
        print(f"model ready  {'yes' if present else 'NO — run: ollama pull ' + cfg.model}")
        print(f"installed    {', '.join(names) if names else '(none)'}")
        ok = ok and present

    win = hypr.active_window()
    print(f"hyprland     {'ok' if win is not None or hypr.monitors() else 'NOT responding'}")
    print(f"actions      {', '.join(a.id for a in cfg.actions)}")
    print("\nstatus:", "ready" if ok else "not ready")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quill",
        description="Local-AI writing assistant for Omarchy: rewrite or "
                    "spellcheck the selected text anywhere on the desktop.",
    )
    parser.add_argument("--config", type=Path, help="path to config.toml")
    parser.add_argument("--model", help="override the configured model")
    sub = parser.add_subparsers(dest="command")

    p_menu = sub.add_parser("menu", help="show the action menu at the cursor")
    p_menu.set_defaults(func=cmd_menu)

    p_run = sub.add_parser("run", help="apply one action to the selection")
    p_run.add_argument("action", help="action id, e.g. fix or rewrite")
    p_run.add_argument("-i", "--instruction", help="instruction for the custom action")
    p_run.set_defaults(func=cmd_run)

    p_filter = sub.add_parser("filter", help="stdin -> stdout, for other tools")
    p_filter.add_argument("action", nargs="?", default="fix")
    p_filter.add_argument("-i", "--instruction")
    p_filter.set_defaults(func=cmd_filter)

    p_doctor = sub.add_parser("doctor", help="check the install")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load(args.config)
    if args.model:
        cfg.model = args.model
    func = getattr(args, "func", None)
    if func is None:
        args.func = cmd_menu
        return cmd_menu(args, cfg)
    return func(args, cfg)
