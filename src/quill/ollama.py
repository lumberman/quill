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

    if not out:
        # Never re-attach the original's whitespace to an empty result: the
        # truthy "\n" that produces silently defeats caller fallbacks.
        return ""

    # Give back the caller's own leading/trailing whitespace so pasting over a
    # selection does not silently eat an indent or a trailing space.
    lead = original[: len(original) - len(original.lstrip())]
    trail = original[len(original.rstrip()):]
    return f"{lead}{out}{trail}" if original else out
