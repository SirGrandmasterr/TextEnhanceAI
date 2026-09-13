"""Tests for the automatic manuscript workflow (core logic, no Tk)."""

import json
import queue
import threading
import time

import pytest

from core.backend import BackendUnavailable, EditCancelled, OutputTruncated
from core.models import ACCEPTED, PENDING, REJECTED
from core.workflow import (
    CHECK_EXPRESSION,
    CHECK_GRAMMAR,
    CHECK_SPELLING,
    CHECKS,
    STATE_APPLIED,
    STATE_PENDING,
    STATE_REJECTED,
    STATE_SUPERSEDED,
    STATUS_CLEAN,
    STATUS_ERROR,
    STATUS_QUEUED,
    STATUS_READY,
    STATUS_REVIEWED,
    Change,
    CheckResult,
    Project,
    ProjectOptions,
    ProjectRunner,
    Segment,
    apply_result,
    applied_changes,
    build_explanation_messages,
    change_states,
    create_project,
    extract_changes,
    parse_explanations,
    parse_outline,
    render_segment,
    run_check,
    sanity_check_proposal,
    strip_fences,
)

ALL = {check: True for check in CHECKS}


# --------------------------------------------------------------- changes
def test_extract_changes_anchors_offsets_in_the_original():
    original = "Teh cat sat on teh mat. It were happy.\n\nAnother line."
    proposed = "The cat sat on the mat. It was happy.\n\nAnother line."

    changes = extract_changes(original, proposed, CHECK_SPELLING)

    assert [(c.original_text, c.proposed_text) for c in changes] == [
        ("Teh", "The"), ("teh", "the"), ("were", "was")
    ]
    for change in changes:
        assert original[change.start:change.end] == change.original_text
        assert change.check == CHECK_SPELLING
        assert change.decision == PENDING
    assert [c.change_id for c in changes] == ["spelling-1", "spelling-2", "spelling-3"]


def test_insertions_and_deletions_get_zero_or_full_spans():
    original = "I saw the the dog"
    changes = extract_changes(original, "I saw the dog", CHECK_GRAMMAR)
    assert len(changes) == 1
    assert original[changes[0].start:changes[0].end] == changes[0].original_text
    assert changes[0].proposed_text == ""

    changes = extract_changes("I saw dog", "I saw a dog", CHECK_GRAMMAR)
    assert any(c.original_text == "" and "a" in c.proposed_text for c in changes)


def make_segment():
    text = "Teh dog were very very big and it run fast."
    segment = Segment(1, text)
    spelling = extract_changes(text, "The dog were very very big and it run fast.", CHECK_SPELLING)
    grammar = extract_changes(text, "Teh dog was very very big and it ran fast.", CHECK_GRAMMAR)
    expression = extract_changes(text, "Teh dog was enormous and it ran fast.", CHECK_EXPRESSION)
    segment.results[CHECK_SPELLING] = CheckResult(CHECK_SPELLING, "done", "", spelling)
    segment.results[CHECK_GRAMMAR] = CheckResult(CHECK_GRAMMAR, "done", "", grammar)
    segment.results[CHECK_EXPRESSION] = CheckResult(CHECK_EXPRESSION, "done", "", expression)
    return segment


def test_merge_applies_accepted_changes_and_supersedes_overlaps():
    segment = make_segment()
    for change in segment.changes(ALL):
        change.decision = ACCEPTED

    states = change_states(segment, ALL)
    grammar_was = next(c for c in segment.changes(ALL) if c.check == CHECK_GRAMMAR and c.original_text == "were")
    expression_was = next(c for c in segment.changes(ALL) if c.check == CHECK_EXPRESSION and c.original_text == "were")
    assert states[grammar_was.change_id] == STATE_APPLIED
    assert states[expression_was.change_id] == STATE_SUPERSEDED
    rendered = render_segment(segment, ALL)
    assert rendered.startswith("The dog was ")
    assert "ran fast" in rendered
    assert "very very" not in rendered  # expression's non-overlapping change applied
    assert segment.status(ALL) == STATUS_REVIEWED


