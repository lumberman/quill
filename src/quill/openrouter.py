"""OpenRouter backend: model listing, streaming chat, and browser sign-in.

Sign-in is OAuth 2.0 with PKCE, which is the flow meant for apps that cannot
keep a client secret. Quill opens the browser, OpenRouter redirects back to a
loopback server we started, and the one-time code is exchanged for a key that
belongs to the user's own account.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import credentials, openai_compat

ACCOUNT = "openrouter"
# Overridable so the flow can be exercised against a local stand-in, and so a
# self-hosted or proxied endpoint works without a code change.
API_BASE = os.environ.get("QUILL_OPENROUTER_API_BASE",
                          "https://openrouter.ai/api/v1").rstrip("/")
AUTH_URL = os.environ.get("QUILL_OPENROUTER_AUTH_URL",
                          "https://openrouter.ai/auth")
KEY_LABEL = "Quill"

# Sent purely for attribution on OpenRouter's dashboards.
_HEADERS = {
    "Content-Type": "application/json",
    "X-Title": "Quill",
    "HTTP-Referer": "https://github.com/lumberman/quill",
}


class OpenRouterError(RuntimeError):
    pass


# --- keys -------------------------------------------------------------------

def key() -> str | None:
    return credentials.get(ACCOUNT)


def has_key() -> bool:
    return bool(key())


def store_key(value: str) -> str:
    return credentials.set(ACCOUNT, value)


def forget_key() -> None:
    credentials.clear(ACCOUNT)


def key_source() -> str | None:
    return credentials.source(ACCOUNT)


# --- models -----------------------------------------------------------------

def _is_free(model: dict) -> bool:
    pricing = model.get("pricing") or {}
    def zero(field: str) -> bool:
        try:
            return float(pricing.get(field, "1")) == 0.0
        except (TypeError, ValueError):
            return False
    # Both directions must be free; some models are free to prompt but charge
    # for completion.
    return zero("prompt") and zero("completion")


def models(free_only: bool = True, timeout: float = 15.0) -> list[dict]:
    """Public endpoint — works before the user has signed in."""
    req = urllib.request.Request(f"{API_BASE}/models", headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise OpenRouterError(f"Could not reach OpenRouter: {exc}") from exc

    out = []
    for model in payload.get("data", []):
        if free_only and not _is_free(model):
            continue
        # The free tier includes image and music models. Require text-only
        # output: the Lyria music models list ["text", "audio"] and would
        # otherwise look like valid choices for a rewrite.
        arch = model.get("architecture") or {}
        if set(arch.get("output_modalities") or ["text"]) != {"text"}:
            continue
        if "text" not in (arch.get("input_modalities") or ["text"]):
            continue
        out.append({
            "id": model.get("id", ""),
            "name": model.get("name") or model.get("id", ""),
            "context_length": model.get("context_length") or 0,
            "free": _is_free(model),
        })
    out.sort(key=lambda m: m["id"])
    return [m for m in out if m["id"]]


def is_up(timeout: float = 8.0) -> bool:
    try:
        models(free_only=True, timeout=timeout)
        return True
    except OpenRouterError:
        return False


# --- chat -------------------------------------------------------------------

def stream_chat(cfg, messages: list[dict], temperature: float = 0.2,
                cancel: threading.Event | None = None) -> Iterator[str]:
    api_key = key()
    if not api_key:
        raise OpenRouterError(
            "Not connected to OpenRouter. Run `quill login`, or add a key in settings."
        )

    payload = {
        "model": cfg.openrouter_model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": openai_compat.output_cap(messages),
        # Same rationale as Ollama's think=false: an edit is a transformation,
        # and reasoning tokens are latency we pay for and then discard.
        "reasoning": {"exclude": True},
    }
    request = openai_compat.build_request(
        API_BASE, "/chat/completions", payload, api_key, _HEADERS)
    try:
        yield from openai_compat.stream_completion(
            request, cfg.request_timeout, cancel, service="OpenRouter")
    except openai_compat.CompatError as exc:
        message = str(exc)
        if "rejected the API key" in message:
            message += " Sign in again from settings."
        raise OpenRouterError(message) from exc


# --- sign-in ----------------------------------------------------------------

_SUCCESS_HTML = """<!doctype html><meta charset="utf-8"><title>Quill connected</title>
<style>body{font-family:system-ui,sans-serif;background:#16161a;color:#e8e8ea;
display:grid;place-items:center;height:100vh;margin:0}
.card{text-align:center;max-width:26rem;padding:2rem}
h1{font-size:1.3rem;margin:0 0 .5rem}p{opacity:.7;line-height:1.5}</style>
<div class=card><h1>Quill is connected</h1>
<p>Your OpenRouter key has been saved. You can close this tab and go back to Quill.</p></div>"""

_FAILURE_HTML = """<!doctype html><meta charset="utf-8"><title>Quill sign-in failed</title>
<style>body{font-family:system-ui,sans-serif;background:#16161a;color:#e8e8ea;
display:grid;place-items:center;height:100vh;margin:0}
.card{text-align:center;max-width:26rem;padding:2rem}
h1{font-size:1.3rem;margin:0 0 .5rem}p{opacity:.7;line-height:1.5}</style>
<div class=card><h1>Sign-in did not complete</h1>
<p>Nothing was saved. Go back to Quill and try again.</p></div>"""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict = {}

    def log_message(self, *args):  # keep the terminal clean
        pass

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        code = (params.get("code") or [""])[0]
        error = (params.get("error") or [""])[0]

        body = (_SUCCESS_HTML if code else _FAILURE_HTML).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

        type(self).result["code"] = code
        type(self).result["error"] = error
        type(self).result["done"] = True


def _exchange(code: str, verifier: str, timeout: float = 30.0) -> str:
    payload = {
        "code": code,
        "code_verifier": verifier,
        "code_challenge_method": "S256",
    }
    req = urllib.request.Request(
        f"{API_BASE}/auth/keys",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise OpenRouterError(f"Key exchange failed (HTTP {exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise OpenRouterError(f"Key exchange failed: {exc}") from exc

    api_key = data.get("key")
    if not api_key:
        raise OpenRouterError("OpenRouter did not return a key")
    return str(api_key)


def _open_browser(url: str) -> None:
    opener = shutil.which("xdg-open")
    if opener:
        try:
            subprocess.Popen([opener, url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    webbrowser.open(url)


def login(timeout_s: float = 300.0, open_browser: bool = True,
          on_url=None) -> str:
    """Run the PKCE flow and store the resulting key. Returns the key."""
    verifier = _b64url(os.urandom(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())

    handler = type("Handler", (_CallbackHandler,), {"result": {}})
    # Port 0 lets the OS pick; the callback URL is built from what it gave us.
    server = HTTPServer(("127.0.0.1", 0), handler)
    server.timeout = 1.0
    port = server.server_address[1]
    callback = f"http://localhost:{port}/callback"

    url = f"{AUTH_URL}?" + urllib.parse.urlencode({
        "callback_url": callback,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "key_label": KEY_LABEL,
    })

    if on_url:
        on_url(url)
    if open_browser:
        _open_browser(url)

    deadline = threading.Event()
    timer = threading.Timer(timeout_s, deadline.set)
    timer.daemon = True
    timer.start()
    try:
        while not handler.result.get("done"):
            if deadline.is_set():
                raise OpenRouterError(
                    "Timed out waiting for the browser sign-in to finish."
                )
            server.handle_request()
    finally:
        timer.cancel()
        server.server_close()

    if handler.result.get("error"):
        raise OpenRouterError(f"OpenRouter refused sign-in: {handler.result['error']}")
    code = handler.result.get("code")
    if not code:
        raise OpenRouterError("No authorization code came back from OpenRouter.")

    api_key = _exchange(code, verifier)
    store_key(api_key)
    return api_key
