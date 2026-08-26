"""User configuration: ~/.config/quill/config.toml (all keys optional)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .actions import DEFAULT_ACTIONS, Action

DEFAULT_MODEL = "gemma4:12b-it-qat"
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_OPENROUTER_MODEL = "z-ai/glm-5.2:free"

OLLAMA = "ollama"
OPENROUTER = "openrouter"
PROVIDERS = (OLLAMA, OPENROUTER)


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "quill" / "config.toml"


@dataclass
class Config:
    # Which backend runs the edit. "ollama" keeps everything on this machine;
    # "openrouter" sends the selected text to a third party.
    provider: str = OLLAMA
    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    # Holding the model resident is what makes the second invocation feel instant.
    keep_alive: str = "30m"
    num_ctx: int = 8192
    # False disables a thinking model's reasoning channel; None omits the
    # field entirely, leaving the model default.
    think: bool | None = False
    request_timeout: float = 120.0
    restore_clipboard: bool = True
    # Replace immediately instead of showing the result for review first.
    auto_replace: bool = False
    # Kept separate from `model` so switching providers back and forth does
    # not lose the other one's choice.
    openrouter_model: str = DEFAULT_OPENROUTER_MODEL
    openrouter_free_only: bool = True
    actions: list[Action] = field(default_factory=lambda: list(DEFAULT_ACTIONS))

    @property
    def uses_openrouter(self) -> bool:
        return self.provider == OPENROUTER

    @property
    def active_model(self) -> str:
        """The model actually in play, whichever backend is selected."""
        return self.openrouter_model if self.uses_openrouter else self.model

    @property
    def chat_url(self) -> str:
        return self.host.rstrip("/") + "/api/chat"

    @property
    def tags_url(self) -> str:
        return self.host.rstrip("/") + "/api/tags"

    def action(self, action_id: str) -> Action | None:
        return next((a for a in self.actions if a.id == action_id), None)


def _parse_actions(raw: list, defaults: list[Action]) -> list[Action]:
    """An [[actions]] table replaces the built-in list outright.

    Entries may reference a built-in by id and override only some fields, so
    reordering or hiding actions does not mean retyping their prompts.
    """
    by_id = {a.id: a for a in defaults}
    out: list[Action] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        action_id = str(entry.get("id") or "").strip()
        if not action_id:
            continue
        base = by_id.get(action_id)
        if base is None:
            base = Action(
                id=action_id,
                label=entry.get("label", action_id),
                instruction=entry.get("instruction", ""),
            )
        out.append(
            replace(
                base,
                label=entry.get("label", base.label),
                instruction=entry.get("instruction", base.instruction),
                temperature=float(entry.get("temperature", base.temperature)),
                prompts_for_input=bool(
                    entry.get("prompts_for_input", base.prompts_for_input)
                ),
            )
        )
    return out or list(defaults)


def load(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or config_path()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    except (OSError, tomllib.TOMLDecodeError):
        # A broken config should degrade to defaults, not break the hotkey.
        return cfg

    provider = str(raw.get("provider", cfg.provider)).strip().lower()
    cfg.provider = provider if provider in PROVIDERS else cfg.provider
    cfg.model = str(raw.get("model", cfg.model))
    cfg.openrouter_model = str(raw.get("openrouter_model", cfg.openrouter_model))
    cfg.openrouter_free_only = bool(
        raw.get("openrouter_free_only", cfg.openrouter_free_only))
    cfg.host = str(raw.get("host", cfg.host))
    cfg.keep_alive = str(raw.get("keep_alive", cfg.keep_alive))
    cfg.num_ctx = int(raw.get("num_ctx", cfg.num_ctx))
    if "think" in raw:
        cfg.think = None if raw["think"] is None else bool(raw["think"])
    cfg.request_timeout = float(raw.get("request_timeout", cfg.request_timeout))
    cfg.restore_clipboard = bool(raw.get("restore_clipboard", cfg.restore_clipboard))
    cfg.auto_replace = bool(raw.get("auto_replace", cfg.auto_replace))

    if isinstance(raw.get("actions"), list):
        cfg.actions = _parse_actions(raw["actions"], list(DEFAULT_ACTIONS))
    return cfg


# --- writing ----------------------------------------------------------------
# tomllib reads but cannot write, and pulling in a dependency for eight scalars
# and one array-of-tables is not worth it.

def _toml_string(value: str) -> str:
    out = value.replace("\\", "\\\\").replace('"', '\\"')
    out = out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{out}"'


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return _toml_string(str(value))


def dumps(cfg: Config) -> str:
    lines = [
        "# Quill configuration.",
        "# Written by `quill settings` -- hand-edits survive, comments do not.",
        "",
    ]
    for key in ("provider", "model", "host", "keep_alive", "num_ctx",
                "request_timeout", "restore_clipboard", "auto_replace",
                "openrouter_model", "openrouter_free_only"):
        lines.append(f"{key} = {_toml_value(getattr(cfg, key))}")
    if cfg.think is not None:
        lines.append(f"think = {_toml_value(cfg.think)}")

    # Only spell out the menu when it actually differs, so an untouched config
    # keeps tracking future changes to the built-in prompts.
    if cfg.actions != list(DEFAULT_ACTIONS):
        for action in cfg.actions:
            lines += [
                "",
                "[[actions]]",
                f"id = {_toml_string(action.id)}",
                f"label = {_toml_string(action.label)}",
                f"instruction = {_toml_string(action.instruction)}",
                f"temperature = {_toml_value(float(action.temperature))}",
                f"prompts_for_input = {_toml_value(bool(action.prompts_for_input))}",
            ]
    return "\n".join(lines) + "\n"


def save(cfg: Config, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps(cfg), encoding="utf-8")
    tmp.replace(path)
    return path
