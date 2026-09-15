"""Tests for the statistics tables (core/statistics.py, no Tk, no model)."""

from core.models import ACCEPTED, PENDING, REJECTED
from core.statistics import (
    FREQUENT_LIMIT,
    acceptance_rate,
    per_thousand,
    project_statistics,
    statistics_markdown,
)
from core.workflow import (
    CHECK_AUTHOR,
    CHECK_EXPRESSION,
    CHECK_GRAMMAR,
    CHECK_SPELLING,
    Change,
    Chapter,
    CheckResult,
    Project,
    ProjectOptions,
    Segment,
)


def _segment(index, text, *proposals):
    """A segment with one change per (check, start, end, original, proposed, decision) tuple."""
    segment = Segment(index, text)
    for number, (check, start, end, original, proposed, decision) in enumerate(proposals, 1):
        result = segment.results.setdefault(check, CheckResult(check, "done", text))
        result.changes.append(Change("{0}-{1}".format(check, number), check, start, end, original, proposed,
                                     "why", decision))
    return segment


def _project(chapters):
    return Project("book", "book.txt", "2026-01-01T00:00:00", ProjectOptions(), chapters)


def make_statistics_project():
    # chapter 1: 10 words, 4 changes -> 400 per 1,000; chapter 2: 20 words, 1 change -> 50 per 1,000
    one = _segment(
        1, "Teh cat sat on teh mat and teh dog ran.",  # 10 words
        (CHECK_SPELLING, 0, 3, "Teh", "The", ACCEPTED),
        (CHECK_SPELLING, 15, 18, "teh", "the", ACCEPTED),
        (CHECK_SPELLING, 27, 30, "teh", "the", REJECTED),
        (CHECK_GRAMMAR, 4, 7, "cat", "cats", PENDING),
    )
    two = _segment(
        1, " ".join(["word"] * 19) + " teh",  # 20 words
        (CHECK_EXPRESSION, 95, 98, "teh", "the", PENDING),
    )
    return _project([Chapter(1, "One", "One\n\n", "\n", [one]), Chapter(2, "Two", "Two\n\n", "\n", [two])])


def test_per_thousand_and_acceptance_rate_math():
    assert per_thousand(4, 10) == 400.0
    assert per_thousand(1, 20) == 50.0
    assert per_thousand(3, 1000) == 3.0
    assert per_thousand(1, 3) == 333.3
    assert per_thousand(5, 0) == 0.0
    assert acceptance_rate(3, 1) == 0.75
    assert acceptance_rate(0, 0) == 0.0  # nothing decided, nothing divided by zero
    assert acceptance_rate(0, 4) == 0.0
    assert acceptance_rate(2, 0) == 1.0


def test_project_statistics_rows_checks_kinds_and_totals():
    stats = project_statistics(make_statistics_project())
    rows = stats["chapters"]
    assert [row["title"] for row in rows] == ["One", "Two"]
    assert (rows[0]["words"], rows[0]["changes"], rows[0]["per_1000"]) == (10, 4, 400.0)
    assert (rows[0]["accepted"], rows[0]["rejected"], rows[0]["pending"]) == (2, 1, 1)
    assert rows[0]["acceptance_rate"] == round(2 / 3, 3)
    assert rows[0]["per_check"][CHECK_SPELLING]["changes"] == 3
    assert rows[0]["per_check"][CHECK_GRAMMAR]["pending"] == 1
    assert (rows[1]["words"], rows[1]["changes"], rows[1]["per_1000"], rows[1]["acceptance_rate"]) == (20, 1, 50.0, 0.0)

    checks = stats["checks"]
    assert set(checks) >= {CHECK_SPELLING, CHECK_GRAMMAR, CHECK_EXPRESSION, CHECK_AUTHOR}
    assert (checks[CHECK_SPELLING]["changes"], checks[CHECK_SPELLING]["acceptance_rate"]) == (3, round(2 / 3, 3))
    assert checks[CHECK_SPELLING]["per_1000"] == 100.0  # 3 changes over 30 words
    assert checks[CHECK_GRAMMAR]["acceptance_rate"] == 0.0  # only a pending change: nothing decided
    assert checks[CHECK_AUTHOR]["changes"] == 0 and checks[CHECK_AUTHOR]["acceptance_rate"] == 0.0

    kinds = stats["kinds"]
    assert kinds["spelling"]["changes"] == 5  # the four teh/Teh -> the fixes and cat -> cats (distance 1)
    assert kinds["insertion"]["changes"] == 0
    assert kinds["spelling"]["acceptance_rate"] == round(2 / 3, 3)  # two pending, so 2 of 3 decided
    totals = stats["totals"]
    assert (totals["words"], totals["changes"], totals["accepted"], totals["rejected"], totals["pending"]) == (30, 5, 2, 1, 2)
    assert totals["per_1000"] == round(5 * 1000 / 30, 1)


