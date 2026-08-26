"""Stand-in OpenRouter for testing the PKCE flow and SSE parsing offline.

    python3 tests/mock_openrouter.py [port]

Deliberately strict about PKCE: /api/v1/auth/keys recomputes the challenge from
the verifier and rejects a mismatch, so a broken implementation fails here
rather than silently "working" against a permissive stub.
"""

import base64
import hashlib
import json
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

ISSUED_CODE = "mock-auth-code"
ISSUED_KEY = "sk-or-v1-mockkey-0123456789abcdef"
CHALLENGES: dict[str, str] = {}

MODELS = {
    "data": [
        {"id": "z-ai/glm-5.2:free", "name": "GLM 5.2 (free)",
         "context_length": 256000,
         "pricing": {"prompt": "0", "completion": "0"},
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        {"id": "google/gemma-4-31b-it:free", "name": "Gemma 4 31B (free)",
         "context_length": 262144,
         "pricing": {"prompt": "0", "completion": "0"},
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        {"id": "acme/music:free", "name": "Music (free, not text)",
         "context_length": 8192,
         "pricing": {"prompt": "0", "completion": "0"},
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text", "audio"]}},
        {"id": "acme/paid", "name": "Paid model",
         "context_length": 8192,
         "pricing": {"prompt": "0.5", "completion": "1.5"},
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    ]
}

REPLY = 'Here is the corrected text:\n"I have received your message."'


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/auth":
            callback = (params.get("callback_url") or [""])[0]
            challenge = (params.get("code_challenge") or [""])[0]
            method = (params.get("code_challenge_method") or [""])[0]
            if not callback or not challenge or method != "S256":
                self._send(400, b'{"error":"bad auth request"}')
                return
            CHALLENGES[ISSUED_CODE] = challenge
            sep = "&" if "?" in callback else "?"
            self.send_response(302)
            self.send_header("Location", f"{callback}{sep}code={ISSUED_CODE}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if parsed.path == "/api/v1/models":
            self._send(200, json.dumps(MODELS).encode())
            return

        self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        if parsed.path == "/api/v1/auth/keys":
            body = json.loads(raw or b"{}")
            code = body.get("code")
            verifier = body.get("code_verifier") or ""
            expected = CHALLENGES.get(code)
            if expected is None:
                self._send(400, b'{"error":"unknown or reused code"}')
                return
            actual = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
            if actual != expected:
                self._send(400, b'{"error":"PKCE verifier does not match challenge"}')
                return
            del CHALLENGES[code]  # single use, as the real one is
            self._send(200, json.dumps({"key": ISSUED_KEY}).encode())
            return

        if parsed.path == "/api/v1/chat/completions":
            if self.headers.get("Authorization") != f"Bearer {ISSUED_KEY}":
                self._send(401, b'{"error":"bad key"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # Include the keep-alive comment the real API emits.
            self.wfile.write(b": OPENROUTER PROCESSING\n\n")
            self.wfile.flush()
            for word in REPLY.split(" "):
                chunk = {"choices": [{"delta": {"content": word + " "}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.02)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        self._send(404, b'{"error":"not found"}')


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    print(f"mock openrouter on :{port}", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
