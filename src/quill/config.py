"""User configuration: ~/.config/quill/config.toml (all keys optional)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .actions import DEFAULT_ACTIONS, Action

DEFAULT_MODEL = "gemma4:12b-it-qat"
DEFAULT_HOST = "http://127.0.0.1:11434"


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "quill" / "config.toml"


@dataclass
class Config:
    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    # Holding the model resident is what makes the second invocation feel instant.
    keep_alive: str = "30m"
    num_ctx: int = 8192
    request_timeout: float = 120.0
    restore_clipboard: bool = True
    # Replace immediately instead of showing the result for review first.
    auto_replace: bool = False
    actions: list[Action] = field(default_factory=lambda: list(DEFAULT_ACTIONS))

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

    cfg.model = str(raw.get("model", cfg.model))
    cfg.host = str(raw.get("host", cfg.host))
    cfg.keep_alive = str(raw.get("keep_alive", cfg.keep_alive))
    cfg.num_ctx = int(raw.get("num_ctx", cfg.num_ctx))
    cfg.request_timeout = float(raw.get("request_timeout", cfg.request_timeout))
    cfg.restore_clipboard = bool(raw.get("restore_clipboard", cfg.restore_clipboard))
    cfg.auto_replace = bool(raw.get("auto_replace", cfg.auto_replace))

    if isinstance(raw.get("actions"), list):
        cfg.actions = _parse_actions(raw["actions"], list(DEFAULT_ACTIONS))
    return cfg
