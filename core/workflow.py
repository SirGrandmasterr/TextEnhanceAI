"""Automatic manuscript review: checks, changes, merging, evaluation, persistence.

A *project* is one manuscript split into chapters and segments (see
``chunking``). Every segment is evaluated by up to three independent checks
(spelling, grammar, expression). Each check asks the model for the corrected
segment, diffs it against the original into word-level *changes* with exact
character offsets, and asks the model for a one-line explanation per change.

Because the checks run independently on the same original text, the author
can accept or reject each change in any order. When two accepted changes touch
the same words, the earlier check wins (spelling > grammar > expression) and
the other is reported as *superseded* instead of being applied.
"""

import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .backend import BackendUnavailable, EditCancelled, OutputTruncated
from .chunking import (
    DEFAULT_MAX_CHAPTER_CHARS,
    DEFAULT_MAX_CHARS,
    DEFAULT_TARGET_CHARS,
    chapters_from_outline,
    paragraph_outline,
    split_document,
    split_segments,
    word_count,
)
from .diff_engine import build_edit_session
from .models import ACCEPTED, PENDING, REJECTED, ReviewItem

PROJECT_FORMAT = 1
PROJECT_DIR_SUFFIX = ".teai"
PROJECT_FILE = "project.json"

CHECK_SPELLING = "spelling"
CHECK_GRAMMAR = "grammar"
CHECK_EXPRESSION = "expression"
CHECKS = (CHECK_SPELLING, CHECK_GRAMMAR, CHECK_EXPRESSION)  # also the priority order
CHECK_LABELS = {
    CHECK_SPELLING: "Spelling",
    CHECK_GRAMMAR: "Grammar",
    CHECK_EXPRESSION: "Expression",
}
CHECK_DESCRIPTIONS = {
    CHECK_SPELLING: "Typos, misspellings, capitalization, accents and umlauts.",
    CHECK_GRAMMAR: "Agreement, tense, cases, articles, commas and sentence structure.",
    CHECK_EXPRESSION: "Clearer, more natural wording where a phrase is awkward or vague.",
}
CHECK_INSTRUCTIONS = {
    CHECK_SPELLING: (
        "Correct spelling mistakes and typos only: misspelled words, wrong or "
        "swapped letters, missing or wrong accents and umlauts, and capitalization "
        "errors. Do not change punctuation, grammar, word choice, or sentence "
        "structure."
    ),
    CHECK_GRAMMAR: (
        "Fix grammar and punctuation errors only: agreement, tense, cases, articles, "
        "prepositions, missing or misplaced commas, sentence fragments, and run-on "
        "sentences. Keep the author's wording and style; do not rephrase sentences "
        "that are already correct."
    ),
    CHECK_EXPRESSION: (
        "Improve expression only where it clearly helps: replace awkward, repetitive, "
        "vague, or unidiomatic phrasing with clearer and more natural wording. "
        "Preserve the author's voice, tone, meaning, and rhythm; leave sentences that "
        "already read well untouched, and never add new content."
    ),
}
FALLBACK_EXPLANATIONS = {
    CHECK_SPELLING: "Spelling correction.",
    CHECK_GRAMMAR: "Grammar or punctuation fix.",
    CHECK_EXPRESSION: "Clearer, more natural expression.",
}
EXPLANATION_SYSTEM_PROMPT = (
    "You are an editor explaining proposed corrections to an author. Answer with a "
    "single JSON object and nothing else."
)
SAME_LANGUAGE = "same as text"

STATUS_QUEUED = "queued"
STATUS_ERROR = "error"
STATUS_CLEAN = "clean"
STATUS_READY = "ready"
STATUS_REVIEWED = "reviewed"

STATE_APPLIED = "applied"
STATE_SUPERSEDED = "superseded"
STATE_REJECTED = "rejected"
STATE_PENDING = "pending"

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$", re.DOTALL)


