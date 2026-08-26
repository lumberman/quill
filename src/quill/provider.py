"""Backend dispatch.

One place that knows which client to call, so the UI and CLI never branch on
provider themselves.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from . import ollama, openrouter
from .config import Config


class ProviderError(RuntimeError):
    pass


def stream_chat(cfg: Config, messages: list[dict], temperature: float = 0.2,
                cancel: threading.Event | None = None) -> Iterator[str]:
    try:
        if cfg.uses_openrouter:
            yield from openrouter.stream_chat(cfg, messages, temperature, cancel)
        else:
            yield from ollama.stream_chat(cfg, messages, temperature, cancel)
    except (ollama.OllamaError, openrouter.OpenRouterError) as exc:
        # Callers catch one exception type regardless of backend.
        raise ProviderError(str(exc)) from exc


def ready(cfg: Config) -> tuple[bool, str]:
    """(usable, human-readable reason). The reason is shown as-is."""
    if cfg.uses_openrouter:
        if not openrouter.has_key():
            return False, ("Not connected to OpenRouter — run `quill login` or "
                           "add a key in settings")
        if not openrouter.is_up():
            return False, "Cannot reach OpenRouter (no network?)"
        return True, f"OpenRouter · {cfg.openrouter_model}"

    if not ollama.is_up(cfg):
        return False, ("Ollama is not running — start it with: "
                       "systemctl start ollama")
    if not ollama.has_model(cfg):
        return False, f"Model '{cfg.model}' is not installed — ollama pull {cfg.model}"
    return True, f"Ollama · {cfg.model}"


def label(cfg: Config) -> str:
    """Short provider tag for the popup header."""
    return f"OpenRouter · {cfg.openrouter_model}" if cfg.uses_openrouter else cfg.model
