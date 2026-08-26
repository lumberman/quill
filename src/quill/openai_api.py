"""Any OpenAI-compatible endpoint: LM Studio, llama.cpp, vLLM, LocalAI, OpenAI.

One provider covers all of them because they share the /chat/completions shape.
The only thing that changes is the base URL and whether a key is needed.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from . import credentials, openai_compat

ACCOUNT = "openai"

# Defaults for the servers people actually run locally.
PRESETS: dict[str, str] = {
    "LM Studio": "http://127.0.0.1:1234/v1",
    "Ollama (OpenAI-compatible)": "http://127.0.0.1:11434/v1",
    "llama.cpp server": "http://127.0.0.1:8080/v1",
    "vLLM": "http://127.0.0.1:8000/v1",
    "LocalAI": "http://127.0.0.1:8080/v1",
    "OpenAI": "https://api.openai.com/v1",
}


class OpenAIError(RuntimeError):
    pass


def key() -> str | None:
    return credentials.get(ACCOUNT)


def store_key(value: str) -> str:
    return credentials.set(ACCOUNT, value)


def forget_key() -> None:
    credentials.clear(ACCOUNT)


def key_source() -> str | None:
    return credentials.source(ACCOUNT)


def needs_key(base_url: str) -> bool:
    """Local servers usually accept anything; hosted ones do not."""
    lowered = base_url.lower()
    return not any(host in lowered for host in
                   ("127.0.0.1", "localhost", "0.0.0.0", "::1"))


def models(cfg) -> list[str]:
    try:
        return openai_compat.list_models(
            cfg.openai_base_url, key(), service="The OpenAI-compatible server")
    except openai_compat.CompatError as exc:
        raise OpenAIError(str(exc)) from exc


def is_up(cfg) -> bool:
    try:
        models(cfg)
        return True
    except OpenAIError:
        return False


def stream_chat(cfg, messages: list[dict], temperature: float = 0.2,
                cancel: threading.Event | None = None) -> Iterator[str]:
    api_key = key()
    if not api_key and needs_key(cfg.openai_base_url):
        raise OpenAIError(
            f"{cfg.openai_base_url} needs an API key. Add one in settings."
        )
    # No reasoning switch here on purpose. There is no field every
    # OpenAI-compatible server agrees on, and strict ones reject unknown
    # keys outright with a 400. Reasoning models are therefore slower
    # through this provider than through their native one.
    payload = {
        "model": cfg.openai_model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": openai_compat.output_cap(messages),
    }
    request = openai_compat.build_request(
        cfg.openai_base_url, "/chat/completions", payload, api_key)
    try:
        yield from openai_compat.stream_completion(
            request, cfg.request_timeout, cancel,
            service="The OpenAI-compatible server")
    except openai_compat.CompatError as exc:
        raise OpenAIError(str(exc)) from exc