def test_rejecting_the_winner_lets_the_lower_priority_change_apply():
    segment = make_segment()
    for change in segment.changes(ALL):
        change.decision = ACCEPTED
    grammar_was = next(c for c in segment.changes(ALL) if c.check == CHECK_GRAMMAR and c.original_text == "were")
    grammar_was.decision = REJECTED

    states = change_states(segment, ALL)
    expression_was = next(c for c in segment.changes(ALL) if c.check == CHECK_EXPRESSION and c.original_text == "were")
    assert states[grammar_was.change_id] == STATE_REJECTED
    assert states[expression_was.change_id] == STATE_APPLIED
    assert "was" in render_segment(segment, ALL)


def test_disabled_checks_are_ignored_everywhere():
    segment = make_segment()
    for change in segment.changes(ALL):
        change.decision = ACCEPTED
    enabled = {CHECK_SPELLING: True, CHECK_GRAMMAR: False, CHECK_EXPRESSION: False}

    assert render_segment(segment, enabled) == "The dog were very very big and it run fast."
    assert all(c.check == CHECK_SPELLING for c in segment.changes(enabled))
    assert segment.status(enabled) == STATUS_REVIEWED
    assert segment.status({CHECK_SPELLING: True, CHECK_GRAMMAR: True, CHECK_EXPRESSION: True}) == STATUS_REVIEWED


def test_segment_status_progression():
    segment = Segment(1, "Some text.")
    assert segment.status(ALL) == STATUS_QUEUED
    segment.results[CHECK_SPELLING] = CheckResult(CHECK_SPELLING, "done", "Some text.", [])
    segment.results[CHECK_GRAMMAR] = CheckResult(CHECK_GRAMMAR, "error", error="boom")
    assert segment.status({CHECK_SPELLING: True, CHECK_GRAMMAR: False, CHECK_EXPRESSION: False}) == STATUS_CLEAN
    assert segment.status({CHECK_SPELLING: True, CHECK_GRAMMAR: True, CHECK_EXPRESSION: False}) == STATUS_ERROR
    segment.results[CHECK_GRAMMAR] = CheckResult(
        CHECK_GRAMMAR, "done", "Some texts.", [Change("grammar-1", CHECK_GRAMMAR, 5, 9, "text", "texts")]
    )
    enabled = {CHECK_SPELLING: True, CHECK_GRAMMAR: True, CHECK_EXPRESSION: False}
    assert segment.status(enabled) == STATUS_READY
    segment.find_change("grammar-1").decision = REJECTED
    assert segment.status(enabled) == STATUS_REVIEWED
    assert Segment(2, "   \n").status(ALL) == STATUS_CLEAN


# ---------------------------------------------------------- explanations
def test_parse_explanations_accepts_common_shapes():
    assert parse_explanations('{"1": "Typo.", "2": "Agreement."}') == {1: "Typo.", 2: "Agreement."}
    assert parse_explanations('```json\n{"1": "Typo."}\n```') == {1: "Typo."}
    assert parse_explanations('Sure! {"explanations": ["One", "Two"]} done') == {1: "One", 2: "Two"}
    assert parse_explanations('{"1": {"explanation": "Nested"}, "x": "ignored", "2": ""}') == {1: "Nested"}
    assert parse_explanations("no json here") == {}
    assert strip_fences("```\ntext\n```") == "text"
    assert strip_fences("plain") == "plain"


def test_explanation_prompt_lists_changes_with_context():
    text = "Teh dog sat."
    changes = extract_changes(text, "The dog sat.", CHECK_SPELLING)
    messages = build_explanation_messages(text, changes, CHECK_SPELLING, language="German")
    content = messages[1]["content"]
    assert '1. "Teh" → "The"' in content
    assert "in German" in content
    assert "Text:\nTeh dog sat." in content


def test_parse_outline():
    starts, titles = parse_outline('{"chapters": [{"start": 0, "title": "A"}, {"start": 12, "title": "B"}, {"start": "x"}]}')
    assert starts == [0, 12] and titles == ["A", "B"]
    assert parse_outline("garbage") == ([], [])


