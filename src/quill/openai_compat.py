"""Shared client for anything speaking the OpenAI /chat/completions API.

OpenRouter, LM Studio, llama.cpp's server, vLLM, LocalAI and api.openai.com all
expose the same shape, so the streaming and listing logic lives here once.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator


class CompatError(RuntimeError):
    pass


def build_request(base_url: str, path: str, payload: dict | None,
                  api_key: str | None = None,
                  extra_headers: dict | None = None) -> urllib.request.Request:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)
    url = base_url.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    return urllib.request.Request(
        url, data=data, headers=headers,
        method="POST" if data is not None else "GET",
    )


def describe_http_error(exc: urllib.error.HTTPError, service: str) -> str:
    detail = exc.read().decode("utf-8", errors="replace")[:400]
    if exc.code in (401, 403):
        return f"{service} rejected the API key."
    if exc.code == 429:
        return f"{service} rate limit reached — wait, or switch provider."
    if exc.code == 404:
        return (f"{service} has no such model or endpoint (HTTP 404). "
                f"Check the base URL and model name.")
    return f"{service} returned HTTP {exc.code}: {detail}"


def stream_completion(request: urllib.request.Request, timeout: float,
                      cancel: threading.Event | None,
                      service: str = "The server") -> Iterator[str]:
    """Yield content deltas from a streaming chat completion."""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            for raw in resp:
                if cancel is not None and cancel.is_set():
                    return
                line = raw.decode("utf-8", errors="replace").strip()
                # Comment frames are keep-alives (OpenRouter sends
                # ": OPENROUTER PROCESSING"); blank lines separate SSE events.
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise CompatError(str(chunk["error"]))
                for choice in chunk.get("choices") or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        yield delta
    except urllib.error.HTTPError as exc:
        raise CompatError(describe_http_error(exc, service)) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise CompatError(f"Could not reach {service}: {exc}") from exc


def list_models(base_url: str, api_key: str | None = None,
                timeout: float = 10.0, service: str = "The server") -> list[str]:
    request = build_request(base_url, "/models", None, api_key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CompatError(describe_http_error(exc, service)) from exc
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise CompatError(f"Could not reach {service}: {exc}") from exc

    out = []
    for model in payload.get("data", payload if isinstance(payload, list) else []):
        name = model.get("id") if isinstance(model, dict) else str(model)
        if name:
            out.append(name)
    return sorted(out)
