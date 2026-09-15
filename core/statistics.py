"""Where the manuscript is weak and which checks earn their cost.

Pure functions over a ``workflow.Project``: no Tk, no model. The tables are
shown in the Statistics window and appended to ``report.md``.
"""

from .change_kinds import CHANGE_KIND_LABELS, CHANGE_KINDS, KIND_WHITESPACE, collapse_whitespace
from .chunking import word_count
from .models import ACCEPTED, REJECTED
from .workflow import ALL_CHECKS, CHECK_LABELS  # workflow.build_report imports this module lazily

FREQUENT_LIMIT = 20


def _counter():
    return {"changes": 0, "accepted": 0, "rejected": 0, "pending": 0}


def _count(counter, change):
    counter["changes"] += 1
    if change.decision == ACCEPTED:
        counter["accepted"] += 1
    elif change.decision == REJECTED:
        counter["rejected"] += 1
    else:
        counter["pending"] += 1


def per_thousand(changes, words):
    """Changes per 1,000 words, rounded to one decimal; 0.0 without words."""
    if not words:
        return 0.0
    return round(changes * 1000.0 / words, 1)


def acceptance_rate(accepted, rejected):
    """Share of decided changes that were accepted (0.0-1.0); 0.0 when nothing was decided."""
    decided = accepted + rejected
    if not decided:
        return 0.0
    return round(accepted / float(decided), 3)


def _finish(counter, words=None):
    counter["acceptance_rate"] = acceptance_rate(counter["accepted"], counter["rejected"])
    if words is not None:
        counter["per_1000"] = per_thousand(counter["changes"], words)
    return counter


def project_statistics(project):
    """Return per-chapter rows, per-check and per-kind totals and the most frequent corrections.

    ``chapters``: one row per chapter with words, changes, changes per 1,000
    words, accepted/rejected/pending counts and a ``per_check`` breakdown.
    ``checks`` / ``kinds``: totals per check (``ALL_CHECKS`` keys) and per
    kind with ``acceptance_rate`` (accepted / decided) and, for checks,
    ``per_1000`` over the whole manuscript. ``frequent``: the top
    ``FREQUENT_LIMIT`` (original -> proposed) pairs, grouped
    case-insensitively with whitespace collapsed, whitespace-only changes
    left out; each with ``count``, the ``check`` that proposed it most often,
    how many were accepted and the ``first`` occurrence as
    ``(chapter_index, segment_index, change_id)``.
    """
    enabled = project.enabled
    checks = {check: _counter() for check in ALL_CHECKS}
    kinds = {kind: _counter() for kind in CHANGE_KINDS}
    groups = {}
    chapters = []
    total_words = 0
    totals = _counter()
    for chapter in project.chapters:
        row = _counter()
        row.update({"index": chapter.index, "title": chapter.title, "words": 0,
                    "per_check": {check: _counter() for check in ALL_CHECKS}})
        for segment in chapter.segments:
            row["words"] += word_count(segment.text)
            for change in segment.changes(enabled):
                _count(row, change)
                _count(row["per_check"][change.check], change)
                _count(checks[change.check], change)
                _count(kinds[change.kind], change)
                _count(totals, change)
                if change.kind == KIND_WHITESPACE:
                    continue
                key = (collapse_whitespace(change.original_text).casefold(),
                       collapse_whitespace(change.proposed_text).casefold())
                group = groups.get(key)
                if group is None:
                    group = groups[key] = {
                        "original": collapse_whitespace(change.original_text),
                        "proposed": collapse_whitespace(change.proposed_text),
                        "count": 0, "accepted": 0, "rejected": 0, "pending": 0, "checks": {},
                        "first": (chapter.index, segment.index, change.change_id),
                    }
                group["count"] += 1
                group["checks"][change.check] = group["checks"].get(change.check, 0) + 1
                if change.decision == ACCEPTED:
                    group["accepted"] += 1
                elif change.decision == REJECTED:
                    group["rejected"] += 1
                else:
                    group["pending"] += 1
        total_words += row["words"]
        row["per_1000"] = per_thousand(row["changes"], row["words"])
        row["acceptance_rate"] = acceptance_rate(row["accepted"], row["rejected"])
        for counter in row["per_check"].values():
            _finish(counter)
        chapters.append(row)
    for counter in checks.values():
        _finish(counter, total_words)
    for counter in kinds.values():
        _finish(counter)
    _finish(totals, total_words)
    totals["words"] = total_words

    frequent = []
    for group in sorted(groups.values(), key=lambda g: (-g["count"], g["original"].casefold(), g["proposed"].casefold())):
        check = max(group["checks"].items(), key=lambda item: (item[1], -ALL_CHECKS.index(item[0])))[0]
        frequent.append({
            "original": group["original"], "proposed": group["proposed"], "count": group["count"],
            "check": check, "accepted": group["accepted"], "rejected": group["rejected"],
            "pending": group["pending"], "first": group["first"],
        })
        if len(frequent) >= FREQUENT_LIMIT:
            break
    return {"chapters": chapters, "checks": checks, "kinds": kinds, "frequent": frequent, "totals": totals}


