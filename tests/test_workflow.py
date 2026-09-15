"""Tests for the automatic manuscript workflow (core logic, no Tk)."""

import json
import queue
import threading
import time

import pytest

from core.backend import BackendUnavailable, EditCancelled, OutputTruncated
from core.models import ACCEPTED, PENDING, REJECTED
from core.change_kinds import CHANGE_KIND_LABELS, CHANGE_KINDS, levenshtein
from core.workflow import (
    CHANGE_KINDS as WORKFLOW_CHANGE_KINDS,
    CHECK_EXPRESSION,
    CHECK_GRAMMAR,
    CHECK_INSTRUCTIONS,
    CHECK_SPELLING,
    CHECKS,
    FLAG_GROWTH,
    FLAG_NOVEL_WORDS,
    FLAG_REPORT_MARK,
    FLAG_REWRITE_IN_STRICT_CHECK,
    GLOSSARY_EXPLANATION,
    GLOSSARY_HEADER,
    GLOSSARY_MAX_CHARS,
    GLOSSARY_MAX_TERMS,
    STYLE_GUIDE_HEADER,
    STYLE_GUIDE_MAX_CHARS,
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
    build_check_instruction,
    build_explanation_messages,
    change_states,
    classify_change,
    create_project,
    extract_changes,
    flag_changes,
    flag_suspicious,
    format_glossary,
    format_style_guide,
    glossary_matcher,
    glossary_prompt_terms,
    normalise_style_guide,
    novel_words,
    parse_explanations,
    parse_glossary,
    parse_outline,
    render_segment,
    run_check,
    sanity_check_proposal,
    strip_fences,
    suppress_glossary_changes,
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


# ----------------------------------------------------------------- kinds
KIND_EXAMPLES = [
    ("whitespace", "a  b", "a b"),
    ("whitespace", "word\n", " word "),
    ("punctuation", "", ","),
    ("punctuation", "word.", "word,"),
    ("punctuation", "Hallo Welt", "Hallo, Welt"),
    ("capitalization", "hello", "Hello"),
    ("capitalization", "the river", "The River"),
    ("spelling", "teh", "the"),
    ("spelling", "Grossmutter", "Großmutter"),  # ß is not a case change
    ("spelling", "recieved", "received"),
    ("word_choice", "were", "was"),
    ("word_choice", "very very", "extremely"),
    ("word_choice", "at this point", "now"),
    ("insertion", "", "a "),
    ("insertion", " ", "sehr "),
    ("deletion", "the ", ""),
    ("deletion", " die, dass", "  "),
    ("rewrite", "in the event that it might possibly rain", "in case it rained"),
    ("rewrite", "Die Tatsache der Sachlage", "Tatsächlich, so"),
    ("rewrite", "at this point in", "now"),  # one side longer than three words
]


@pytest.mark.parametrize("kind, original, proposed", KIND_EXAMPLES, ids=[
    "{0}:{1!r}->{2!r}".format(*example) for example in KIND_EXAMPLES
])
def test_classify_change_examples(kind, original, proposed):
    assert classify_change(original, proposed) == kind


def test_change_kinds_are_fixed_order_and_labelled():
    assert CHANGE_KINDS == (
        "whitespace", "punctuation", "capitalization", "spelling",
        "word_choice", "insertion", "deletion", "rewrite",
    )
    assert WORKFLOW_CHANGE_KINDS is CHANGE_KINDS
    assert set(CHANGE_KIND_LABELS) == set(CHANGE_KINDS)
    assert all(classify_change(o, p) in CHANGE_KINDS for _, o, p in KIND_EXAMPLES)


def test_levenshtein_helper():
    assert levenshtein("", "") == 0
    assert levenshtein("abc", "") == 3
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("teh", "the") == 2
    assert levenshtein("flaw", "lawn") == 2
    assert levenshtein("Straße", "Strasse") == 2


def test_mixed_german_sentence_is_classified_per_change():
    original = "Er wusste nicht ob der Zug kommt, und die Tatsache der Sachlage war die, dass er müde war."
    proposed = "Er wusste nicht, ob der Zug kommt, und tatsächlich war er müde."

    changes = extract_changes(original, proposed, CHECK_GRAMMAR)

    assert [(c.original_text, c.proposed_text, c.kind) for c in changes] == [
        ("", ",", "punctuation"),
        ("die Tatsache der Sachlage", "tatsächlich", "rewrite"),
        (" die, dass", "", "deletion"),
        (" war", "", "deletion"),
    ]

    typo = extract_changes("Er hatte kaum geschlaffen.", "Er hatte kaum geschlafen.", CHECK_SPELLING)
    assert [(c.original_text, c.kind) for c in typo] == [("geschlaffen", "spelling")]


def test_kind_round_trips_and_is_recomputed_when_missing_or_unknown():
    change = Change("grammar-1", CHECK_GRAMMAR, 15, 15, "", ",")
    assert change.kind == "punctuation"
    data = change.to_dict()
    assert data["kind"] == "punctuation"
    assert Change.from_dict(data) == change

    legacy = {key: value for key, value in data.items() if key != "kind"}
    assert Change.from_dict(legacy).kind == "punctuation"
    assert Change.from_dict(dict(data, kind="")).kind == "punctuation"
    assert Change.from_dict(dict(data, kind="banana")).kind == "punctuation"

    # A stored, valid kind is trusted even if the heuristic would now differ.
    assert Change.from_dict(dict(data, kind="rewrite")).kind == "rewrite"
    assert Change("x", CHECK_SPELLING, 0, 3, "teh", "the", kind="word_choice").kind == "word_choice"


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
        self.instructions = []  # full instruction of every edit request
        self.prompts = []  # full user message of every explanation request
        self.fail_first = fail_first
        self.truncate = truncate
        self.delay = delay
        self.lock = threading.Lock()

    def stream_edit(self, model, instruction, text, cancel_event, on_progress=None, text_first=False):
        with self.lock:
            self.calls.append(("edit", instruction[:20], text, text_first))
            self.instructions.append(instruction)
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
            self.prompts.append(messages[1]["content"])
        count = messages[1]["content"].count("→")
        return json.dumps({str(n): "Reason {0}".format(n) for n in range(1, count + 1)})


# ----------------------------------------------------------- style guide
GUIDE = "British spelling\nkeep dialect inside dialogue\nnever touch quotations"
GUIDE_BLOCK = "\n\n" + STYLE_GUIDE_HEADER + "\n" + GUIDE


def test_check_instruction_without_style_guide_is_the_plain_instruction():
    for check in CHECKS:
        assert build_check_instruction(check) == CHECK_INSTRUCTIONS[check]
        assert build_check_instruction(check, ProjectOptions()) == CHECK_INSTRUCTIONS[check]
        assert build_check_instruction(check, style_guide="  \n\t ") == CHECK_INSTRUCTIONS[check]
    assert format_style_guide("") == "" and format_style_guide(None) == ""


def test_check_instruction_appends_the_authors_rules_one_per_line():
    options = ProjectOptions(style_guide=GUIDE)
    instruction = build_check_instruction(CHECK_GRAMMAR, options)
    assert instruction == CHECK_INSTRUCTIONS[CHECK_GRAMMAR] + GUIDE_BLOCK
    assert instruction.startswith(CHECK_INSTRUCTIONS[CHECK_GRAMMAR])
    # an explicit string wins over the options
    assert build_check_instruction(CHECK_GRAMMAR, options, style_guide="x") == CHECK_INSTRUCTIONS[CHECK_GRAMMAR] + (
        "\n\n" + STYLE_GUIDE_HEADER + "\nx"
    )


def test_style_guide_is_normalised_to_one_rule_per_line():
    typed = "  British spelling · keep   dialect inside dialogue  \n\n\r\n never touch quotations \n"
    assert normalise_style_guide(typed) == GUIDE
    assert format_style_guide(typed) == GUIDE_BLOCK


def test_style_guide_is_trimmed_to_the_limit():
    long_rules = "\n".join("rule number {0} is rather long".format(n) for n in range(200))
    assert len(long_rules) > STYLE_GUIDE_MAX_CHARS
    trimmed = normalise_style_guide(long_rules)
    assert len(trimmed) <= STYLE_GUIDE_MAX_CHARS == 1500
    assert long_rules.startswith(trimmed)
    assert build_check_instruction(CHECK_SPELLING, style_guide=long_rules).endswith(trimmed)


def test_explanation_prompt_carries_the_authors_rules():
    text = "Teh colour."
    changes = extract_changes(text, "The colour.", CHECK_SPELLING)
    content = build_explanation_messages(text, changes, CHECK_SPELLING, style_guide=GUIDE)[1]["content"]
    assert GUIDE_BLOCK in content
    assert content.index(STYLE_GUIDE_HEADER) < content.index("Text:\nTeh colour.")
    plain = build_explanation_messages(text, changes, CHECK_SPELLING)[1]["content"]
    assert STYLE_GUIDE_HEADER not in plain


def test_style_guide_round_trips_through_project_options_and_project(tmp_path):
    options = ProjectOptions(style_guide=GUIDE)
    assert options.to_dict()["style_guide"] == GUIDE
    assert ProjectOptions.from_dict(options.to_dict()).style_guide == GUIDE

    legacy = {key: value for key, value in options.to_dict().items() if key != "style_guide"}
    assert ProjectOptions.from_dict(legacy).style_guide == ""
    assert ProjectOptions.from_dict(dict(legacy, style_guide=None)).style_guide == ""

    source = tmp_path / "novel.txt"
    source.write_text("Some text.", encoding="utf-8")
    project = create_project(source, "Some text.", options, model="m", backend="fake")
    data = project.to_dict()
    assert data["options"]["style_guide"] == GUIDE
    assert Project.from_dict(data).options.style_guide == GUIDE
    del data["options"]["style_guide"]
    assert Project.from_dict(data).options.style_guide == ""


def test_run_check_sends_the_authors_rules_with_edit_and_explanation():
    service = FakeService()
    result = run_check(service, "m", "Teh dog.", CHECK_SPELLING, threading.Event(), style_guide=GUIDE)

    assert result.status == "done" and len(result.changes) == 1
    assert service.instructions == [CHECK_INSTRUCTIONS[CHECK_SPELLING] + GUIDE_BLOCK]
    assert GUIDE_BLOCK in service.prompts[0]
    # keyword stays optional: positional callers are unaffected
    assert run_check(FakeService(), "m", "Teh dog.", CHECK_SPELLING, threading.Event(), True, "German", 1).status == "done"


def test_runner_passes_the_project_style_guide_to_every_check(tmp_path):
    project = make_project(tmp_path, style_guide=GUIDE)
    events = queue.Queue()
    service = FakeService()
    ProjectRunner(project, service, "m", events, parallelism=2).start()
    drain(events)

    assert service.instructions and all(GUIDE_BLOCK in instruction for instruction in service.instructions)
    assert service.prompts and all(GUIDE_BLOCK in prompt for prompt in service.prompts)


# -------------------------------------------------------------- glossary
def test_parse_glossary_trims_dedupes_and_accepts_lists():
    assert parse_glossary("Thalbrück\n\n  Meret   Aubinger \nThalbrück\n*\nhyper*") == [
        "Thalbrück", "Meret Aubinger", "hyper*"
    ]
    assert parse_glossary("a · b") == ["a", "b"]
    assert parse_glossary(["x", " y ", "", "x"]) == ["x", "y"]
    assert parse_glossary(None) == [] and parse_glossary("") == []


def test_glossary_matcher_semantics():
    hit = glossary_matcher(["Thalbrück", "Meret Aubinger", "hyper*", "C++", "Straße"])
    # exact terms need word boundaries on both sides
    assert hit("nach Thalbrück fahren") and hit("Thalbrück,") and hit("(Thalbrück)")
    assert not hit("Thalbrücker") and not hit("Neuthalbrück")
    # case matters: a protected name keeps its capitalization
    assert not hit("thalbrück") and not hit("THALBRÜCK")
    # umlauts and ß are word characters, not boundaries
    assert hit("die Straße") and not hit("Straßenbahn") and not hit("Strasse")
    # multi-word terms match as a phrase only
    assert hit("Meret Aubinger kam") and not hit("Meret kam") and not hit("Aubinger")
    # wildcard: word-boundary at the start, any suffix
    assert hit("hyperdrive") and hit("hyper") and hit("hyper-drive")
    assert not hit("superhyper") and not hit("ahyperb")
    # punctuation inside terms is escaped
    assert hit("C++ code") and not hit("C code") and not hit("xC++")
    assert not hit("") and not glossary_matcher([])("Thalbrück") and not glossary_matcher(["*"])("anything")


def test_post_filter_drops_changes_touching_protected_terms():
    text = "Teh Kestenholz dog were very very big."
    changes = extract_changes(text, "The Kestenholz dog was extremely big.", CHECK_GRAMMAR)
    kept, suppressed = suppress_glossary_changes(text, changes, glossary_matcher(["Teh", "very*"]))
    assert [c.original_text for c in kept] == ["were"]
    assert [c.original_text for c in suppressed] == ["Teh", "very very"]
    assert all(c.explanation == GLOSSARY_EXPLANATION for c in suppressed)

    # an insertion is dropped only when a protected term sits within 20 characters
    near = "I saw Kestenholz there"
    inserted = extract_changes(near, "I saw a Kestenholz there", CHECK_GRAMMAR)
    assert inserted[0].original_text == ""
    assert suppress_glossary_changes(near, inserted, glossary_matcher(["Kestenholz"])) == ([], inserted)
    far = "I saw dog. " + "x" * 30 + " Kestenholz"
    inserted = extract_changes(far, far.replace("saw dog", "saw a dog"), CHECK_GRAMMAR)
    assert suppress_glossary_changes(far, inserted, glossary_matcher(["Kestenholz"])) == (inserted, [])
    # no glossary: nothing is touched
    assert suppress_glossary_changes(text, changes, glossary_matcher([])) == (changes, [])


def test_run_check_suppresses_and_keeps_the_dropped_changes():
    service = FakeService()
    result = run_check(service, "m", "Teh dog were big.", CHECK_SPELLING, threading.Event(), glossary=["Teh"])

    assert result.status == "done"
    assert result.changes == []
    assert [c.original_text for c in result.suppressed] == ["Teh"]
    assert service.prompts == []  # nothing left to explain
    assert GLOSSARY_HEADER + "Teh" in service.instructions[0]

    result = run_check(FakeService(), "m", "Teh dog were big.", CHECK_SPELLING, threading.Event(), glossary=["Kestenholz"])
    assert [c.original_text for c in result.changes] == ["Teh"] and result.suppressed == []


def test_suppressed_changes_round_trip_and_are_counted(tmp_path):
    result = run_check(FakeService(), "m", "Teh dog.", CHECK_SPELLING, threading.Event(), glossary=["Teh"])
    data = result.to_dict()
    assert data["suppressed"][0]["original"] == "Teh"
    restored = CheckResult.from_dict(data)
    assert [c.to_dict() for c in restored.suppressed] == data["suppressed"]
    legacy = {key: value for key, value in data.items() if key != "suppressed"}
    assert CheckResult.from_dict(legacy).suppressed == []

    project = make_project(tmp_path, glossary=["Teh", "very*"])
    events = queue.Queue()
    ProjectRunner(project, FakeService(), "m", events, parallelism=2).start()
    for event in drain(events):
        if event[0] == "workflow_result":
            apply_result(project, *event[1:])
    stats = project.progress()
    assert stats["suppressed"] >= 2  # "Teh" (spelling) and "very very" (expression)
    assert stats["per_check"][CHECK_SPELLING]["suppressed"] == 1
    assert stats["per_check"][CHECK_EXPRESSION]["suppressed"] == 1
    assert stats["per_check"][CHECK_GRAMMAR]["suppressed"] == 0
    assert stats["suppressed"] == sum(per["suppressed"] for per in stats["per_check"].values())
    assert not any("Teh" in c.original_text for _, s in project.all_segments() for c in s.changes(ALL))
    report = project.build_report()
    assert "  - Spelling: 0 proposed, 0 accepted, 0 rejected\n    - Glossary suppressed 1 proposed change(s)" in report
    assert "  - Grammar:" in report and report.count("Glossary suppressed") == 2

    project.save()
    reloaded = Project.load(project.root)
    assert reloaded.options.glossary == ["Teh", "very*"]
    assert reloaded.progress()["suppressed"] == stats["suppressed"]


def test_glossary_option_round_trips_and_defaults_to_empty():
    options = ProjectOptions(glossary=["Thalbrück", "hyper*"])
    assert options.to_dict()["glossary"] == ["Thalbrück", "hyper*"]
    assert ProjectOptions.from_dict(options.to_dict()).glossary == ["Thalbrück", "hyper*"]
    legacy = {key: value for key, value in options.to_dict().items() if key != "glossary"}
    assert ProjectOptions.from_dict(legacy).glossary == []
    assert ProjectOptions.from_dict(dict(legacy, glossary=None)).glossary == []
    assert ProjectOptions.from_dict(dict(legacy, glossary="a\nb")).glossary == ["a", "b"]  # tolerant of text


def test_prompt_lists_protected_terms_up_to_the_caps():
    instruction = build_check_instruction(CHECK_SPELLING, glossary=["Thalbrück", "hyper*"], style_guide="British spelling")
    assert instruction.endswith(GLOSSARY_HEADER + "Thalbrück, hyper*")
    assert instruction.index(STYLE_GUIDE_HEADER) < instruction.index(GLOSSARY_HEADER)
    assert build_check_instruction(CHECK_SPELLING, glossary=[]) == CHECK_INSTRUCTIONS[CHECK_SPELLING]
    assert format_glossary(None) == ""

    many = ["term{0}".format(n) for n in range(GLOSSARY_MAX_TERMS + 50)]
    listed = glossary_prompt_terms(many)
    assert listed == many[:GLOSSARY_MAX_TERMS]
    assert "term{0}".format(GLOSSARY_MAX_TERMS) not in format_glossary(many)

    long_terms = ["{0}{1}".format("x" * 150, n) for n in range(30)]
    block = format_glossary(long_terms)
    assert len(block) - len("\n\n" + GLOSSARY_HEADER) <= GLOSSARY_MAX_CHARS
    assert long_terms[0] in block and long_terms[-1] not in block

    # beyond the caps the post-filter still protects the term
    hit = glossary_matcher(many + ["Kestenholz"])
    assert hit("Kestenholz") and "Kestenholz" not in format_glossary(many + ["Kestenholz"])


# ---------------------------------------------------- hallucination guard
def _single(segment, proposed, check):
    changes = extract_changes(segment, proposed, check)
    assert len(changes) == 1, changes
    return changes[0]


def test_growth_rule_fires_on_added_clauses():
    segment = "The dog sat on the mat."
    change = _single(segment, "The dog sat on the mat, watching the stranger approach slowly.", CHECK_EXPRESSION)
    assert change.original_text == "" and len(change.proposed_text) > 12
    assert flag_suspicious(segment, change) == FLAG_GROWTH

    # a long replacement of a short phrase also counts
    change = Change("x", CHECK_EXPRESSION, 4, 7, "sat", "settled down comfortably for the evening")
    assert flag_suspicious(segment, change) == FLAG_GROWTH
    # exactly at the limit does not fire: 1.6 * 3 + 12 = 16.8 → 16 characters are fine
    assert flag_suspicious(segment, Change("x", CHECK_EXPRESSION, 4, 7, "sat", "s" * 16)) != FLAG_GROWTH


def test_novel_words_rule_needs_two_unknown_content_words():
    segment = "The dog sat on the mat while the children played."
    # two content words absent from the segment, but not long enough to trip growth
    change = Change("x", CHECK_EXPRESSION, 4, 7, "dog", "golden retriever")
    assert flag_suspicious(segment, change) == FLAG_NOVEL_WORDS
    assert novel_words(segment, "golden retriever") == ["golden", "retriever"]
    # one novel word is a normal rephrase
    assert flag_suspicious(segment, Change("x", CHECK_EXPRESSION, 4, 7, "dog", "the retriever")) is None
    # stopwords and words already in the segment (any case) never count
    assert novel_words(segment, "While THE Children played") == []
    assert novel_words("Er kam nach Hause.", "obwohl er dennoch nach Hause kam") == []
    assert flag_suspicious(segment, Change("x", CHECK_GRAMMAR, 0, 3, "The", "Although they")) is None


def test_rewrite_rule_only_fires_for_strict_checks():
    segment = "Die Tatsache der Sachlage war die, dass er müde war."
    proposed = "Tatsächlich war er müde."
    grammar = extract_changes(segment, proposed, CHECK_GRAMMAR)
    rewrite = next(c for c in grammar if c.kind == "rewrite")
    assert flag_suspicious(segment, rewrite) == FLAG_REWRITE_IN_STRICT_CHECK
    assert [flag_suspicious(segment, c) for c in grammar if c.kind != "rewrite"] == [None, None]
    spelling = next(c for c in extract_changes(segment, proposed, CHECK_SPELLING) if c.kind == "rewrite")
    assert flag_suspicious(segment, spelling) == FLAG_REWRITE_IN_STRICT_CHECK
    expression = next(c for c in extract_changes(segment, proposed, CHECK_EXPRESSION) if c.kind == "rewrite")
    assert flag_suspicious(segment, expression) is None


def test_ordinary_corrections_are_not_flagged():
    assert flag_suspicious("Teh dog sat.", _single("Teh dog sat.", "The dog sat.", CHECK_SPELLING)) is None
    german = "Er wusste nicht ob der Zug kommt."
    assert flag_suspicious(german, _single(german, "Er wusste nicht, ob der Zug kommt.", CHECK_GRAMMAR)) is None
    segment = "It were very very big."
    assert flag_suspicious(segment, _single(segment, "It were extremely big.", CHECK_EXPRESSION)) is None
    assert flag_suspicious(segment, Change("x", CHECK_EXPRESSION, 8, 17, "very very", "really quite")) is None
    assert flag_suspicious("He run fast.", _single("He run fast.", "He ran fast.", CHECK_GRAMMAR)) is None
    assert flag_suspicious("a b", Change("x", CHECK_GRAMMAR, 1, 2, " b", "")) is None  # deletion


def test_flags_persist_and_default_to_empty():
    change = Change("g-1", CHECK_GRAMMAR, 0, 3, "dog", "golden retriever", flags=[FLAG_NOVEL_WORDS])
    assert change.flagged and change.to_dict()["flags"] == [FLAG_NOVEL_WORDS]
    assert Change.from_dict(change.to_dict()) == change
    legacy = {key: value for key, value in change.to_dict().items() if key != "flags"}
    restored = Change.from_dict(legacy)
    assert restored.flags == [] and not restored.flagged
    assert Change.from_dict(dict(legacy, flags=None)).flags == []
    plain = Change("g-2", CHECK_GRAMMAR, 0, 3, "teh", "the")
    assert plain.flags == [] and plain.to_dict()["flags"] == []


class InventingService(FakeService):
    """Fake backend whose expression check appends a clause the text never had."""

    def stream_edit(self, model, instruction, text, cancel_event, on_progress=None, text_first=False):
        if instruction.startswith("Improve expression"):
            return text.replace("It run fast.", "It run fast, chasing the stranger.")
        return super().stream_edit(model, instruction, text, cancel_event, on_progress, text_first)


def test_run_check_flags_after_the_glossary_filter_and_reports(tmp_path):
    text = "Teh dog were very very big. It run fast."
    result = run_check(InventingService(), "m", text, CHECK_EXPRESSION, threading.Event())
    flagged = [c for c in result.changes if c.flagged]
    assert len(flagged) == 1 and flagged[0].flags == [FLAG_GROWTH]
    assert "stranger" in flagged[0].proposed_text
    assert result.status == "done" and len(result.changes) == 1

    # a suppressed change is not flagged (the glossary filter runs first; "fast" sits next to the insertion)
    result = run_check(InventingService(), "m", text, CHECK_EXPRESSION, threading.Event(), glossary=["fast"])
    assert result.suppressed and not any(c.flagged for c in result.suppressed)
    assert not any(c.flagged for c in result.changes)

    project = make_project(tmp_path)
    events = queue.Queue()
    ProjectRunner(project, InventingService(), "m", events, parallelism=2).start()
    for event in drain(events):
        if event[0] == "workflow_result":
            apply_result(project, *event[1:])
    stats = project.progress()
    assert stats["flagged"] == stats["flagged_pending"] == 1
    assert stats["per_check"][CHECK_EXPRESSION]["flagged"] == 1
    assert stats["per_check"][CHECK_SPELLING]["flagged"] == 0
    report = project.build_report()
    assert FLAG_REPORT_MARK + " (growth)" in report
    assert "    - " + FLAG_REPORT_MARK + ": 1 change(s) flagged" in report
    assert report.count(FLAG_REPORT_MARK) == 2

    project.save()
    reloaded = Project.load(project.root)
    assert reloaded.progress()["flagged"] == 1
    the_change = next(c for _, s in reloaded.all_segments() for c in s.changes(ALL) if c.flagged)
    the_change.decision = REJECTED
    assert reloaded.progress()["flagged_pending"] == 0 and reloaded.progress()["flagged"] == 1


def test_flag_changes_is_idempotent():
    segment = "The dog sat on the mat."
    change = Change("x", CHECK_EXPRESSION, 4, 7, "dog", "golden retriever")
    assert flag_changes(segment, [change]) == [change]
    flag_changes(segment, [change])
    assert change.flags == [FLAG_NOVEL_WORDS]


def test_instructions_forbid_invented_content():
    assert CHECK_INSTRUCTIONS[CHECK_EXPRESSION].endswith(
        "Never add facts, names, clauses or sentences that are not already in the text."
    )
    assert CHECK_INSTRUCTIONS[CHECK_GRAMMAR].endswith("Do not rewrite sentences.")


def test_run_check_produces_changes_with_explanations_and_strips_fences():
    service = FakeService()
    result = run_check(service, "m", "It were very very big.", CHECK_EXPRESSION, threading.Event())

    # Review edits put the segment before the instruction (prefix-cache friendly).
    assert service.calls[0] == ("edit", "Improve expression o", "It were very very big.", True)
    # Without author's instructions the plain check instruction is sent.
    assert service.instructions == [CHECK_INSTRUCTIONS[CHECK_EXPRESSION]]
    assert STYLE_GUIDE_HEADER not in service.prompts[0]

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
    assert set(stats["per_kind"]) == set(CHANGE_KINDS)
    assert sum(kind["changes"] for kind in stats["per_kind"].values()) == stats["changes"]
    assert stats["per_kind"]["spelling"]["accepted"] == stats["per_kind"]["spelling"]["changes"] >= 1  # Teh -> The
    assert stats["per_kind"]["word_choice"]["changes"] >= 2  # were -> was, very very -> extremely
    assert stats["per_check"][CHECK_SPELLING]["by_kind"]["spelling"] == stats["per_check"][CHECK_SPELLING]["changes"]
    assert stats["per_check"][CHECK_EXPRESSION]["by_kind"]["word_choice"] >= 1

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
    assert "  - Spelling: 1 proposed, 1 accepted, 0 rejected\n    - by kind: spelling 1\n" in report
    assert "    - by kind: word choice 1\n" in report  # expression: very very -> extremely
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


def test_runner_dispatches_all_checks_of_a_segment_consecutively(tmp_path):
    project = make_project(tmp_path)
    pending = project.pending_tasks()
    assert len(pending) > len(CHECKS)  # several segments
    # pending_tasks() is (chapter, segment, check) with checks innermost ...
    for index in range(0, len(pending), len(CHECKS)):
        group = pending[index:index + len(CHECKS)]
        assert {(chapter, segment) for chapter, segment, _ in group} == {group[0][:2]}
        assert [check for _, _, check in group] == list(CHECKS)
    assert [task[:2] for task in pending] == sorted(task[:2] for task in pending)

    # ... and the runner starts the tasks in exactly that order.
    events = queue.Queue()
    runner = ProjectRunner(project, FakeService(), "m", events, parallelism=1)
    runner.start()
    started = [event[1:] for event in drain(events) if event[0] == "workflow_started"]
    assert started == pending


def test_runner_dispatches_all_checks_of_a_segment_consecutively(tmp_path):
    project = make_project(tmp_path)
    pending = project.pending_tasks()
    assert len(pending) > len(CHECKS)  # several segments
    # pending_tasks() is (chapter, segment, check) with checks innermost ...
    for index in range(0, len(pending), len(CHECKS)):
        group = pending[index:index + len(CHECKS)]
        assert {(chapter, segment) for chapter, segment, _ in group} == {group[0][:2]}
        assert [check for _, _, check in group] == list(CHECKS)
    assert [task[:2] for task in pending] == sorted(task[:2] for task in pending)

    # ... and the runner starts the tasks in exactly that order.
    events = queue.Queue()
    runner = ProjectRunner(project, FakeService(), "m", events, parallelism=1)
    runner.start()
    started = [event[1:] for event in drain(events) if event[0] == "workflow_started"]
    assert started == pending


def test_runner_dispatches_all_checks_of_a_segment_consecutively(tmp_path):
    project = make_project(tmp_path)
    pending = project.pending_tasks()
    assert len(pending) > len(CHECKS)  # several segments
    # pending_tasks() is (chapter, segment, check) with checks innermost ...
    for index in range(0, len(pending), len(CHECKS)):
        group = pending[index:index + len(CHECKS)]
        assert {(chapter, segment) for chapter, segment, _ in group} == {group[0][:2]}
        assert [check for _, _, check in group] == list(CHECKS)
    assert [task[:2] for task in pending] == sorted(task[:2] for task in pending)

    # ... and the runner starts the tasks in exactly that order.
    events = queue.Queue()
    runner = ProjectRunner(project, FakeService(), "m", events, parallelism=1)
    runner.start()
    started = [event[1:] for event in drain(events) if event[0] == "workflow_started"]
    assert started == pending


def test_runner_with_nothing_to_do_finishes_immediately(tmp_path):
    project = make_project(tmp_path, checks={check: False for check in CHECKS})
    events = queue.Queue()
    assert ProjectRunner(project, FakeService(), "m", events).start() == 0
    assert events.get(timeout=1) == ("workflow_finished", False)