# ------------------------------------------------------------------ models
@dataclass
class Change:
    """One word-level edit proposed by a check, anchored in the segment text."""

    change_id: str
    check: str
    start: int
    end: int
    original_text: str
    proposed_text: str
    explanation: str = ""
    decision: str = PENDING

    @property
    def priority(self):
        return CHECKS.index(self.check)

    def to_dict(self):
        return {
            "id": self.change_id,
            "check": self.check,
            "start": self.start,
            "end": self.end,
            "original": self.original_text,
            "proposed": self.proposed_text,
            "explanation": self.explanation,
            "decision": self.decision,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            data["id"], data["check"], int(data["start"]), int(data["end"]),
            data.get("original", ""), data.get("proposed", ""),
            data.get("explanation", ""), data.get("decision", PENDING),
        )


@dataclass
class CheckResult:
    """Outcome of running one check on one segment."""

    check: str
    status: str  # "done" or "error"
    proposed_text: str = ""
    changes: List[Change] = field(default_factory=list)
    error: str = ""
    model: str = ""
    duration: float = 0.0
    explained: bool = False

    def to_dict(self):
        return {
            "check": self.check,
            "status": self.status,
            "proposed": self.proposed_text,
            "changes": [change.to_dict() for change in self.changes],
            "error": self.error,
            "model": self.model,
            "duration": round(self.duration, 2),
            "explained": self.explained,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            data["check"], data.get("status", "done"), data.get("proposed", ""),
            [Change.from_dict(item) for item in data.get("changes", [])],
            data.get("error", ""), data.get("model", ""), float(data.get("duration", 0.0)),
            bool(data.get("explained", False)),
        )


@dataclass
class Segment:
    """A reviewable chunk with per-check results and decisions."""

    index: int
    text: str
    trailing: str = ""
    results: Dict[str, CheckResult] = field(default_factory=dict)

    @property
    def is_blank(self):
        return not self.text.strip()

    def changes(self, enabled):
        """Return changes of enabled, successful checks sorted by position."""
        found = []
        for check in CHECKS:
            if not enabled.get(check):
                continue
            result = self.results.get(check)
            if result and result.status == "done":
                found.extend(result.changes)
        return sorted(found, key=lambda change: (change.start, change.priority, change.end))

    def find_change(self, change_id):
        for result in self.results.values():
            for change in result.changes:
                if change.change_id == change_id:
                    return change
        return None

    def status(self, enabled):
        """Summarise where this segment stands for the enabled checks."""
        if self.is_blank:
            return STATUS_CLEAN
        needed = [check for check in CHECKS if enabled.get(check)]
        if any(check not in self.results for check in needed):
            return STATUS_QUEUED
        if any(self.results[check].status == "error" for check in needed):
            return STATUS_ERROR
        changes = self.changes(enabled)
        if not changes:
            return STATUS_CLEAN
        if any(change.decision == PENDING for change in changes):
            return STATUS_READY
        return STATUS_REVIEWED

    def to_dict(self):
        return {
            "index": self.index,
            "text": self.text,
            "trailing": self.trailing,
            "results": {check: result.to_dict() for check, result in self.results.items()},
        }

    @classmethod
    def from_dict(cls, data):
        segment = cls(int(data["index"]), data["text"], data.get("trailing", ""))
        for check, result in (data.get("results") or {}).items():
            segment.results[check] = CheckResult.from_dict(result)
        return segment


@dataclass
class Chapter:
    index: int
    title: str
    heading: str
    trailing: str = ""
    segments: List[Segment] = field(default_factory=list)

    @property
    def body(self):
        return "".join(segment.text + segment.trailing for segment in self.segments)

    @property
    def file_name(self):
        safe = re.sub(r"[^\w\- ]+", "", self.title, flags=re.UNICODE).strip() or "chapter"
        return "{0:02d} - {1}.txt".format(self.index, safe[:60])

    def to_dict(self):
        return {
            "index": self.index,
            "title": self.title,
            "heading": self.heading,
            "trailing": self.trailing,
            "segments": [segment.to_dict() for segment in self.segments],
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            int(data["index"]), data["title"], data.get("heading", ""), data.get("trailing", ""),
            [Segment.from_dict(item) for item in data.get("segments", [])],
        )


