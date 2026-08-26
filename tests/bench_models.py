"""Measure candidate models on Quill's actual grammar-fix path.

    python3 tests/bench_models.py model:tag [model:tag ...]

Uses build_messages + provider.stream_chat + sanitize, so what it prints is
exactly what Quill would paste over the selection.
"""

import re
import statistics
import sys
import time

from quill import provider, sanitize
from quill.actions import build_messages
from quill.config import Config

# Expectations are case-SENSITIVE: capitalisation is part of grammar, and a
# case-insensitive check silently passes "i has received" as a fix.
CASES = [
    # (input, must-contain fixes, must-not-change)
    ("subttitle translation is not finished yet, i want you to focuse on the assigment.",
     ["Subtitle", "I want", "focus", "assignment"], []),
    ("i has recieved you're mesage yesterday.",
     ["I have received", "your message"], []),
    ("we was going to the store, but it dont open untill 9.",
     ["We were", "doesn't", "until"], []),
    # Already correct and full of things a sloppy model will "improve".
    ("The API returned a 404 error when GET /v1/models was called.",
     [], ["API", "404", "/v1/models"]),
]

REPEATS = 2
# Short on purpose: a model that will not stop should abort fast, not slowly.
TIMEOUT_S = 30.0


def stray_capitals(original: str, result: str) -> int:
    """Words the model capitalised that the input had in lower case.

    Small models like to Title Case Everything, which is a silent corruption:
    every required word is still present, so a substring check passes it.
    Sentence-initial words are excluded.
    """
    def mid_sentence_caps(text: str) -> set[str]:
        found = set()
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            for word in sentence.split()[1:]:
                bare = word.strip(".,;:!?\"'()")
                if len(bare) > 2 and bare[0].isupper() and not bare.isupper():
                    found.add(bare.lower())
        return found

    return len(mid_sentence_caps(result) - mid_sentence_caps(original))


def score(original: str, text: str, expect: list[str], keep: list[str]) -> str:
    bits = []
    missing = [w for w in expect if w not in text]
    if missing:
        bits.append("missed " + ",".join(missing))
    lost = [w for w in keep if w not in text]
    if lost:
        bits.append("mangled " + ",".join(lost))
    strays = stray_capitals(original, text)
    if strays >= 3:
        bits.append(f"title-cased {strays} words")
    # A rewrite that balloons is not a spelling fix.
    if len(text) > max(80, len(original) * 2):
        bits.append(f"{len(text)}ch from {len(original)}ch")
    return "FAIL (" + "; ".join(bits) + ")" if bits else "ok"


def unload(model: str) -> None:
    """Free the VRAM before the next candidate loads, so they never stack."""
    import json as _json
    import urllib.request as _rq
    try:
        body = _json.dumps({"model": model, "messages": [], "keep_alive": 0})
        _rq.urlopen(_rq.Request(
            "http://127.0.0.1:11434/api/chat", data=body.encode(),
            headers={"Content-Type": "application/json"}), timeout=15).read()
    except Exception:
        pass


def run(model: str) -> None:
    cfg = Config()
    cfg.model = model
    cfg.request_timeout = TIMEOUT_S
    action = cfg.action("fix")
    print(f"\n=== {model} ===", flush=True)

    # Warm-up: the first call includes loading weights into VRAM.
    try:
        list(provider.stream_chat(cfg, build_messages(action, "warm up"), 0.0))
    except provider.ProviderError as exc:
        print(f"  unusable: {exc}", flush=True)
        unload(model)
        return

    all_times = []
    passes = 0
    for text, expect, keep in CASES:
        messages = build_messages(action, text)
        times = []
        out = ""
        for _ in range(REPEATS):
            start = time.perf_counter()
            chunks = list(provider.stream_chat(cfg, messages, action.temperature))
            times.append(time.perf_counter() - start)
            out = sanitize.clean_output("".join(chunks), text)
        median = statistics.median(times)
        all_times.append(median)
        verdict = score(text, out, expect, keep)
        passes += verdict == "ok"
        print(f"  {median*1000:7.0f} ms  {verdict}", flush=True)
        print(f"           in : {text}", flush=True)
        print(f"           out: {out[:150]}", flush=True)

    print(f"  -> median {statistics.median(all_times)*1000:.0f} ms, "
          f"{passes}/{len(CASES)} cases correct", flush=True)


if __name__ == "__main__":
    for name in sys.argv[1:]:
        try:
            run(name)
        except provider.ProviderError as exc:
            print(f"  aborted: {exc}", flush=True)
        finally:
            unload(name)
