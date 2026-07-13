"""Editing modes and prompt construction."""

PROMPTS = {
    "Grammar": "Fix grammar issues without altering the meaning.",
    "Proofread": (
        "Proofread the text comprehensively, correcting errors and improving "
        "readability."
    ),
    "Natural": (
        "Refine awkward phrasing to make the text feel natural while preserving "
        "the original meaning."
    ),
    "Streamline": (
        "Remove unnecessary elements, clarify the message, and ensure coherence "
        "and ease of understanding."
    ),
    "Awkward": (
        "Fix only awkward or poorly written sentences without making other changes."
    ),
    "Rewrite": "Rewrite the text to improve clarity, flow, and overall readability.",
    "Concise": (
        "Make the text more concise by removing redundancy and unnecessary content."
    ),
    "Polish": (
        "Refine awkward words or phrases to give the text a polished and "
        "professional tone."
    ),
    "Improve": (
        "Enhance the text by proofreading and improving its clarity, flow, and "
        "coherence."
    ),
}

EDITING_MODES = list(PROMPTS) + ["Translate", "Custom"]


def build_instruction(mode, extra_value=None):
    """Return the LLM instruction for an editing mode."""
    if mode in PROMPTS:
        return PROMPTS[mode]
    if mode == "Translate":
        return (
            "Translate the text into {0}. Preserve paragraphs and formatting."
        ).format(extra_value)
    if mode == "Custom":
        return extra_value or ""
    raise ValueError("Unknown editing mode: {0}".format(mode))