@dataclass
class ProjectOptions:
    checks: Dict[str, bool] = field(default_factory=lambda: {check: True for check in CHECKS})
    auto_accept: Dict[str, bool] = field(default_factory=lambda: {check: False for check in CHECKS})
    target_chars: int = DEFAULT_TARGET_CHARS
    max_chars: int = DEFAULT_MAX_CHARS
    max_chapter_chars: int = DEFAULT_MAX_CHAPTER_CHARS
    chapter_mode: str = "auto"  # auto | size | single | model
    explain: bool = True
    language: str = SAME_LANGUAGE
    parallelism: int = 2

    def enabled_checks(self):
        return [check for check in CHECKS if self.checks.get(check)]

    def to_dict(self):
        return {
            "checks": dict(self.checks),
            "auto_accept": dict(self.auto_accept),
            "target_chars": self.target_chars,
            "max_chars": self.max_chars,
            "max_chapter_chars": self.max_chapter_chars,
            "chapter_mode": self.chapter_mode,
            "explain": self.explain,
            "language": self.language,
            "parallelism": self.parallelism,
        }

    @classmethod
    def from_dict(cls, data):
        options = cls()
        for key, value in (data or {}).items():
            if key in ("checks", "auto_accept"):
                merged = getattr(options, key)
                merged.update({k: bool(v) for k, v in (value or {}).items() if k in CHECKS})
            elif hasattr(options, key):
                setattr(options, key, value)
        return options