def test_sanity_check_rejects_summaries_and_empty_answers():
    with pytest.raises(Exception):
        sanity_check_proposal("A long paragraph of text that goes on and on and on.", "Summary.")
    with pytest.raises(Exception):
        sanity_check_proposal("text", "")
    sanity_check_proposal("Almost same text.", "Almost same text!")


# ------------------------------------------------------------ evaluation
class FakeService:
    """Deterministic backend: fixes 'teh', 'were' and 'very very'; explains in JSON."""

    display_name = "fake"

    def __init__(self, fail_first=0, truncate=False, delay=0.0):
        self.calls = []
        self.fail_first = fail_first
        self.truncate = truncate
        self.delay = delay
        self.lock = threading.Lock()

    def stream_edit(self, model, instruction, text, cancel_event, on_progress=None):
        with self.lock:
            self.calls.append(("edit", instruction[:20], text))
            if self.fail_first > 0:
                self.fail_first -= 1
                raise BackendUnavailable("transient")
        if self.truncate:
            raise OutputTruncated("cut")
        if self.delay:
            if cancel_event.wait(self.delay):
                raise EditCancelled("cancelled")
        if instruction.startswith("Correct spelling"):
            return text.replace("teh", "the").replace("Teh", "The")
        if instruction.startswith("Fix grammar"):
            return text.replace("were", "was")
        return "```\n" + text.replace("very very", "extremely") + "\n```"

    def generate(self, model, messages, cancel_event, on_progress=None, max_tokens=None):
        with self.lock:
            self.calls.append(("explain", messages[1]["content"][:20], None))
        count = messages[1]["content"].count("→")
        return json.dumps({str(n): "Reason {0}".format(n) for n in range(1, count + 1)})


def test_run_check_produces_changes_with_explanations_and_strips_fences():
    service = FakeService()
    result = run_check(service, "m", "It were very very big.", CHECK_EXPRESSION, threading.Event())

    assert result.status == "done"
    assert result.proposed_text == "It were extremely big."
    assert [c.explanation for c in result.changes] == ["Reason 1"]
    assert result.explained is True
    assert result.duration >= 0
    assert [call[0] for call in service.calls] == ["edit", "explain"]


def test_run_check_retries_transient_errors_but_not_truncation():
    service = FakeService(fail_first=1)
    result = run_check(service, "m", "Teh dog.", CHECK_SPELLING, threading.Event())
    assert result.status == "done"
    assert len([c for c in service.calls if c[0] == "edit"]) == 2

    service = FakeService(fail_first=2)
    result = run_check(service, "m", "Teh dog.", CHECK_SPELLING, threading.Event(), retries=1)
    assert result.status == "error" and "transient" in result.error

    result = run_check(FakeService(truncate=True), "m", "Teh dog.", CHECK_SPELLING, threading.Event())
    assert result.status == "error" and "cut" in result.error


def test_run_check_without_explanations_uses_fallback_text():
    result = run_check(FakeService(), "m", "Teh dog.", CHECK_SPELLING, threading.Event(), explain=False)
    assert result.changes[0].explanation == "Spelling correction."
    assert result.explained is False


def test_run_check_propagates_cancellation():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(EditCancelled):
        run_check(FakeService(delay=1), "m", "Teh dog.", CHECK_SPELLING, cancel)


FILLER = " ".join(["The evening settled quietly over the small town by the river."] * 5)
MANUSCRIPT = (
    "Kapitel 1\n\nTeh dog were very very big. It run fast.\n\n" + FILLER + "\n\n"
    "Kapitel 2\n\n" + FILLER + "\n\nNothing wrong here at all.\n"
)


def make_project(tmp_path, **option_overrides):
    source = tmp_path / "novel.txt"
    source.write_text(MANUSCRIPT, encoding="utf-8")
    options = ProjectOptions(target_chars=200, max_chars=400, parallelism=2, **option_overrides)
    return create_project(source, MANUSCRIPT, options, model="m", backend="fake")


