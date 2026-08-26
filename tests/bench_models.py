"""Measure candidate models on Quill's actual grammar-fix path.

    python3 tests/bench_models.py model:tag [model:tag ...]

Uses build_messages + provider.stream_chat + sanitize, so what it prints is
exactly what Quill would paste over the selection.
"""

import statistics
import sys
import time

from quill import provider, sanitize
from quill.actions import build_messages
from quill.config import Config

CASES = [
    # (input, must-contain fixes, must-not-change)
    ("subttitle translation is not finished yet, i want you to focuse on the assigment.",
     ["Subtitle", "focus", "assignment"], []),
    ("i has recieved you're mesage yesterday.",
     ["received", "your", "message"], []),
    ("we was going to the store, but it dont open untill 9.",
     ["were", "doesn't", "until"], []),
    # Already correct and full of things a sloppy model will "improve".
    ("The API returned a 404 error when GET /v1/models was called.",
     [], ["API", "404", "/v1/models"]),
]

REPEATS = 3


def score(text: str, expect: list[str], keep: list[str]) -> str:
    missing = [w for w in expect if w.lower() not in text.lower()]
    lost = [w for w in keep if w not in text]
    if missing or lost:
        bits = []
        if missing:
            bits.append("missed " + ",".join(missing))
        if lost:
            bits.append("mangled " + ",".join(lost))
        return "FAIL (" + "; ".join(bits) + ")"
    return "ok"


def run(model: str) -> None:
    cfg = Config()
    cfg.model = model
    action = cfg.action("fix")
    print(f"\n=== {model} ===")

    # Warm-up: the first call includes loading weights into VRAM.
    try:
        list(provider.stream_chat(cfg, build_messages(action, "warm up"), 0.0))
    except provider.ProviderError as exc:
        print(f"  unavailable: {exc}")
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
        verdict = score(out, expect, keep)
        passes += verdict == "ok"
        print(f"  {median*1000:7.0f} ms  {verdict}")
        print(f"           in : {text}")
        print(f"           out: {out}")

    print(f"  -> median {statistics.median(all_times)*1000:.0f} ms, "
          f"{passes}/{len(CASES)} cases correct")


if __name__ == "__main__":
    for name in sys.argv[1:]:
        run(name)