@dataclass
class Project:
    """One manuscript under automatic review."""

    name: str
    source_path: str
    created_at: str
    options: ProjectOptions
    chapters: List[Chapter]
    model: str = ""
    backend: str = ""
    method: str = ""
    root: Optional[Path] = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------ queries
    def all_segments(self):
        """Yield (chapter, segment) pairs in document order."""
        for chapter in self.chapters:
            for segment in chapter.segments:
                yield chapter, segment

    @property
    def enabled(self):
        return self.options.checks

    def pending_tasks(self):
        """Return (chapter_index, segment_index, check) tuples still to evaluate."""
        tasks = []
        for chapter in self.chapters:
            for segment in chapter.segments:
                if segment.is_blank:
                    continue
                for check in self.options.enabled_checks():
                    result = segment.results.get(check)
                    if result is None or result.status == "error":
                        tasks.append((chapter.index, segment.index, check))
        return tasks

    def find(self, chapter_index, segment_index):
        for chapter in self.chapters:
            if chapter.index == chapter_index:
                for segment in chapter.segments:
                    if segment.index == segment_index:
                        return chapter, segment
        return None, None

    def progress(self):
        """Return counters for progress displays and reports."""
        enabled = self.enabled
        stats = {
            "segments": 0, "queued": 0, "error": 0, "clean": 0, "ready": 0, "reviewed": 0,
            "tasks_total": 0, "tasks_done": 0, "changes": 0, "accepted": 0, "rejected": 0, "pending": 0,
            "words": 0, "per_check": {check: {"changes": 0, "accepted": 0, "rejected": 0} for check in CHECKS},
        }
        checks = self.options.enabled_checks()
        for _, segment in self.all_segments():
            stats["segments"] += 1
            stats["words"] += word_count(segment.text)
            stats[segment.status(enabled)] += 1
            if segment.is_blank:
                continue
            stats["tasks_total"] += len(checks)
            stats["tasks_done"] += sum(
                1 for check in checks
                if check in segment.results and segment.results[check].status == "done"
            )
            for change in segment.changes(enabled):
                stats["changes"] += 1
                stats["per_check"][change.check]["changes"] += 1
                if change.decision == ACCEPTED:
                    stats["accepted"] += 1
                    stats["per_check"][change.check]["accepted"] += 1
                elif change.decision == REJECTED:
                    stats["rejected"] += 1
                    stats["per_check"][change.check]["rejected"] += 1
                else:
                    stats["pending"] += 1
        return stats

    # -------------------------------------------------------- persistence
    @property
    def project_file(self):
        return self.root / PROJECT_FILE if self.root else None

    def to_dict(self):
        return {
            "format": PROJECT_FORMAT,
            "name": self.name,
            "source_path": self.source_path,
            "created_at": self.created_at,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "model": self.model,
            "backend": self.backend,
            "method": self.method,
            "options": self.options.to_dict(),
            "chapters": [chapter.to_dict() for chapter in self.chapters],
        }

    @classmethod
    def from_dict(cls, data, root=None):
        if data.get("format", PROJECT_FORMAT) > PROJECT_FORMAT:
            raise ValueError("This project was saved by a newer TextEnhanceAI version.")
        return cls(
            data["name"], data.get("source_path", ""), data.get("created_at", ""),
            ProjectOptions.from_dict(data.get("options")),
            [Chapter.from_dict(item) for item in data.get("chapters", [])],
            data.get("model", ""), data.get("backend", ""), data.get("method", ""), root,
        )

    def save(self):
        """Write project.json atomically (safe against crashes mid-write)."""
        if self.root is None:
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.project_file
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(str(temporary), str(path))
        return path

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.is_dir():
            path = path / PROJECT_FILE
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data, root=path.parent)

    def write_chapter_files(self):
        """Write each chapter's original text as its own file (chapters/NN - Title.txt)."""
        directory = self.root / "chapters"
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for chapter in self.chapters:
            path = directory / chapter.file_name
            path.write_text(chapter.heading + chapter.body + chapter.trailing, encoding="utf-8")
            paths.append(path)
        return paths

    # -------------------------------------------------------------- export
    def render_chapter(self, chapter):
        return chapter.heading + "".join(
            render_segment(segment, self.enabled) + segment.trailing for segment in chapter.segments
        ) + chapter.trailing

    def render_document(self):
        return "".join(self.render_chapter(chapter) for chapter in self.chapters)

    def export(self):
        """Write reviewed chapters, the combined document and a Markdown report."""
        reviewed = self.root / "reviewed"
        reviewed.mkdir(parents=True, exist_ok=True)
        written = []
        for chapter in self.chapters:
            path = reviewed / chapter.file_name
            path.write_text(self.render_chapter(chapter), encoding="utf-8")
            written.append(path)
        combined = self.root / "{0}-reviewed.txt".format(self.name)
        combined.write_text(self.render_document(), encoding="utf-8")
        report = self.root / "report.md"
        report.write_text(self.build_report(), encoding="utf-8")
        return {"chapters": written, "document": combined, "report": report}

    def build_report(self):
        stats = self.progress()
        lines = [
            "# Review report: {0}\n".format(self.name),
            "- Source: `{0}`".format(self.source_path),
            "- Model: `{0}` ({1})".format(self.model or "?", self.backend or "?"),
            "- Checks: {0}".format(", ".join(CHECK_LABELS[c] for c in self.options.enabled_checks()) or "none"),
            "- Chapters: {0} ({1}), segments: {2}, words: {3}".format(
                len(self.chapters), self.method or "?", stats["segments"], stats["words"]
            ),
            "- Changes: {0} proposed, {1} accepted, {2} rejected, {3} pending".format(
                stats["changes"], stats["accepted"], stats["rejected"], stats["pending"]
            ),
        ]
        for check in self.options.enabled_checks():
            per = stats["per_check"][check]
            lines.append("  - {0}: {1} proposed, {2} accepted, {3} rejected".format(
                CHECK_LABELS[check], per["changes"], per["accepted"], per["rejected"]
            ))
        lines.append("")
        for chapter in self.chapters:
            lines.append("## {0}. {1}\n".format(chapter.index, chapter.title))
            for segment in chapter.segments:
                changes = segment.changes(self.enabled)
                status = segment.status(self.enabled)
                if not changes:
                    lines.append("- Segment {0}: {1}".format(segment.index, status))
                    continue
                states = change_states(segment, self.enabled)
                lines.append("- Segment {0}: {1} change(s), {2}".format(segment.index, len(changes), status))
                for change in changes:
                    lines.append("  - [{0}] `{1}` → `{2}` — {3} — {4}".format(
                        CHECK_LABELS[change.check],
                        _inline(change.original_text), _inline(change.proposed_text),
                        states[change.change_id], change.explanation or FALLBACK_EXPLANATIONS[change.check],
                    ))
            lines.append("")
        return "\n".join(lines)


def _inline(text):
    text = text.replace("`", "'").replace("\n", "↵")
    return text if text else "∅"