def drain(events, timeout=10):
    finished = None
    collected = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            event = events.get(timeout=0.2)
        except queue.Empty:
            continue
        collected.append(event)
        if event[0] == "workflow_finished":
            finished = event
            break
    assert finished is not None, "runner never finished"
    return collected


def test_project_creation_splits_and_writes_chapter_files(tmp_path):
    project = make_project(tmp_path)

    assert project.method == "headings:chapter-word"
    assert [c.title for c in project.chapters] == ["Kapitel 1", "Kapitel 2"]
    assert project.root == tmp_path / "novel.teai"
    assert project.render_document() == MANUSCRIPT
    paths = project.write_chapter_files()
    assert [p.name for p in paths] == ["01 - Kapitel 1.txt", "02 - Kapitel 2.txt"]
    assert paths[0].read_text(encoding="utf-8").startswith("Kapitel 1\n\nTeh dog")
    assert len(project.pending_tasks()) == 3 * sum(len(c.segments) for c in project.chapters)


def test_runner_evaluates_everything_and_results_round_trip(tmp_path):
    project = make_project(tmp_path, auto_accept={CHECK_SPELLING: True})
    events = queue.Queue()
    runner = ProjectRunner(project, FakeService(), "m", events)
    queued = runner.start()
    collected = drain(events)

    results = [event for event in collected if event[0] == "workflow_result"]
    assert len(results) == queued == runner.total
    for _, chapter_index, segment_index, check, result in results:
        apply_result(project, chapter_index, segment_index, check, result)
    assert collected[-1] == ("workflow_finished", False)
    assert project.pending_tasks() == []

    stats = project.progress()
    assert stats["tasks_done"] == stats["tasks_total"]
    assert stats["per_check"][CHECK_SPELLING]["accepted"] == stats["per_check"][CHECK_SPELLING]["changes"] >= 1
    assert stats["per_check"][CHECK_GRAMMAR]["changes"] >= 1
    assert stats["pending"] > 0  # grammar/expression are not auto-accepted

    project.save()
    reloaded = Project.load(project.root)
    assert reloaded.to_dict()["chapters"] == project.to_dict()["chapters"]
    assert reloaded.options.auto_accept[CHECK_SPELLING] is True
    assert reloaded.pending_tasks() == []

    for _, segment in reloaded.all_segments():
        for change in segment.changes(reloaded.enabled):
            change.decision = ACCEPTED
    paths = reloaded.export()
    document = paths["document"].read_text(encoding="utf-8")
    assert "The dog was extremely big." in document
    assert document.startswith("Kapitel 1\n\n")
    assert document.endswith("Nothing wrong here at all.\n")
    report = paths["report"].read_text(encoding="utf-8")
    assert "# Review report: novel" in report
    assert "[Spelling] `Teh` → `The` — applied — Reason" in report
    assert (reloaded.root / "reviewed" / "01 - Kapitel 1.txt").exists()


def test_runner_can_be_cancelled_and_resumed(tmp_path):
    project = make_project(tmp_path)
    events = queue.Queue()
    runner = ProjectRunner(project, FakeService(delay=0.5), "m", events, parallelism=1)
    runner.start()
    time.sleep(0.1)
    runner.cancel()
    collected = drain(events)
    assert collected[-1] == ("workflow_finished", True)
    for event in collected:
        if event[0] == "workflow_result":
            apply_result(project, event[1], event[2], event[3], event[4])
    remaining = project.pending_tasks()
    assert 0 < len(remaining) <= runner.total

    events = queue.Queue()
    runner = ProjectRunner(project, FakeService(), "m", events, parallelism=3)
    assert runner.start() == len(remaining)
    for event in drain(events):
        if event[0] == "workflow_result":
            apply_result(project, event[1], event[2], event[3], event[4])
    assert project.pending_tasks() == []


def test_runner_with_nothing_to_do_finishes_immediately(tmp_path):
    project = make_project(tmp_path, checks={check: False for check in CHECKS})
    events = queue.Queue()
    assert ProjectRunner(project, FakeService(), "m", events).start() == 0
    assert events.get(timeout=1) == ("workflow_finished", False)
