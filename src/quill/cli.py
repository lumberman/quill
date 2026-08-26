"""Command line entry points."""

from __future__ import annotations

import argparse
import atexit
import os
import signal
import sys
import threading
from pathlib import Path

from . import clipboard, codex, hypr, ollama, openai_api, openrouter
from . import config as config_mod
from . import provider, sanitize, state
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
    usable, reason = provider.ready(cfg)
    if not usable:
        hypr.notify("Quill is not ready", reason, urgency="critical")
    return usable


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
            chunks = list(provider.stream_chat(cfg, messages, action.temperature))
    except provider.ProviderError as exc:
        hypr.notify("Quill failed", str(exc), urgency="critical")
        return 1

    result = sanitize.clean_output("".join(chunks), text)
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
    if not provider.ready(cfg)[0]:
        # Never swallow the caller's text just because the model is unavailable.
        print(text, end="")
        return 1
    messages = build_messages(action, text, args.instruction or "")
    try:
        with state.working(action.label):
            chunks = list(provider.stream_chat(cfg, messages, action.temperature))
    except provider.ProviderError:
        print(text, end="")
        return 1
    result = sanitize.clean_output("".join(chunks), text)
    print(result or text, end="")
    return 0


def cmd_login(args, cfg: Config) -> int:
    """Browser sign-in to OpenRouter (OAuth PKCE)."""
    if openrouter.has_key() and not args.force:
        print(f"Already connected (key from {openrouter.key_source()}).")
        print("Re-run with --force to replace it.")
        return 0
    print("Opening your browser to sign in to OpenRouter...")
    try:
        api_key = openrouter.login(
            on_url=lambda url: print(f"\nIf it did not open:\n  {url}\n"))
    except openrouter.OpenRouterError as exc:
        print(f"Sign-in failed: {exc}", file=sys.stderr)
        return 1
    from . import credentials
    print(f"Connected. Key {credentials.redact(api_key)} saved to "
          f"{openrouter.key_source()}.")
    return 0


def cmd_logout(args, cfg: Config) -> int:
    if not openrouter.has_key():
        print("Not connected.")
        return 0
    openrouter.forget_key()
    print("Disconnected. The key was removed from local storage.")
    print("Revoke it for good at https://openrouter.ai/settings/keys")
    return 0


def cmd_models(args, cfg: Config) -> int:
    """List what the selected provider can run."""
    if cfg.uses_openrouter:
        try:
            found = openrouter.models(free_only=not args.all)
        except openrouter.OpenRouterError as exc:
            print(f"{exc}", file=sys.stderr)
            return 1
        for model in found:
            marker = "free" if model["free"] else "paid"
            print(f"{model['id']:<52} {marker:>4}  ctx={model['context_length']}")
        return 0
    if cfg.uses_openai:
        try:
            for name in openai_api.models(cfg):
                print(name)
        except openai_api.OpenAIError as exc:
            print(f"{exc}", file=sys.stderr)
            return 1
        return 0
    if cfg.uses_codex:
        print("Codex chooses its own model; set codex_model to override.")
        print(codex.describe())
        return 0
    for name in ollama.installed_models(cfg):
        print(name)
    return 0


def cmd_settings(args, cfg: Config) -> int:
    from . import settings  # imported lazily so non-GUI commands stay light

    return settings.run(cfg)


def cmd_doctor(args, cfg: Config) -> int:
    ok = True
    print(f"config       {config_path()}"
          f"{'' if config_path().exists() else '  (not created, using defaults)'}")
    print(f"provider     {cfg.provider}")
    print(f"model        {cfg.active_model}")
    print(f"text stays local  {'yes' if cfg.is_local else 'NO — sent to the provider'}")
    if cfg.uses_openrouter:
        print(f"api key      {openrouter.key_source() or 'NOT CONNECTED'}")
    elif cfg.uses_openai:
        print(f"base url     {cfg.openai_base_url}")
        print(f"api key      {openai_api.key_source() or '(none stored)'}")
    elif cfg.uses_codex:
        print(f"codex        {codex.describe()}")
    else:
        print(f"host         {cfg.host}")

    import shutil
    optional = {"ollama", "secret-tool"}
    for tool in ("hyprctl", "wl-copy", "wl-paste", "notify-send", "ollama",
                 "secret-tool"):
        found = shutil.which(tool)
        print(f"{tool:<12} {found or 'MISSING'}")
        if not found and tool not in optional:
            ok = False

    usable, reason = provider.ready(cfg)
    print(f"backend      {reason}")
    ok = ok and usable

    if cfg.provider == config_mod.OLLAMA and ollama.is_up(cfg):
        names = ollama.installed_models(cfg)
        print(f"installed    {', '.join(names) if names else '(none)'}")

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

    p_settings = sub.add_parser("settings", help="open the settings window")
    p_settings.set_defaults(func=cmd_settings)

    p_login = sub.add_parser("login", help="sign in to OpenRouter in a browser")
    p_login.add_argument("--force", action="store_true",
                         help="replace an existing key")
    p_login.set_defaults(func=cmd_login)

    p_logout = sub.add_parser("logout", help="forget the stored OpenRouter key")
    p_logout.set_defaults(func=cmd_logout)

    p_models = sub.add_parser("models", help="list available models")
    p_models.add_argument("--all", action="store_true",
                          help="include paid OpenRouter models")
    p_models.set_defaults(func=cmd_models)

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