# ------------------------------------------------------------ diff/merge
def extract_changes(original, proposed, check):
    """Diff ``proposed`` against ``original`` into offset-anchored changes."""
    session = build_edit_session(original, proposed)
    changes = []
    number = 0
    for part in session.parts:
        if not isinstance(part, ReviewItem):
            continue
        position = part.original_start
        for hunk in part.hunks:
            length = len(hunk.original_text)
            if hunk.is_change:
                number += 1
                changes.append(
                    Change(
                        "{0}-{1}".format(check, number), check, position, position + length,
                        hunk.original_text, hunk.proposed_text,
                    )
                )
            position += length
    return changes


def overlaps(a, b):
    """Return whether two changes touch the same characters (or the same insertion point)."""
    if a.start == a.end and b.start == b.end:
        return a.start == b.start
    return a.start < b.end and b.start < a.end


def applied_changes(segment, enabled):
    """Return the accepted changes that will actually be applied, in text order."""
    accepted = [change for change in segment.changes(enabled) if change.decision == ACCEPTED]
    accepted.sort(key=lambda change: (change.priority, change.start))
    kept = []
    for change in accepted:
        if not any(overlaps(change, other) for other in kept):
            kept.append(change)
    kept.sort(key=lambda change: (change.start, change.end))
    return kept


def change_states(segment, enabled):
    """Map change ids to applied / superseded / rejected / pending."""
    applied = {change.change_id for change in applied_changes(segment, enabled)}
    states = {}
    for change in segment.changes(enabled):
        if change.decision == REJECTED:
            states[change.change_id] = STATE_REJECTED
        elif change.decision == ACCEPTED:
            states[change.change_id] = STATE_APPLIED if change.change_id in applied else STATE_SUPERSEDED
        else:
            states[change.change_id] = STATE_PENDING
    return states


def render_segment(segment, enabled):
    """Apply the effective changes to the segment text."""
    output = []
    position = 0
    for change in applied_changes(segment, enabled):
        output.append(segment.text[position:change.start])
        output.append(change.proposed_text)
        position = change.end
    output.append(segment.text[position:])
    return "".join(output)


# ------------------------------------------------------------- creation
def create_project(source_path, text, options, model="", backend="", root=None):
    """Split a manuscript and return an unevaluated project."""
    source_path = Path(source_path)
    result = split_document(
        text, mode="auto" if options.chapter_mode == "model" else options.chapter_mode,
        target_chars=options.target_chars, max_chars=options.max_chars,
        max_chapter_chars=options.max_chapter_chars,
    )
    chapters = [
        Chapter(
            chapter.index, chapter.title, chapter.heading, chapter.trailing,
            [Segment(segment.index, segment.text, segment.trailing) for segment in chapter.segments],
        )
        for chapter in result.chapters
    ]
    project = Project(
        source_path.stem, str(source_path), datetime.now().isoformat(timespec="seconds"),
        options, chapters, model, backend, result.method,
        root=Path(root) if root else source_path.with_name(source_path.stem + PROJECT_DIR_SUFFIX),
    )
    return project


def apply_model_outline(project, text, starts, titles=None):
    """Replace the chapters using boundaries proposed by the model."""
    result = chapters_from_outline(text, starts, titles)
    project.chapters = []
    for chapter in result.chapters:
        segments = split_segments(chapter.body, project.options.target_chars, project.options.max_chars)
        project.chapters.append(
            Chapter(
                chapter.index, chapter.title, chapter.heading, chapter.trailing,
                [Segment(segment.index, segment.text, segment.trailing) for segment in segments],
            )
        )
    project.method = result.method
    return project


def build_outline_messages(text):
    """Ask the model where chapters start (used when no headings were found)."""
    outline = paragraph_outline(text)
    listing = "\n".join("{0}: {1}".format(index, preview) for index, preview in outline)
    return [
        {"role": "system", "content": EXPLANATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Below is a numbered outline of the paragraphs of a manuscript (first "
                "characters of each paragraph). Identify where new chapters or major "
                "sections begin. Return JSON: {\"chapters\": [{\"start\": <paragraph "
                "number>, \"title\": \"short title\"}, ...]}. The first chapter starts at "
                "paragraph 0. Prefer clear structural breaks; return at most 60 chapters.\n\n"
                + listing
            ),
        },
    ]


