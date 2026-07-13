"""Tests for sentence alignment and reviewed reconstruction."""

import pytest

from core.diff_engine import build_edit_session, render_reviewed_text
from core.models import ACCEPTED, REJECTED


@pytest.mark.parametrize(
    "original,proposed",
    [
        ("This are correct.", "This is correct."),
        ("Wait... what?", "Wait—what?"),
        ("I can't re-enter.", "I can’t re-enter."),
        ("très bon", "très bien"),
        ("word word word", "word word"),
        ("Hello!", "Hello, friend!"),
        ("Remove this word now.", "Remove this now."),
    ],
)
def test_accept_and_reject_preserve_exact_documents(original, proposed):
    session = build_edit_session(original, proposed)
    assert session.review_items

    session.accept_all()
    assert render_reviewed_text(session) == proposed

    session.reject_all()
    assert render_reviewed_text(session) == original


def test_adjacent_changed_sentences_are_separate_review_items():
    original = "This are wrong. It have errors."
    proposed = "This is wrong. It has errors."
    session = build_edit_session(original, proposed)

    assert len(session.review_items) == 2
    assert session.review_items[0].original_text == "This are wrong. "
    assert session.review_items[1].original_text == "It have errors."


def test_unequal_fully_rewritten_blocks_still_follow_sentence_punctuation():
    original = (
        "The best first release is sentence-level review with word-level "
        "highlighting—not individual word toggles yet. "
        "That delivers most of the usability improvement while keeping decisions "
        "grammatically safe. "
        "Add per-change-group acceptance once the reconstruction tests are solid. "
        "I did not change the repository. "
        "The existing working-tree changes were preserved, and the current module "
        "passes Python compilation. "
        "Full accessibility compliance could not be determined from screenshots "
        "alone; keyboard navigation and screen-reader behavior still require "
        "hands-on testing. "
        "Want me to plot this out in Figma with the screenshots and notes?"
    )
    proposed = (
        "The best first release is a sentence-level review with word-level "
        "highlighting, rather than individual word toggles yet. "
        "This approach delivers most of the usability improvements while keeping "
        "decisions grammatically sound. "
        "We can add per-change-group acceptance once our reconstruction tests are "
        "solid. "
        "I've left the repository unchanged, preserving the existing working-tree "
        "changes and ensuring that the current module still passes Python "
        "compilation. "
        "However, we cannot determine full accessibility compliance from "
        "screenshots alone; further testing is needed to assess keyboard navigation "
        "and screen-reader behavior. "
        "Would you like me to create a visual outline in Figma using the screenshots "
        "and notes?"
    )

    session = build_edit_session(original, proposed)

    assert len(session.review_items) == 6
    assert session.review_items[0].original_text.endswith("yet. ")
    assert session.review_items[1].original_text.startswith("That delivers")
    assert session.review_items[3].original_text == (
        "I did not change the repository. "
        "The existing working-tree changes were preserved, and the current module "
        "passes Python compilation. "
    )
    session.accept_all()
    assert render_reviewed_text(session) == proposed
    session.reject_all()
    assert render_reviewed_text(session) == original


def test_replacement_is_a_bounded_hunk_not_concatenated_text():
    session = build_edit_session("This are fine.", "This is fine.")
    changes = session.review_items[0].changed_hunks

    assert len(changes) == 1
    assert changes[0].kind == "replace"
    assert changes[0].original_text == "are"
    assert changes[0].proposed_text == "is"
    assert "areis" not in render_reviewed_text(session)


def test_mixed_hunk_decisions_reconstruct_safely():
    session = build_edit_session(
        "It have two error.",
        "It has two errors.",
    )
    changes = session.review_items[0].changed_hunks
    changes[0].decision = ACCEPTED
    changes[1].decision = REJECTED

    assert session.review_items[0].decision == "mixed"
    assert render_reviewed_text(session) == "It has two error."


def test_paragraphs_and_blank_lines_are_preserved():
    original = "First paragraph.\n\nSecond are here.\nFinal line"
    proposed = "First paragraph.\n\nSecond is here.\nFinal line"
    session = build_edit_session(original, proposed)
    session.accept_all()

    assert render_reviewed_text(session) == proposed


@pytest.mark.parametrize(
    "original,proposed",
    [
        ("One sentence.", "One sentence. Added sentence."),
        ("Keep this. Delete this.", "Keep this."),
        ("One long sentence with two ideas.", "One sentence. Two ideas."),
        ("One sentence. Two ideas.", "One long sentence with two ideas."),
    ],
)
def test_sentence_insert_delete_split_and_merge(original, proposed):
    session = build_edit_session(original, proposed)
    session.accept_all()
    assert render_reviewed_text(session) == proposed
    session.reject_all()
    assert render_reviewed_text(session) == original


def test_no_change_response_has_no_review_items():
    text = "Nothing changed.\n\nFormatting stays."
    session = build_edit_session(text, text)

    assert session.review_items == []
    assert session.pending_count == 0
    assert render_reviewed_text(session) == text


def test_sentence_actions_update_all_hunks():
    session = build_edit_session("It have error.", "It has errors.")
    item = session.review_items[0]

    item.accept()
    assert {hunk.decision for hunk in item.changed_hunks} == {ACCEPTED}
    item.reject()
    assert {hunk.decision for hunk in item.changed_hunks} == {REJECTED}
