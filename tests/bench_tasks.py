"""Compare models across every edit Quill offers, not just grammar.

    python3 tests/bench_tasks.py model:tag [model:tag ...]

Non-grammar edits have no single right answer, so the checks are structural:
did it keep the facts, hit the right length, drop the slang, actually translate.
Every task also gets the checks that catch the ways small models fail loudly --
title-casing, chat commentary, and answering in the wrong language.
"""

import re
import statistics
import sys
import time
import unicodedata

from quill import provider, sanitize
from quill.actions import build_messages
from quill.config import Config

TIMEOUT_S = 60.0
REPEATS = 2

# Markers of a model explaining itself instead of just editing.
COMMENTARY = ["i made the following", "here is", "here's the", "changes:",
              "note:", "i have corrected", "let me know", "sure,", "certainly,",
              "revised version", "corrected text:"]

TASKS = [
    dict(id="fix", label="grammar",
         text="subttitle translation is not finished yet, i want you to focuse on the assigment.",
         require=["Subtitle", "I want", "focus", "assignment"]),
    dict(id="fix", label="grammar/code (leave alone)",
         text="The API returned a 404 error when GET /v1/models was called.",
         keep=["API", "404", "GET /v1/models"], length=(0.8, 1.3)),
    dict(id="shorter", label="shorten",
         text=("I wanted to reach out to you today in order to let you know that we are "
               "currently in the process of reviewing the subtitle translation workflow, "
               "and we expect to have an update for you at some point in the near future."),
         require=["subtitle"], length=(0.15, 0.65)),
    dict(id="professional", label="professional tone",
         text="hey can u send me the subtitle files asap? need em today lol",
         forbid=["asap", "u", "lol", "em"], require=["subtitle"]),
    dict(id="friendly", label="friendly tone",
         text="Submit the translation by Friday. Late submissions are not accepted.",
         # A warmer rewrite legitimately gets longer; only runaway padding fails.
         keep=["Friday"], length=(0.6, 3.5)),
    dict(id="simplify", label="plain english",
         text=("Pursuant to the aforementioned localisation directive, all subtitle "
               "artefacts must undergo quality attestation prior to dissemination."),
         require=["subtitle"], length=(0.3, 1.2), first_upper=True,
         # "every subtitle files need" -- agreement errors introduced by the edit.
         forbid_regex=[r"every \w+ files", r"\ball \w+ needs\b"]),
    dict(id="expand", label="expand",
         text="Subtitle quality is poor.",
         # Ratio is useless on a 25-character input, so bound it absolutely.
         require=["ubtitle"], length_chars=(60, 700)),
    dict(id="translate_en", label="translate RU->EN",
         text="Перевод субтитров ещё не закончен, сосредоточьтесь на задании.",
         require_any=["subtitle", "translation"], no_cyrillic=True),
    dict(id="rewrite", label="rewrite for clarity",
         text="the thing is that the subtitles they are not done and so the review it cannot start.",
         require=["subtitle"], length=(0.5, 1.8), first_upper=True),
    # --- harder cases, where a small model is expected to show strain -------
    dict(id="translate_en", label="translate ZH->EN",
         text="字幕翻译还没有完成，请先专注于这个任务，中文字幕我稍后再跟你确认。",
         require_any=["subtitle", "translation"], no_cjk=True),
    dict(id="fix", label="keep markdown + line structure",
         text=("## Subtitle QA\n"
               "- [ ] chinese track: **not** started\n"
               "- [x] english track: revewed on 2026-08-12\n"
               "See `docs/qa.md` and https://example.com/qa for detials."),
         # "chinese"/"english" are proper nouns: fixing them is part of the job.
         require=["reviewed", "details", "Chinese track", "English track"],
         keep=["## Subtitle QA", "- [ ]", "- [x]", "**not**",
               "`docs/qa.md`", "https://example.com/qa", "2026-08-12"],
         lines=4),
    dict(id="fix", label="Russian stays Russian",
         text="Перевод субтитров ещё не закончен, сосредоточтесь на задании.",
         # "сосредоточтесь" is the planted typo; "ё" must survive.
         require=["ещё", "сосредоточьтесь"],
         must_cyrillic=True, length=(0.7, 1.4)),
    dict(id="shorter", label="shorten, keep facts",
         text=("On 12 August 2026 the JESUS Film Project reviewed 4 subtitle tracks "
               "for the Mandarin release, of which 3 passed quality attestation and "
               "1 was returned to the vendor for a second pass before 30 September."),
         keep=["4", "3", "1"], require_any=["Mandarin", "subtitle"],
         length=(0.2, 0.8)),
    dict(id="custom", label="custom: bullet points",
         text="Subtitle translation is unfinished and the English quality review is blocked.",
         instruction="Turn this into exactly two bullet points.",
         bullets=2),
]


def has_cyrillic(text: str) -> bool:
    return any("CYRILLIC" in unicodedata.name(ch, "") for ch in text)