def parse_outline(text):
    """Return ([starts], [titles]) from the model's JSON answer."""
    data = _parse_json_object(text)
    starts, titles = [], []
    for item in (data or {}).get("chapters", []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("start"), int):
            starts.append(item["start"])
            titles.append(str(item.get("title", "")).strip())
    return starts, titles


# ---------------------------------------------------------- explanations
def _context(text, start, end, radius=40):
    before = text[max(0, start - radius):start].replace("\n", " ")
    after = text[end:end + radius].replace("\n", " ")
    return before, after


def build_explanation_messages(segment_text, changes, check, language=SAME_LANGUAGE):
    """Build the prompt asking for one short explanation per change."""
    lines = []
    for number, change in enumerate(changes, 1):
        before, after = _context(segment_text, change.start, change.end)
        lines.append(
            "{0}. \"{1}\" → \"{2}\"   (context: …{3}[{1}]{4}…)".format(
                number, change.original_text, change.proposed_text, before, after
            )
        )
    if language == SAME_LANGUAGE:
        language_clause = "in the same language as the text"
    else:
        language_clause = "in " + language
    return [
        {"role": "system", "content": EXPLANATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "A {0} check proposed the changes listed below for the text. For each "
                "numbered change, write one short explanation (at most 15 words, {1}) of "
                "why the new version is better. Return a JSON object mapping the change "
                "number to its explanation, e.g. {{\"1\": \"...\", \"2\": \"...\"}}.\n\n"
                "Check: {2}\n\nText:\n{3}\n\nChanges:\n{4}"
            ).format(
                CHECK_LABELS[check].lower(), language_clause, CHECK_DESCRIPTIONS[check],
                segment_text, "\n".join(lines),
            ),
        },
    ]


def _parse_json_object(text):
    text = strip_fences(text or "")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def parse_explanations(text):
    """Return {change_number: explanation} from the model's answer (best effort)."""
    data = _parse_json_object(text)
    explanations = {}
    if isinstance(data, dict):
        items = data.get("explanations", data) if isinstance(data.get("explanations", None), (dict, list)) else data
        if isinstance(items, list):
            for number, item in enumerate(items, 1):
                if isinstance(item, str):
                    explanations[number] = item.strip()
                elif isinstance(item, dict):
                    value = item.get("explanation") or item.get("reason") or ""
                    explanations[number] = str(value).strip()
        else:
            for key, value in items.items():
                try:
                    number = int(str(key).strip())
                except ValueError:
                    continue
                if isinstance(value, dict):
                    value = value.get("explanation") or value.get("reason") or ""
                if isinstance(value, str) and value.strip():
                    explanations[number] = value.strip()
    return {number: value[:200] for number, value in explanations.items() if value}


def strip_fences(text):
    """Remove a Markdown code fence wrapping the whole answer."""
    match = _FENCE.match(text)
    return match.group(1) if match else text


# ------------------------------------------------------------ evaluation
class ProposalRejected(Exception):
    """The model's answer is not a plausible edit of the segment."""


def sanity_check_proposal(original, proposed):
    """Reject answers that are clearly not the edited text (summaries, refusals)."""
    original_length = max(1, len(original.strip()))
    proposed_length = len(proposed.strip())
    if proposed_length == 0:
        raise ProposalRejected("The model returned an empty answer.")
    ratio = proposed_length / original_length
    if ratio < 0.5 or ratio > 2.0:
        raise ProposalRejected(
            "The model did not return the complete edited text ({0:.0%} of the original length).".format(ratio)
        )


def run_check(service, model, text, check, cancel_event, explain=True, language=SAME_LANGUAGE, retries=1):
    """Evaluate one check on one segment; returns a CheckResult (never raises except on cancel)."""
    started = time.time()
    attempt = 0
    while True:
        try:
            proposed = strip_fences(
                service.stream_edit(model, CHECK_INSTRUCTIONS[check], text, cancel_event)
            ).strip("\n")
            sanity_check_proposal(text, proposed)
            break
        except EditCancelled:
            raise
        except (BackendUnavailable, ProposalRejected) as exc:
            attempt += 1
            if isinstance(exc, OutputTruncated) or attempt > retries:
                return CheckResult(check, "error", error=str(exc), model=model, duration=time.time() - started)
            if cancel_event.wait(1.5):
                raise EditCancelled("Editing was cancelled.")

    changes = extract_changes(text, proposed, check)
    explained = False
    explanations = {}
    if changes and explain:
        try:
            answer = service.generate(
                model, build_explanation_messages(text, changes, check, language), cancel_event, max_tokens=2048
            )
            explanations = parse_explanations(answer)
            explained = bool(explanations)
        except EditCancelled:
            raise
        except Exception:  # explanations are best effort
            explanations = {}
    for number, change in enumerate(changes, 1):
        change.explanation = explanations.get(number) or FALLBACK_EXPLANATIONS[check]
    return CheckResult(check, "done", proposed, changes, model=model, duration=time.time() - started, explained=explained)


