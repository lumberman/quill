"""Stand-in Ollama for developing the UI without a model on disk.

    python3 tests/mock_ollama.py [port]

Serves /api/tags and a slow-streaming /api/chat so streaming, cancellation and
the sanitiser can all be exercised offline.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

MODELS = ["gemma4:12b-it-qat", "granite4.1:8b"]

# Deliberately dirty: a reasoning block, a chat preamble and wrapping quotes,
# so clean_output has something to strip.
REPLY = (
    "<think>The user wants the grammar fixed. I should keep the meaning.</think>"
    'Here is the corrected text:\n"I\'m reviewing the subtitle translation '
    "workflow, but Manager shows a different status after I sign in. Do I need "
    "Manager access to help me get the rest? I want you to focus on the "
    "assignment. I will be back to you about subtitles in Chinese later, once "
    'quality in English is resolved."'
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/api/tags"):
            body = json.dumps(
                {"models": [{"name": m, "size": 1} for m in MODELS]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if not self.path.startswith("/api/chat"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()

        words = REPLY.split(" ")
        for i, word in enumerate(words):
            chunk = {
                "message": {"role": "assistant",
                            "content": word + (" " if i < len(words) - 1 else "")},
                "done": False,
            }
            self.wfile.write((json.dumps(chunk) + "\n").encode())
            self.wfile.flush()
            time.sleep(0.04)
        self.wfile.write((json.dumps({"message": {"content": ""}, "done": True}) + "\n").encode())
        self.wfile.flush()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11434
    print(f"mock ollama on :{port}", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
