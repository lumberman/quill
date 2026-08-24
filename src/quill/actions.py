"""The edit catalogue: what appears in the menu and how each edit is phrased."""

from __future__ import annotations

from dataclasses import dataclass

# Kept deliberately blunt. Small local models drift into chat mode ("Sure! Here
# is the corrected text:") unless the contract is spelled out and repeated.
SYSTEM_PROMPT = """\
You are a text-editing engine inside a desktop utility. The user selected some \
text in an application and chose an edit to apply to it.

Rules, in priority order:
1. Reply with the edited text and nothing else. No preamble, no explanation, no \
commentary, no sign-off. Never begin with "Here is" or "Sure".
2. Do not wrap your reply in quotation marks or markdown code fences unless the \
original text was itself wrapped that way.
3. Reply in the same language as the input, unless the instruction explicitly \
asks you to translate.
4. Preserve meaning, intent, names, numbers, URLs, code, placeholders and markup. \
Preserve the original line structure.
5. If the text already satisfies the instruction, reply with it unchanged.
"""


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    instruction: str
    # Low temperature for corrective edits, a little room for generative ones.
    temperature: float = 0.2
    prompts_for_input: bool = False


DEFAULT_ACTIONS: list[Action] = [
    Action(
        id="fix",
        label="Fix Spelling & Grammar",
        instruction=(
            "Correct spelling, grammar, punctuation and capitalisation. Make the "
            "smallest number of changes that fixes the errors: do not reword, "
            "restructure, or change the tone, register or vocabulary."
        ),
        temperature=0.0,
    ),
    Action(
        id="rewrite",
        label="Rewrite for Clarity",
        instruction=(
            "Rewrite the text so it reads clearly and naturally. Keep the same "
            "meaning and roughly the same length."
        ),
        temperature=0.3,
    ),
    Action(
        id="shorter",
        label="Make It Shorter",
        instruction=(
            "Make the text shorter and tighter while keeping every essential "
            "point. Cut filler, not information."
        ),
        temperature=0.3,
    ),
    Action(
        id="professional",
        label="Professional Tone",
        instruction=(
            "Rewrite the text in a polished, professional tone suitable for work "
            "communication. Keep it warm and direct rather than stiff or formal."
        ),
        temperature=0.3,
    ),
    Action(
        id="friendly",
        label="Friendly Tone",
        instruction="Rewrite the text in a warm, friendly, conversational tone.",
        temperature=0.4,
    ),
    Action(
        id="simplify",
        label="Plain English",
        instruction=(
            "Rewrite the text in plain, simple language that a non-native English "
            "speaker can follow easily. Prefer short sentences and common words."
        ),
        temperature=0.3,
    ),
    Action(
        id="expand",
        label="Expand",
        instruction=(
            "Expand the text with fuller sentences and useful supporting detail, "
            "keeping the author's voice. Do not invent facts."
        ),
        temperature=0.4,
    ),
    Action(
        id="translate_en",
        label="Translate to English",
        instruction=(
            "Translate the text into natural, idiomatic English. If it is already "
            "English, return it unchanged."
        ),
        temperature=0.2,
    ),
    Action(
        id="custom",
        label="Custom Instruction…",
        instruction="",
        temperature=0.3,
        prompts_for_input=True,
    ),
]


def build_messages(action: Action, text: str, custom: str = "") -> list[dict]:
    instruction = custom.strip() if action.prompts_for_input else action.instruction
    user = f"Instruction: {instruction}\n\nText:\n{text}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