def has_cjk(text: str) -> bool:
    return any("CJK" in unicodedata.name(ch, "") for ch in text)


def stray_capitals(original: str, result: str) -> int:
    def mid(text: str) -> set[str]:
        found = set()
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            for word in sentence.split()[1:]:
                bare = word.strip(".,;:!?\"'()")
                if len(bare) > 2 and bare[0].isupper() and not bare.isupper():
                    found.add(bare.lower())
        return found
    return len(mid(result) - mid(original))


def check(task: dict, out: str) -> list[str]:
    text = task["text"]
    fails = []

    missing = [w for w in task.get("require", []) if w not in out]
    if missing:
        fails.append("missed " + ",".join(missing))

    any_of = task.get("require_any")
    if any_of and not any(w.lower() in out.lower() for w in any_of):
        fails.append("none of " + "/".join(any_of))

    lost = [w for w in task.get("keep", []) if w not in out]
    if lost:
        fails.append("lost " + ",".join(lost))

    present = [w for w in task.get("forbid", [])
               if re.search(rf"\b{re.escape(w.strip())}\b", out, re.IGNORECASE)]
    if present:
        fails.append("kept " + ",".join(w.strip() for w in present))

    span = task.get("length")
    if span:
        ratio = len(out) / max(1, len(text))
        if not span[0] <= ratio <= span[1]:
            fails.append(f"length {ratio:.2f}x (want {span[0]}-{span[1]}x)")

    chars = task.get("length_chars")
    if chars and not chars[0] <= len(out) <= chars[1]:
        fails.append(f"{len(out)} chars (want {chars[0]}-{chars[1]})")

    want_bullets = task.get("bullets")
    if want_bullets:
        found = len([ln for ln in out.splitlines()
                     if re.match(r"\s*([-*•]|\d+[.)])\s+", ln)])
        if found != want_bullets:
            fails.append(f"{found} bullets, wanted {want_bullets}")

    if task.get("no_cyrillic") and has_cyrillic(out):
        fails.append("still Cyrillic")
    if task.get("no_cjk") and has_cjk(out):
        fails.append("still CJK")
    if task.get("must_cyrillic") and not has_cyrillic(out):
        fails.append("answered in the wrong language")

    want_lines = task.get("lines")
    if want_lines:
        got = len([ln for ln in out.splitlines() if ln.strip()])
        if got != want_lines:
            fails.append(f"{got} lines, wanted {want_lines}")

    if stray_capitals(text, out) >= 3:
        fails.append("title-cased")

    lowered = out.lower()
    chatter = [m for m in COMMENTARY if m in lowered]
    if chatter:
        fails.append("commentary: " + chatter[0])

    if task.get("first_upper") and out.strip() and not out.strip()[0].isupper():
        fails.append("sentence not capitalised")

    for pattern in task.get("forbid_regex", []):
        if re.search(pattern, out, re.IGNORECASE):
            fails.append(f"agreement error: /{pattern}/")

    if not out.strip():
        fails.append("empty")

    return fails


def run(model: str) -> tuple[int, int, float]:
    cfg = Config()
    cfg.model = model
    cfg.request_timeout = TIMEOUT_S
    print(f"\n=== {model} ===", flush=True)

    passed = 0
    times = []
    for task in TASKS:
        action = cfg.action(task["id"])
        messages = build_messages(action, task["text"], task.get("instruction", ""))
        out, elapsed = "", []
        for _ in range(REPEATS):
            start = time.perf_counter()
            try:
                chunks = list(provider.stream_chat(cfg, messages, action.temperature))
            except provider.ProviderError as exc:
                print(f"  {task['label']:<26} ERROR {exc}", flush=True)
                chunks = []
            elapsed.append(time.perf_counter() - start)
            out = sanitize.clean_output("".join(chunks), task["text"])
        median = statistics.median(elapsed)
        times.append(median)
        fails = check(task, out)
        passed += not fails
        mark = "ok  " if not fails else "FAIL"
        print(f"  {task['label']:<26} {median*1000:6.0f} ms  {mark} "
              f"{'; '.join(fails)}", flush=True)
        print(f"      {out[:170]}", flush=True)

    total = len(TASKS)
    print(f"  -> {passed}/{total} passed, median {statistics.median(times)*1000:.0f} ms",
          flush=True)
    return passed, total, statistics.median(times)


def unload(model: str) -> None:
    import json as _json
    import urllib.request as _rq
    try:
        _rq.urlopen(_rq.Request(
            "http://127.0.0.1:11434/api/chat",
            data=_json.dumps({"model": model, "messages": [], "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"}), timeout=15).read()
    except Exception:
        pass


if __name__ == "__main__":
    summary = []
    for name in sys.argv[1:]:
        try:
            summary.append((name, *run(name)))
        finally:
            unload(name)
    print("\n" + "=" * 60)
    for name, passed, total, median in summary:
        print(f"{name:<22} {passed}/{total}   median {median*1000:.0f} ms")