def test_frequent_corrections_group_case_insensitively_and_skip_whitespace_changes():
    project = make_statistics_project()
    segment = project.chapters[0].segments[0]
    segment.results[CHECK_GRAMMAR].changes.append(Change("grammar-2", CHECK_GRAMMAR, 7, 8, " ", "  "))
    assert segment.results[CHECK_GRAMMAR].changes[-1].kind == "whitespace"
    stats = project_statistics(project)
    frequent = stats["frequent"]
    assert frequent[0]["original"].casefold() == "teh" and frequent[0]["proposed"] == "The"
    assert frequent[0]["count"] == 4  # Teh + teh + teh (spelling) + teh (expression), one group
    assert frequent[0]["original"] == "Teh"  # spelling of the first occurrence
    assert (frequent[0]["accepted"], frequent[0]["rejected"], frequent[0]["pending"]) == (2, 1, 1)
    assert frequent[0]["check"] == CHECK_SPELLING  # proposed it three of four times
    assert frequent[0]["first"] == (1, 1, "spelling-1")
    assert [item["original"] for item in frequent] == ["Teh", "cat"]  # the whitespace change is left out
    assert stats["kinds"]["whitespace"]["changes"] == 1  # ... but still counted as a change

    # the list is capped
    filler = project.chapters[1].segments[0]
    for number in range(FREQUENT_LIMIT + 5):
        filler.results[CHECK_EXPRESSION].changes.append(
            Change("expression-{0}".format(number + 10), CHECK_EXPRESSION, 0, 4, "word", "w{0}".format(number)))
    assert len(project_statistics(project)["frequent"]) == FREQUENT_LIMIT


def test_statistics_markdown_lists_the_tables_and_the_report_includes_them():
    project = make_statistics_project()
    markdown = statistics_markdown(project_statistics(project))
    assert markdown.startswith("## Statistics\n\n30 words, 5 changes (166.7 per 1,000 words), 2 accepted, 1 rejected, 2 pending; "
                               "acceptance rate 67%.")
    assert "| 1 | One | 10 | 4 | 400.0 | 2 | 1 | 1 |" in markdown
    assert "| 2 | Two | 20 | 1 | 50.0 | 0 | 0 | 1 |" in markdown
    assert "| Spelling | 3 | 100.0 | 2 | 1 | 0 | 67% |" in markdown
    assert "| Grammar | 1 | 33.3 | 0 | 0 | 1 | 0% |" in markdown
    assert "| Author |" not in markdown  # checks without changes are left out
    assert "| Spelling | 5 | 2 | 1 | 2 | 67% |" in markdown  # kinds table
    assert "| `Teh` | `The` | 4 | Spelling | 2 |" in markdown
    report = project.build_report()
    assert report.count("## Statistics") == 1 and report.rstrip().endswith("| `cat` | `cats` | 1 | Grammar | 0 |")

    empty = statistics_markdown(project_statistics(_project([])))
    assert "0 words, 0 changes (0.0 per 1,000 words)" in empty and "_No corrections yet._" in empty
