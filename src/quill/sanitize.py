"""Cleaning up what models actually return, as opposed to what they were told to.

Provider-neutral: every backend has some model that leaks reasoning blocks,
chat preambles or code fences no matter how the prompt is worded.
"""

from __future__ import annotations

import re


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