def _percent(rate):
    return "{0:.0f}%".format(rate * 100)


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def statistics_markdown(stats, check_labels=None, kind_labels=None):
    """Render the statistics as Markdown tables (chapters, checks, kinds, frequent corrections)."""
    check_labels = check_labels or CHECK_LABELS
    kind_labels = kind_labels or CHANGE_KIND_LABELS
    lines = ["## Statistics", ""]
    totals = stats["totals"]
    lines.append("{0:,} words, {1} changes ({2} per 1,000 words), {3} accepted, {4} rejected, {5} pending; "
                 "acceptance rate {6}.".format(totals["words"], totals["changes"], totals["per_1000"],
                                              totals["accepted"], totals["rejected"], totals["pending"],
                                              _percent(totals["acceptance_rate"])))
    lines.append("")
    lines.append("### Chapters")
    lines.append("")
    lines.append("| # | Chapter | Words | Changes | per 1,000 | Accepted | Rejected | Pending |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for row in stats["chapters"]:
        lines.append("| {0} | {1} | {2:,} | {3} | {4} | {5} | {6} | {7} |".format(
            row["index"], _cell(row["title"]), row["words"], row["changes"], row["per_1000"],
            row["accepted"], row["rejected"], row["pending"]))
    lines.append("")
    lines.append("### Checks")
    lines.append("")
    lines.append("| Check | Changes | per 1,000 | Accepted | Rejected | Pending | Acceptance |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for check, counter in stats["checks"].items():
        if not counter["changes"]:
            continue
        lines.append("| {0} | {1} | {2} | {3} | {4} | {5} | {6} |".format(
            check_labels.get(check, check), counter["changes"], counter["per_1000"], counter["accepted"],
            counter["rejected"], counter["pending"], _percent(counter["acceptance_rate"])))
    lines.append("")
    lines.append("### Kinds of edits")
    lines.append("")
    lines.append("| Kind | Changes | Accepted | Rejected | Pending | Acceptance |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for kind, counter in stats["kinds"].items():
        if not counter["changes"]:
            continue
        lines.append("| {0} | {1} | {2} | {3} | {4} | {5} |".format(
            kind_labels.get(kind, kind), counter["changes"], counter["accepted"], counter["rejected"],
            counter["pending"], _percent(counter["acceptance_rate"])))
    lines.append("")
    lines.append("### Most frequent corrections")
    lines.append("")
    if stats["frequent"]:
        lines.append("| Original | Proposed | Count | Check | Accepted |")
        lines.append("|---|---|---:|---|---:|")
        for item in stats["frequent"]:
            lines.append("| `{0}` | `{1}` | {2} | {3} | {4} |".format(
                _cell(item["original"]) or "\u2205", _cell(item["proposed"]) or "\u2205", item["count"],
                check_labels.get(item["check"], item["check"]), item["accepted"]))
    else:
        lines.append("_No corrections yet._")
    return "\n".join(lines)
