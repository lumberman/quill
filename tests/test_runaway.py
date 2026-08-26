"""Regression: a model that never stops must not hang Quill.

    python3 tests/test_runaway.py

A 135M model did this for real and pinned the machine. urllib's timeout is
per-read, so it never fired; only a wall-clock deadline catches it.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from quill import ollama
from quill.actions import build_messages
from quill.config import Config


class Runaway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:  # never sets done
                body = json.dumps({"message": {"content": "blah "},
                                   "done": False}) + "\n"
                self.wfile.write(f"{len(body):x}\r\n{body}\r\n".encode())
                self.wfile.flush()
                time.sleep(0.01)
        except Exception:
            pass


def main() -> int:
    server = HTTPServer(("127.0.0.1", 8801), Runaway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.4)

    cfg = Config()
    cfg.host = "http://127.0.0.1:8801"
    cfg.request_timeout = 4.0
    action = next(a for a in cfg.actions if a.id == "fix")
    messages = build_messages(action, "fix this")

    start = time.time()
    try:
        for _ in ollama.stream_chat(cfg, messages, 0.0):
            if time.time() - start > 20:
                print("FAIL: guard never fired")
                return 1
        print("FAIL: stream ended without raising")
        return 1
    except ollama.OllamaError as exc:
        elapsed = time.time() - start
        ok = 3.5 < elapsed < 8
        print(f"{'PASS' if ok else 'FAIL'}: aborted after {elapsed:.1f}s — {exc}")
        return 0 if ok else 1
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
