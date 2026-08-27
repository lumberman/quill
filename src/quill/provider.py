"""Backend dispatch.

One place that knows which client to call, so the UI and CLI never branch on
provider themselves.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from . import codex, models, ollama, openai_api, openrouter
from .config import CODEX, OLLAMA, OPENAI, OPENROUTER, Config

_ERRORS = (
    ollama.OllamaError,
    openrouter.OpenRouterError,
    openai_api.OpenAIError,
    codex.CodexError,
)


class ProviderError(RuntimeError):
    pass


def stream_chat(cfg: Config, messages: list[dict], temperature: float = 0.2,
                cancel: threading.Event | None = None) -> Iterator[str]:
    try:
        if cfg.provider == OPENROUTER:
            yield from openrouter.stream_chat(cfg, messages, temperature, cancel)
        elif cfg.provider == OPENAI:
            yield from openai_api.stream_chat(cfg, messages, temperature, cancel)
        elif cfg.provider == CODEX:
            yield from codex.stream_chat(cfg, messages, temperature, cancel)
        else:
            yield from ollama.stream_chat(cfg, messages, temperature, cancel)
    except _ERRORS as exc:
        # Callers catch one exception type regardless of backend.
        raise ProviderError(str(exc)) from exc


def streams_incrementally(cfg: Config) -> bool:
    """False when the answer only arrives all at once, so the UI can say so."""
    return cfg.provider != CODEX


def ready(cfg: Config) -> tuple[bool, str]:
    """(usable, human-readable reason). The reason is shown as-is."""
    if cfg.provider == OPENROUTER:
        if not openrouter.has_key():
            return False, ("Not connected to OpenRouter — run `quill login` or "
                           "add a key in settings")
        if not openrouter.is_up():
            return False, "Cannot reach OpenRouter (no network?)"
        return True, f"OpenRouter · {cfg.openrouter_model}"

    if cfg.provider == OPENAI:
        if openai_api.needs_key(cfg.openai_base_url) and not openai_api.key():
            return False, f"{cfg.openai_base_url} needs an API key — add one in settings"
        if not openai_api.is_up(cfg):
            return False, f"Cannot reach {cfg.openai_base_url}"
        return True, f"{cfg.openai_base_url} · {cfg.openai_model}"

    if cfg.provider == CODEX:
        if not codex.available():
            return False, "Codex CLI is not installed"
        if not codex.signed_in():
            return False, "Codex is not signed in — run: codex login"
        return True, f"Codex · {codex.describe()}"

    if not ollama.is_up(cfg):
        return False, ("Ollama is not running — start it with: "
                       "systemctl start ollama")
    if not ollama.has_model(cfg):
        return False, (f"{models.friendly_name(cfg.model)} is not installed — "
                       f"run: ollama pull {cfg.model}")
    return True, f"Running on this machine · {models.friendly_name(cfg.model)}"


def label(cfg: Config) -> str:
    """Short provider tag for the popup header."""
    if cfg.provider == OPENROUTER:
        return f"OpenRouter · {cfg.openrouter_model}"
    if cfg.provider == OPENAI:
        return f"OpenAI-compatible · {cfg.openai_model}"
    if cfg.provider == CODEX:
        return f"Codex · {cfg.codex_model or 'default'}"
    return models.friendly_name(cfg.model)
