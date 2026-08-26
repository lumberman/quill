"""What Quill knows about specific local models.

Every number here was measured on this project's own benchmark
(`tests/bench_tasks.py`, 14 tasks covering all nine edits plus Chinese and
Russian translation, markdown preservation and fact retention). Latency is the
median of a warm model; the first edit after a pull is slower while weights load.

Kept as data rather than prose in the UI so the settings window and the README
cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

QUALITY = "quality"
FAST = "fast"
UNSUITED = "unsuited"


@dataclass(frozen=True)
class ModelNote:
    model: str
    tier: str
    headline: str
    size: str
    latency: str
    score: str
    detail: str

    @property
    def choice_label(self) -> str:
        # The dropdown shows the selected entry on the right of the row, so it
        # has to stay short or it is ellipsized to nothing useful.
        return self.model

    @property
    def short(self) -> str:
        """One line, for the row subtitle."""
        return f"{self.headline} · {self.size} · ~{self.latency} · scores {self.score}"

    @property
    def summary(self) -> str:
        return f"{self.size} · ~{self.latency} per edit · {self.score} · {self.detail}"


NOTES: dict[str, ModelNote] = {
    "gemma4:12b-it-qat": ModelNote(
        model="gemma4:12b-it-qat",
        tier=QUALITY,
        headline="Best quality",
        size="7.2 GB",
        latency="315 ms",
        score="13/14",
        detail=(
            "Gets proper nouns, subject-verb agreement and non-English "
            "orthography right. The one to use if you edit Chinese or Russian."
        ),
    ),
    "granite4.1:3b": ModelNote(
        model="granite4.1:3b",
        tier=FAST,
        headline="Fastest usable",
        size="2.1 GB",
        latency="113 ms",
        score="10/14",
        detail=(
            "Nearly 3x faster and a third of the size. Sometimes leaves a "
            "sentence uncapitalised, misses proper nouns, and is weaker on "
            "Russian and Chinese."
        ),
    ),
    # Kept so that picking one does not look like a neutral choice.
    "llama3.2:1b": ModelNote(
        model="llama3.2:1b", tier=UNSUITED, headline="Too small",
        size="1.3 GB", latency="40 ms", score="1/4",
        detail="Fixes spelling but leaves grammar alone. Not recommended.",
    ),
    "qwen3:0.6b": ModelNote(
        model="qwen3:0.6b", tier=UNSUITED, headline="Too small",
        size="522 MB", latency="30 ms", score="1/4",
        detail="Fixes spelling but leaves grammar alone. Not recommended.",
    ),
    "gemma3:270m-it-qat": ModelNote(
        model="gemma3:270m-it-qat", tier=UNSUITED, headline="Too small",
        size="241 MB", latency="33 ms", score="1/4",
        detail="Leaves homophones and agreement errors in place. Not recommended.",
    ),
    "smollm2:135m": ModelNote(
        model="smollm2:135m", tier=UNSUITED, headline="Too small",
        size="270 MB", latency="24 ms", score="1/4",
        detail=(
            "Appends an explanation of its own changes, which would be pasted "
            "into your text. Not recommended."
        ),
    ),
}

# Offered first in the dropdown, in this order.
RECOMMENDED = ("gemma4:12b-it-qat", "granite4.1:3b")

COMPARISON = (
    "gemma4:12b — 7.2 GB, ~315 ms, 13/14.   "
    "granite4.1:3b — 2.1 GB, ~113 ms, 10/14.   "
    "Both are fast enough to feel instant; the larger one makes fewer mistakes."
)


def note_for(model: str) -> ModelNote | None:
    return NOTES.get(model)


def label_for(model: str) -> str:
    note = NOTES.get(model)
    return note.choice_label if note else model


def describe(model: str) -> str:
    """Short enough for a row subtitle without crowding out the value."""
    note = NOTES.get(model)
    if note:
        return note.short
    return "Not measured by Quill — quality unknown"


def detail(model: str) -> str:
    note = NOTES.get(model)
    return note.detail if note else ""


def ordered(installed: list[str]) -> list[str]:
    """Recommended models first (when installed), then everything else."""
    present = [m for m in RECOMMENDED if m in installed]
    rest = sorted(m for m in installed if m not in present)
    return present + rest


def missing_recommendation(installed: list[str]) -> str | None:
    """A recommended model worth suggesting, if it is not pulled yet."""
    for model in RECOMMENDED:
        if model not in installed:
            return model
    return None
