"""Tests for the shared backend helpers (prompt construction)."""

from core.backend import SYSTEM_PROMPT, TEXT_FIRST_NOTE, build_messages


def test_default_order_is_instruction_then_text_and_unchanged():
    messages = build_messages("Fix grammar.", "Original.")

    assert messages == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Instruction:\nFix grammar.\n\nText:\nOriginal."},
    ]
    assert build_messages("Fix grammar.", "Original.", text_first=False) == messages
    assert TEXT_FIRST_NOTE not in SYSTEM_PROMPT


def test_text_first_shares_a_prefix_across_instructions():
    first = build_messages("Correct spelling.", "Same segment.", text_first=True)
    second = build_messages("Fix grammar.", "Same segment.", text_first=True)

    assert first[0] == second[0]
    assert first[0]["content"] == SYSTEM_PROMPT + " " + TEXT_FIRST_NOTE
    assert first[1]["content"] == "Text:\nSame segment.\n\nInstruction:\nCorrect spelling."
    prefix = "Text:\nSame segment.\n\nInstruction:\n"
    assert first[1]["content"].startswith(prefix) and second[1]["content"].startswith(prefix)
