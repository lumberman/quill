"""Minimal streaming Ollama client (stdlib only) plus output sanitising."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

from . import openai_compat
from .config import Config


class OllamaError(RuntimeError):
    pass


# Set once per process: some models reject a "think" field outright, and
# there is no capability flag to check up front.
_THINK_FIELD_REJECTED = False


def _get_json(url: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def is_up(cfg: Config) -> bool:
    return _get_json(cfg.tags_url) is not None


def installed_models(cfg: Config) -> list[str]:
    data = _get_json(cfg.tags_url)
    if not isinstance(data, dict):
        return []
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def loaded_models(cfg: Config) -> list[str]:
    """Models currently resident in VRAM, per /api/ps."""
    data = _get_json(cfg.host.rstrip("/") + "/api/ps")
    if not isinstance(data, dict):
        return []
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def is_loaded(cfg: Config, model: str | None = None) -> bool:
    """Whether the next request will skip loading weights.

    A cold model can take 20s+ while a warm one answers in a fraction of a
    second, which is worth telling the user before they conclude it hung.
    """
    target = model or cfg.model
    names = loaded_models(cfg)
    return any(n == target or n.split(":")[0] == target.split(":")[0]
               for n in names)


def has_model(cfg: Config, model: str | None = None) -> bool:
    """Ollama resolves a bare name to its :latest tag, so compare both ways."""
    target = model or cfg.model
    names = installed_models(cfg)
    if target in names:
        return True
    base = target.split(":")[0]
    return any(n == target or n.split(":")[0] == base for n in names) and ":" not in target


def _stream_response(
    req: urllib.request.Request,
    cfg: Config,
    cancel: threading.Event | None,
) -> Iterator[str]:
    """Yield content deltas from one NDJSON streaming response."""
    deadline = time.monotonic() + cfg.request_timeout
    with urllib.request.urlopen(req, timeout=cfg.request_timeout) as resp:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                return
            # Per-read timeouts do not catch a model that never stops.
            if time.monotonic() > deadline:
                raise OllamaError(
                    f"The model was still streaming after "
                    f"{cfg.request_timeout:.0f}s; giving up."
                )
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise OllamaError(str(chunk["error"]))
            # Deliberately ignores any "thinking" field: reasoning is not part
            # of the answer, and requesting think=false should mean there is
            # none to begin with.
            delta = (chunk.get("message") or {}).get("content", "")
            if delta:
                yield delta
            if chunk.get("done"):
                return


def stream_chat(
    cfg: Config,
    messages: list[dict],
    temperature: float = 0.2,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    """Yield content deltas from /api/chat. Raises OllamaError on failure."""
    global _THINK_FIELD_REJECTED

    payload = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
        "keep_alive": cfg.keep_alive,
        "options": {
            "temperature": temperature,
            "num_ctx": cfg.num_ctx,
            # Ollama defaults num_predict to unlimited.
            "num_predict": openai_compat.output_cap(messages),
        },
    }
    # Reasoning is actively harmful here: a rewrite needs no deliberation, and a
    # thinking model can burn its whole context in the reasoning channel and
    # return empty content (measured with gemma4:12b-it-qat on a one-line
    # spellcheck: 95s, 7962 tokens, done_reason=length, no answer at all; the
    # same call with think=false took 0.2s and 8 tokens).
    if cfg.think is not None and not _THINK_FIELD_REJECTED:
        payload["think"] = cfg.think

    def _request(body: dict) -> urllib.request.Request:
        return urllib.request.Request(
            cfg.chat_url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    try:
        yield from _stream_response(_request(payload), cfg, cancel)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        if "think" in detail.lower() and "think" in payload:
            # This model has no reasoning channel to switch off. Drop the field
            # and stop sending it for the rest of the process.
            _THINK_FIELD_REJECTED = True
            payload.pop("think", None)
            try:
                yield from _stream_response(_request(payload), cfg, cancel)
            except (urllib.error.URLError, OSError, TimeoutError) as retry_exc:
                raise OllamaError(
                    f"Could not reach Ollama at {cfg.host}: {retry_exc}"
                ) from retry_exc
            return
        raise OllamaError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise OllamaError(f"Could not reach Ollama at {cfg.host}: {exc}") from exc