class ProjectRunner:
    """Evaluate every missing (segment, check) pair on background threads.

    Events: ``("workflow_started", chapter_index, segment_index, check)`` when
    a task begins, ``("workflow_result", chapter_index, segment_index, check,
    CheckResult)`` followed by ``("workflow_progress", done, total, running)``
    when it ends, and finally ``("workflow_finished", cancelled)``. The caller applies results to the
    project on its own thread, so the runner never mutates project state.
    """

    def __init__(self, project, service, model, events, parallelism=None):
        self.project = project
        self.service = service
        self.model = model
        self.events = events
        self.parallelism = max(1, int(parallelism or project.options.parallelism or 1))
        self.cancel_event = threading.Event()
        self.tasks = queue.Queue()
        self.total = 0
        self.done = 0
        self.failed = 0
        self.running = 0
        self._active_workers = 0
        self._lock = threading.Lock()
        self._threads = []
        self.started_at = None

    @property
    def active(self):
        return any(thread.is_alive() for thread in self._threads)

    def start(self):
        pending = self.project.pending_tasks()
        self.total = len(pending)
        self.started_at = time.time()
        for task in pending:
            self.tasks.put(task)
        if not pending:
            self.events.put(("workflow_finished", False))
            return 0
        workers = min(self.parallelism, len(pending))
        self._active_workers = workers
        for number in range(workers):
            thread = threading.Thread(target=self._worker, name="teai-eval-{0}".format(number), daemon=True)
            self._threads.append(thread)
            thread.start()
        return len(pending)

    def cancel(self):
        self.cancel_event.set()

    def _worker(self):
        options = self.project.options
        try:
            while not self.cancel_event.is_set():
                try:
                    chapter_index, segment_index, check = self.tasks.get_nowait()
                except queue.Empty:
                    break
                _, segment = self.project.find(chapter_index, segment_index)
                if segment is None:
                    continue
                with self._lock:
                    self.running += 1
                self.events.put(("workflow_started", chapter_index, segment_index, check))
                try:
                    result = run_check(
                        self.service, self.model, segment.text, check, self.cancel_event,
                        explain=options.explain, language=options.language,
                    )
                except EditCancelled:
                    with self._lock:
                        self.running -= 1
                    break
                except Exception as exc:  # defensive: a crash must not kill the worker
                    result = CheckResult(check, "error", error="Unexpected error: {0!r}".format(exc), model=self.model)
                with self._lock:
                    self.running -= 1
                    self.done += 1
                    if result.status == "error":
                        self.failed += 1
                    done, running = self.done, self.running
                self.events.put(("workflow_result", chapter_index, segment_index, check, result))
                self.events.put(("workflow_progress", done, self.total, running))
        finally:
            with self._lock:
                self._active_workers -= 1
                last = self._active_workers == 0
            if last:
                self.events.put(("workflow_finished", self.cancel_event.is_set()))

    def eta_seconds(self):
        """Rough remaining time based on the throughput so far."""
        if not self.started_at or not self.done:
            return None
        elapsed = time.time() - self.started_at
        remaining = self.total - self.done
        return elapsed / self.done * remaining


def apply_result(project, chapter_index, segment_index, check, result):
    """Store a result on the project, honouring auto-accept options."""
    _, segment = project.find(chapter_index, segment_index)
    if segment is None:
        return None
    if result.status == "done" and project.options.auto_accept.get(check):
        for change in result.changes:
            change.decision = ACCEPTED
    segment.results[check] = result
    return segment
