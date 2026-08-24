"""Minimal streaming Ollama client (stdlib only) plus output sanitising."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

from .config import Config


class OllamaError(RuntimeError):
    pass


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


def has_model(cfg: Config, model: str | None = None) -> bool:
    """Ollama resolves a bare name to its :latest tag, so compare both ways."""
    target = model or cfg.model
    names = installed_models(cfg)
    if target in names:
        return True
    base = target.split(":")[0]
    return any(n == target or n.split(":")[0] == base for n in names) and ":" not in target


def stream_chat(
    cfg: Config,
    messages: list[dict],
    temperature: float = 0.2,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    """Yield content deltas from /api/chat. Raises OllamaError on failure."""
    payload = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
        "keep_alive": cfg.keep_alive,
        "options": {
            "temperature": temperature,
            "num_ctx": cfg.num_ctx,
        },
    }
    req = urllib.request.Request(
        cfg.chat_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=cfg.request_timeout) as resp:
            for raw in resp:
                if cancel is not None and cancel.is_set():
                    return
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise OllamaError(str(chunk["error"]))
                delta = (chunk.get("message") or {}).get("content", "")
                if delta:
                    yield delta
                if chunk.get("done"):
                    return
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise OllamaError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise OllamaError(f"Could not reach Ollama at {cfg.host}: {exc}") from exc


# --- output sanitising ------------------------------------------------------
# Small models leak reasoning blocks, code fences and chat preambles even when
# told not to. Stripping them here keeps every model swap-in-able.

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK_RE = re.compile(r"^.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)\n?```$", re.DOTALL)
_PREAMBLE_RE = re.compile(
    r"^(sure|certainly|okay|ok|of course|here('s| is| are)|the (corrected|revised|rewritten))\b[^\n]*:\s*\n",
    re.IGNORECASE,
)


def _strip_paired(text: str, pairs: tuple[tuple[str, str], ...]) -> str:
    for opener, closer in pairs:
        if len(text) > len(opener) + len(closer) and text.startswith(opener) and text.endswith(closer):
            inner = text[len(opener):-len(closer)]
            if opener not in inner and closer not in inner:
                return inner
    return text


def clean_output(text: str, original: str = "") -> str:
    out = text
    if "</think>" in out.lower():
        out = _THINK_RE.sub("", out)
        if "</think>" in out.lower():
            out = _UNCLOSED_THINK_RE.sub("", out)
    out = out.strip()

    if "\n" in out:
        out = _PREAMBLE_RE.sub("", out, count=1).strip()

    # Only unwrap decoration the input did not already have.
    if not original.strip().startswith("```"):
        fenced = _FENCE_RE.match(out)
        if fenced:
            out = fenced.group(1).strip()

    stripped_original = original.strip()
    quote_pairs = (('"', '"'), ("'", "'"), ("“", "”"), ("«", "»"))
    if not any(stripped_original.startswith(o) for o, _ in quote_pairs):
        out = _strip_paired(out, quote_pairs).strip()

    # Give back the caller's own leading/trailing whitespace so pasting over a
    # selection does not silently eat an indent or a trailing space.
    lead = original[: len(original) - len(original.lstrip())]
    trail = original[len(original.rstrip()):]
    return f"{lead}{out}{trail}" if original else out
