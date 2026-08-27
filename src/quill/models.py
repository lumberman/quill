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
    friendly: str
    speed: str
    memory: str
    accuracy: str
    detail: str

    @property
    def choice_label(self) -> str:
        """What the dropdown shows. Plain language first, model name after.

        "gemma4:12b-it-qat" tells someone who has never run a model nothing at
        all, and four of the entries are equally bad choices for different
        reasons, so the label has to say both which one it is and whether to
        pick it.
        """
        return f"{self.headline} — {self.friendly}"

    @property
    def short(self) -> str:
        """One line under the dropdown, in units people already have."""
        return f"{self.speed} · {self.memory} · {self.accuracy}"

    @property
    def summary(self) -> str:
        return f"{self.short} · {self.detail}"


NOTES: dict[str, ModelNote] = {
    "gemma4:12b-it-qat": ModelNote(
        model="gemma4:12b-it-qat",
        tier=QUALITY,
        headline="Best quality",
        friendly="Gemma 4 (12B)",
        speed="Feels instant (0.3s an edit)",
        memory="Needs 7.2 GB of graphics memory",
        accuracy="Got 13 of 14 test edits right",
        detail=(
            "The most accurate choice, and the one to pick if you write in "
            "Chinese or Russian. It gets names, capitalisation and accents "
            "right where the smaller model does not."
        ),
    ),
    "granite4.1:3b": ModelNote(
        model="granite4.1:3b",
        tier=FAST,
        headline="Faster, less accurate",
        friendly="Granite 4.1 (3B)",
        speed="Feels instant (0.1s an edit)",
        memory="Needs only 2.1 GB of graphics memory",
        accuracy="Got 10 of 14 test edits right",
        detail=(
            "A third of the size, so it leaves your graphics card free for "
            "other things. In exchange it sometimes forgets to capitalise a "
            "sentence, misses names, and is noticeably weaker outside English. "
            "Worth it on a laptop; not if accuracy matters more than memory."
        ),
    ),
}

_TOO_SMALL = (
    "Too small to fix grammar reliably. In testing it corrected the spelling "
    "and left the grammar alone, which is worse than useless in an editor you "
    "cannot see working. Only worth trying if nothing larger will run."
)

for _model, _friendly, _speed, _memory in (
    ("llama3.2:1b", "Llama 3.2 (1B)", "0.04s an edit", "1.3 GB"),
    ("qwen3:0.6b", "Qwen 3 (0.6B)", "0.03s an edit", "522 MB"),
    ("gemma3:270m-it-qat", "Gemma 3 (270M)", "0.03s an edit", "241 MB"),
    ("smollm2:135m", "SmolLM 2 (135M)", "0.02s an edit", "270 MB"),
):
    NOTES[_model] = ModelNote(
        model=_model, tier=UNSUITED, headline="Not recommended",
        friendly=_friendly, speed=f"Very fast ({_speed})",
        memory=f"Needs {_memory}",
        accuracy="Got only 1 of 4 grammar tests right",
        detail=_TOO_SMALL,
    )

# Offered first in the dropdown, in this order.
RECOMMENDED = ("gemma4:12b-it-qat", "granite4.1:3b")

COMPARISON = (
    "Both recommended models feel instant. The larger one makes fewer "
    "mistakes; the smaller one leaves more graphics memory free."
)


def note_for(model: str) -> ModelNote | None:
    return NOTES.get(model)


def friendly_name(model: str) -> str:
    """A name someone can say out loud, falling back to the raw tag."""
    note = NOTES.get(model)
    return note.friendly if note else model


def label_for(model: str) -> str:
    note = NOTES.get(model)
    return note.choice_label if note else model


def describe(model: str) -> str:
    """Short enough for a row subtitle without crowding out the value."""
    note = NOTES.get(model)
    if note:
        return note.short
    return "Quill has not tested this one, so there is nothing to promise"


def detail(model: str) -> str:
    """The nuance, with the technical name kept for anyone who wants it."""
    note = NOTES.get(model)
    if not note:
        return f"{model} — not one of the models Quill has measured."
    return f"{note.detail}  ({note.model})"


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
