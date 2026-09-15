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

from .backend import BackendUnavailable, EditCancelled, OutputTruncated, StructuredOutputUnsupported
from .change_kinds import CHANGE_KIND_LABELS, CHANGE_KINDS, classify_change
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
        "that are already correct. Do not rewrite sentences."
    ),
    CHECK_EXPRESSION: (
        "Improve expression only where it clearly helps: replace awkward, repetitive, "
        "vague, or unidiomatic phrasing with clearer and more natural wording. "
        "Preserve the author's voice, tone, meaning, and rhythm; leave sentences that "
        "already read well untouched, and never add new content. Never add facts, "
        "names, clauses or sentences that are not already in the text."
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

# Standing instructions from the author, appended to every check's instruction
# and to the explanation prompt (see ``format_style_guide``).
STYLE_GUIDE_MAX_CHARS = 1500
STYLE_GUIDE_HEADER = (
    "Author's instructions (they take precedence over the rules above where they conflict):"
)

# Protected terms (names, invented words, technical terms). They are listed in
# the prompt up to these caps; beyond them only the post-filter protects them.
GLOSSARY_MAX_TERMS = 200
GLOSSARY_MAX_CHARS = 2000
GLOSSARY_HEADER = "Protected terms — never change their spelling, capitalization or form: "
GLOSSARY_CONTEXT = 20  # characters around an insertion point checked against the glossary
GLOSSARY_EXPLANATION = "Suppressed: touches a protected term."

# Hallucination guard: heuristics that mark a change as possibly inventing
# content rather than correcting it (see ``flag_suspicious``).
FLAG_GROWTH = "growth"
FLAG_NOVEL_WORDS = "novel_words"
FLAG_REWRITE_IN_STRICT_CHECK = "rewrite_in_strict_check"
FLAG_LABELS = {
    FLAG_GROWTH: "The replacement is much longer than the text it replaces.",
    FLAG_NOVEL_WORDS: "Adds words that appear nowhere else in the segment.",
    FLAG_REWRITE_IN_STRICT_CHECK: "A spelling or grammar check rewrote a whole phrase.",
}
FLAG_REPORT_MARK = "\u26a0 possibly invented"
GROWTH_FACTOR = 1.6
GROWTH_SLACK = 12
NOVEL_MIN_WORDS = 2
NOVEL_MIN_LENGTH = 4
STRICT_CHECKS = (CHECK_SPELLING, CHECK_GRAMMAR)
# Function words (>= 4 letters) that never count as "novel" content, German and English.
STOPWORDS = frozenset("""
about above after again against alone along already also although always among another anyone anything
around because been before being below between both cannot could does doing done down during each either
else enough even ever every everything from further having here hers herself himself however into itself
just last less like many might more most much must myself neither never nevertheless next nobody none
nothing often once only other ours ourselves over perhaps quite rather really same several shall should
since some something sometimes soon still such than that their theirs them themselves then there these
they things this those though through thus toward towards under until upon very were what whatever when
whenever where whether which while whom whose will with within without would yours yourself
aber alle allem allen aller allerdings alles also andere anderem anderen anderer anderes auch aufs beide
beiden beim bereits bevor bloss bloß dabei dadurch dafür dagegen daher damit danach dann daran darauf
daraus darin darum darunter dass davon dazu dein deine deinem deinen deiner deines denen denn dennoch deren
derer deshalb dessen desto dich diese diesem diesen dieser dieses doch dort durch eben eher eine einem einen
einer eines einige einigen einiger einiges einmal entweder erst etwa etwas euch euer eure eurem euren eurer
eures falls ganz gegen gern gewesen haben habt hast hatte hatten hätte hätten hier hinter ihre ihrem ihren
ihrer ihres immer indem innerhalb irgend jede jedem jeden jeder jedes jedoch jene jenem jenen jener jenes
jetzt kann kannst kaum kein keine keinem keinen keiner keines können könnt könnte könnten machen mehr mein
meine meinem meinen meiner meines muss musst musste müssen nach nachdem neben nicht nichts noch nochmals nun
obwohl oder ohne schon sehr sein seine seinem seinen seiner seines seit seitdem selbst sich sind soll sollen
sollte sollten solche solchem solchen solcher solches somit sondern sonst soweit sowie sowohl trotz trotzdem
über überhaupt unser unsere unserem unseren unserer unseres unter viel viele vielem vielen vieler vielleicht
vom von vor während wann waren warst warum weder weil weiter weitere weiterem weiteren weiterer weiteres
welche welchem welchen welcher welches wenig wenige weniger wenn werde werden werdet wieder wird wirst wobei
wodurch wohl wollen wollte wollten worden wurde wurden würde würden zwar zwischen
""".split())
_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Evaluation modes: one combined request per segment that returns every
# category at once, or the classic three separate requests per segment.
EVALUATION_COMBINED = "combined"
EVALUATION_SEPARATE = "separate"
EVALUATION_MODES = (EVALUATION_COMBINED, EVALUATION_SEPARATE)
EVALUATION_LABELS = {
    EVALUATION_COMBINED: "One combined request per segment (faster)",
    EVALUATION_SEPARATE: "Three separate requests (more thorough)",
}
COMBINED_SYSTEM_PROMPT = (
    "You are a careful text editor reviewing one segment of a manuscript. Preserve the "
    "original language, meaning, paragraphs, quotations and formatting. Answer with a "
    "single JSON object and nothing else."
)
COMBINED_MAX_TOKENS = 4096
COMBINED_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(CHECKS)},
                    "original": {"type": "string"},
                    "replacement": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["category", "original", "replacement", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["edits"],
    "additionalProperties": False,
}
COMBINED_EXAMPLE = (
    '{"edits": [{"category": "spelling", "original": "teh dog", "replacement": "the dog", '
    '"reason": "Typo."}]}'
)

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
    """One word-level edit proposed by a check, anchored in the segment text.

    ``kind`` (see ``change_kinds.CHANGE_KINDS``) describes what the edit does
    regardless of which check proposed it; it is derived from the two texts
    whenever it is missing or unknown, so older project files need no upgrade.
    """

    change_id: str
    check: str
    start: int
    end: int
    original_text: str
    proposed_text: str
    explanation: str = ""
    decision: str = PENDING
    kind: str = ""
    flags: List[str] = field(default_factory=list)  # hallucination-guard reason ids, see flag_suspicious

    def __post_init__(self):
        if self.kind not in CHANGE_KINDS:
            self.kind = classify_change(self.original_text, self.proposed_text)
        self.flags = [str(flag) for flag in (self.flags or []) if flag]

    @property
    def flagged(self):
        return bool(self.flags)

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
            "kind": self.kind,
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            data["id"], data["check"], int(data["start"]), int(data["end"]),
            data.get("original", ""), data.get("proposed", ""),
            data.get("explanation", ""), data.get("decision", PENDING),
            data.get("kind") or "", list(data.get("flags") or []),
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
    suppressed: List[Change] = field(default_factory=list)  # dropped by the glossary post-filter
    method: str = EVALUATION_SEPARATE  # "combined" (one request for all checks) or "separate"

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
            "suppressed": [change.to_dict() for change in self.suppressed],
            "method": self.method,
        }

    @classmethod
    def from_dict(cls, data):
        method = data.get("method")
        return cls(
            data["check"], data.get("status", "done"), data.get("proposed", ""),
            [Change.from_dict(item) for item in data.get("changes", [])],
            data.get("error", ""), data.get("model", ""), float(data.get("duration", 0.0)),
            bool(data.get("explained", False)),
            [Change.from_dict(item) for item in data.get("suppressed", []) or []],
            method if method in EVALUATION_MODES else EVALUATION_SEPARATE,
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
    style_guide: str = ""  # author's standing instructions, see build_check_instruction
    glossary: List[str] = field(default_factory=list)  # protected terms, see glossary_matcher
    evaluation_mode: str = EVALUATION_COMBINED  # see EVALUATION_MODES

    def enabled_checks(self):
        return [check for check in CHECKS if self.checks.get(check)]

    @property
    def combined(self):
        return self.evaluation_mode == EVALUATION_COMBINED

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
            "style_guide": self.style_guide,
            "glossary": list(self.glossary),
            "evaluation_mode": self.evaluation_mode,
        }

    @classmethod
    def from_dict(cls, data):
        options = cls()
        for key, value in (data or {}).items():
            if key in ("checks", "auto_accept"):
                merged = getattr(options, key)
                merged.update({k: bool(v) for k, v in (value or {}).items() if k in CHECKS})
            elif key == "style_guide":
                options.style_guide = str(value or "")
            elif key == "glossary":
                options.glossary = parse_glossary(value)
            elif key == "evaluation_mode":
                options.evaluation_mode = value if value in EVALUATION_MODES else EVALUATION_COMBINED
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

    def pending_checks(self, segment):
        """Return the enabled checks that still lack a successful result for ``segment``."""
        return [
            check for check in self.options.enabled_checks()
            if segment.results.get(check) is None or segment.results[check].status == "error"
        ]

    def pending_tasks(self):
        """Return the evaluation tasks still to run, in (chapter, segment, check) order.

        In separate mode every missing (segment, check) pair is one task; in
        combined mode a segment with any missing check is one task whose check
        is ``None`` (one request answers all its checks).
        """
        tasks = []
        for chapter in self.chapters:
            for segment in chapter.segments:
                if segment.is_blank:
                    continue
                pending = self.pending_checks(segment)
                if not pending:
                    continue
                if self.options.combined:
                    tasks.append((chapter.index, segment.index, None))
                else:
                    tasks.extend((chapter.index, segment.index, check) for check in pending)
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
            "suppressed": 0, "flagged": 0, "flagged_pending": 0, "words": 0,
            "methods": {mode: 0 for mode in EVALUATION_MODES},
            "per_check": {
                check: {
                    "changes": 0, "accepted": 0, "rejected": 0, "suppressed": 0, "flagged": 0,
                    "by_kind": {kind: 0 for kind in CHANGE_KINDS},
                }
                for check in CHECKS
            },
            "per_kind": {kind: {"changes": 0, "accepted": 0, "rejected": 0} for kind in CHANGE_KINDS},
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
            for check in checks:
                result = segment.results.get(check)
                if result is None or result.status != "done":
                    continue
                stats["methods"][result.method] = stats["methods"].get(result.method, 0) + 1
                if result.suppressed:
                    stats["suppressed"] += len(result.suppressed)
                    stats["per_check"][check]["suppressed"] += len(result.suppressed)
            for change in segment.changes(enabled):
                per_check = stats["per_check"][change.check]
                per_kind = stats["per_kind"][change.kind]
                stats["changes"] += 1
                per_check["changes"] += 1
                per_check["by_kind"][change.kind] += 1
                per_kind["changes"] += 1
                if change.flagged:
                    stats["flagged"] += 1
                    per_check["flagged"] += 1
                    if change.decision == PENDING:
                        stats["flagged_pending"] += 1
                if change.decision == ACCEPTED:
                    stats["accepted"] += 1
                    per_check["accepted"] += 1
                    per_kind["accepted"] += 1
                elif change.decision == REJECTED:
                    stats["rejected"] += 1
                    per_check["rejected"] += 1
                    per_kind["rejected"] += 1
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
            "- Evaluation: {0} ({1} results combined, {2} separate)".format(
                self.options.evaluation_mode, stats["methods"][EVALUATION_COMBINED], stats["methods"][EVALUATION_SEPARATE]
            ),
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
            if per["changes"]:
                lines.append("    - by kind: " + ", ".join(
                    "{0} {1}".format(CHANGE_KIND_LABELS[kind].lower(), per["by_kind"][kind])
                    for kind in CHANGE_KINDS if per["by_kind"][kind]
                ))
            if per["suppressed"]:
                lines.append("    - Glossary suppressed {0} proposed change(s)".format(per["suppressed"]))
            if per["flagged"]:
                lines.append("    - {0}: {1} change(s) flagged".format(FLAG_REPORT_MARK, per["flagged"]))
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
                    line = "  - [{0}] `{1}` → `{2}` — {3} — {4}".format(
                        CHECK_LABELS[change.check],
                        _inline(change.original_text), _inline(change.proposed_text),
                        states[change.change_id], change.explanation or FALLBACK_EXPLANATIONS[change.check],
                    )
                    if change.flagged:
                        line += " — {0} ({1})".format(FLAG_REPORT_MARK, ", ".join(change.flags))
                    lines.append(line)
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


# ----------------------------------------------------------- style guide
def normalise_style_guide(style_guide):
    """Return the author's rules one per line, trimmed to ``STYLE_GUIDE_MAX_CHARS``.

    Authors type rules separated by line breaks or by " · " bullets (as in the
    placeholder); both become one rule per line. Blank lines are dropped.
    """
    rules = []
    for line in (style_guide or "").replace("\u00b7", "\n").splitlines():
        rule = " ".join(line.split())
        if rule:
            rules.append(rule)
    return "\n".join(rules)[:STYLE_GUIDE_MAX_CHARS].rstrip()


def format_style_guide(style_guide):
    """Return the block appended to a prompt for a non-empty style guide, else ""."""
    rules = normalise_style_guide(style_guide)
    if not rules:
        return ""
    return "\n\n" + STYLE_GUIDE_HEADER + "\n" + rules


# -------------------------------------------------------------- glossary
def parse_glossary(text):
    """Return the protected terms from a text box (one per line) or a stored list.

    Terms are trimmed, empty lines and duplicates dropped (first occurrence
    wins, order kept). A term may end in ``*`` to protect every word that
    starts with it (``hyper*`` covers ``hyperdrive``).
    """
    if text is None:
        return []
    if isinstance(text, str):
        items = text.replace("\u00b7", "\n").splitlines()
    else:
        items = list(text)
    terms = []
    seen = set()
    for item in items:
        term = " ".join(str(item).split())
        if not term or term == "*" or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def glossary_pattern(terms):
    """Return the compiled regex matching any protected term, or ``None`` when there is none."""
    parts = []
    for term in parse_glossary(terms):
        if term.endswith("*"):
            stem = term[:-1]
            parts.append("(?<!\\w)" + re.escape(stem))
        else:
            parts.append("(?<!\\w)" + re.escape(term) + "(?!\\w)")
    if not parts:
        return None
    # Longest first so "Meret Aubinger" wins over "Meret"; lookarounds instead of
    # \b so terms may start or end with punctuation. Case-sensitive on purpose:
    # a protected name keeps its capitalization.
    parts.sort(key=len, reverse=True)
    return re.compile("|".join(parts), re.UNICODE)


def glossary_matcher(terms):
    """Return ``callable(text) -> bool`` telling whether ``text`` contains a protected term."""
    pattern = glossary_pattern(terms)
    if pattern is None:
        return lambda text: False
    return lambda text: bool(text) and pattern.search(text) is not None


def glossary_prompt_terms(terms):
    """Return the terms listed in the prompt: at most ``GLOSSARY_MAX_TERMS`` / ``GLOSSARY_MAX_CHARS``."""
    listed = []
    length = 0
    for term in parse_glossary(terms)[:GLOSSARY_MAX_TERMS]:
        length += len(term) + (2 if listed else 0)
        if length > GLOSSARY_MAX_CHARS:
            break
        listed.append(term)
    return listed


def format_glossary(terms):
    """Return the prompt block naming the protected terms, or "" when there are none."""
    listed = glossary_prompt_terms(terms)
    if not listed:
        return ""
    return "\n\n" + GLOSSARY_HEADER + ", ".join(listed)


def suppress_glossary_changes(text, changes, matcher):
    """Split ``changes`` into (kept, suppressed) using the glossary ``matcher``.

    A change is suppressed when its original text contains a protected term
    or, for insertions, when the ``GLOSSARY_CONTEXT`` characters on either
    side of the insertion point do.
    """
    kept, suppressed = [], []
    for change in changes:
        if change.original_text.strip():
            hit = matcher(change.original_text)
        else:
            window = text[max(0, change.start - GLOSSARY_CONTEXT):change.end + GLOSSARY_CONTEXT]
            hit = matcher(window)
        if hit:
            change.explanation = GLOSSARY_EXPLANATION
            suppressed.append(change)
        else:
            kept.append(change)
    return kept, suppressed


# ---------------------------------------------------- hallucination guard
def content_words(text):
    """Return the lower-cased words of ``text`` that can carry content (long enough, not stopwords)."""
    return {
        word for word in _WORD_RE.findall((text or "").lower())
        if len(word) >= NOVEL_MIN_LENGTH and word not in STOPWORDS
    }


def novel_words(segment_text, proposed_text):
    """Return the content words of ``proposed_text`` that occur nowhere in ``segment_text`` (case-insensitive)."""
    known = set(_WORD_RE.findall((segment_text or "").lower()))
    return sorted(word for word in content_words(proposed_text) if word not in known)


def flag_suspicious(segment_text, change):
    """Return a reason id when ``change`` looks like added content rather than a correction, else ``None``.

    Rules, first match wins: ``growth`` (the replacement is far longer than the
    original), ``novel_words`` (at least ``NOVEL_MIN_WORDS`` content words that
    appear nowhere in the segment) and ``rewrite_in_strict_check`` (a spelling
    or grammar check produced a ``rewrite``-kind change).
    """
    original = change.original_text
    proposed = change.proposed_text
    if len(proposed) > GROWTH_FACTOR * len(original) + GROWTH_SLACK:
        return FLAG_GROWTH
    if len(novel_words(segment_text, proposed)) >= NOVEL_MIN_WORDS:
        return FLAG_NOVEL_WORDS
    if change.kind == "rewrite" and change.check in STRICT_CHECKS:
        return FLAG_REWRITE_IN_STRICT_CHECK
    return None


def flag_changes(segment_text, changes):
    """Append the ``flag_suspicious`` reason to every change it fires on; returns the flagged ones."""
    flagged = []
    for change in changes:
        reason = flag_suspicious(segment_text, change)
        if reason and reason not in change.flags:
            change.flags.append(reason)
        if reason:
            flagged.append(change)
    return flagged


def build_check_instruction(check, options=None, style_guide=None, glossary=None):
    """Return the instruction sent for ``check`` with the author's rules and protected terms appended.

    ``style_guide`` / ``glossary`` win over the values in ``options``; all may
    be omitted, in which case the plain ``CHECK_INSTRUCTIONS`` entry is returned.
    """
    if style_guide is None:
        style_guide = getattr(options, "style_guide", "") if options is not None else ""
    if glossary is None:
        glossary = getattr(options, "glossary", None) if options is not None else None
    return CHECK_INSTRUCTIONS[check] + format_style_guide(style_guide) + format_glossary(glossary or [])


# ---------------------------------------------------------- explanations
def _context(text, start, end, radius=40):
    before = text[max(0, start - radius):start].replace("\n", " ")
    after = text[end:end + radius].replace("\n", " ")
    return before, after


def build_explanation_messages(segment_text, changes, check, language=SAME_LANGUAGE, style_guide=""):
    """Build the prompt asking for one short explanation per change.

    The author's rules are appended after the check description so the
    explanations do not argue against them.
    """
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
                "Check: {2}{3}\n\nText:\n{4}\n\nChanges:\n{5}"
            ).format(
                CHECK_LABELS[check].lower(), language_clause, CHECK_DESCRIPTIONS[check],
                format_style_guide(style_guide), segment_text, "\n".join(lines),
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


def run_check(service, model, text, check, cancel_event, explain=True, language=SAME_LANGUAGE, retries=1,
              style_guide="", glossary=None):
    """Evaluate one check on one segment; returns a CheckResult (never raises except on cancel).

    Edits are requested with ``text_first=True``: the segment comes before the
    check's instruction, so the three checks of one segment share a prompt
    prefix that the GPU server can serve from its prefix cache. ``style_guide``
    holds the author's standing instructions; they are appended to the check's
    instruction and to the explanation prompt. ``glossary`` lists protected
    terms: they are named in the prompt and any change touching one is moved
    to ``CheckResult.suppressed`` instead of being proposed.
    """
    started = time.time()
    attempt = 0
    instruction = build_check_instruction(check, style_guide=style_guide, glossary=glossary)
    while True:
        try:
            proposed = strip_fences(
                service.stream_edit(model, instruction, text, cancel_event, text_first=True)
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

    changes, suppressed = suppress_glossary_changes(text, extract_changes(text, proposed, check), glossary_matcher(glossary))
    flag_changes(text, changes)
    explained = False
    explanations = {}
    if changes and explain:
        try:
            answer = service.generate(
                model, build_explanation_messages(text, changes, check, language, style_guide), cancel_event,
                max_tokens=2048,
            )
            explanations = parse_explanations(answer)
            explained = bool(explanations)
        except EditCancelled:
            raise
        except Exception:  # explanations are best effort
            explanations = {}
    for number, change in enumerate(changes, 1):
        change.explanation = explanations.get(number) or FALLBACK_EXPLANATIONS[check]
    return CheckResult(
        check, "done", proposed, changes, model=model, duration=time.time() - started, explained=explained,
        suppressed=suppressed,
    )


# --------------------------------------------------------- combined pass
def _language_clause(language):
    return "in the same language as the text" if language == SAME_LANGUAGE else "in " + language


def build_combined_messages(text, options=None):
    """Build the single request that returns the edits of every enabled check at once."""
    options = options or ProjectOptions()
    checks = options.enabled_checks() or list(CHECKS)
    rules = "\n".join(
        "{0}. {1}: {2}".format(number, check, CHECK_INSTRUCTIONS[check])
        for number, check in enumerate(checks, 1)
    )
    extras = format_style_guide(options.style_guide) + format_glossary(options.glossary)
    content = (
        "Review the text below and list every correction you would make, in the order in which "
        "they occur in the text. Assign each edit exactly one of these categories:\n{rules}{extras}\n\n"
        "Text:\n{text}\n\n"
        "Return a JSON object of this shape (one entry per edit):\n{example}\n"
        "Rules: \"original\" must be an exact substring of the text, with the same spelling, punctuation "
        "and spacing; when it occurs more than once, extend it with the neighbouring words until it is "
        "unique. \"replacement\" is the text that takes its place (repeat the context words unchanged; "
        "an empty string deletes). \"reason\" is one short sentence {language} explaining why the new "
        "version is better. Return {{\"edits\": []}} when nothing needs to change."
    ).format(rules=rules, extras=extras, text=text, example=COMBINED_EXAMPLE,
             language=_language_clause(options.language))
    return [
        {"role": "system", "content": COMBINED_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _anchor(text, snippet, cursor):
    """Return the unique position of ``snippet`` at/after ``cursor`` (else anywhere), or -1."""
    position = text.find(snippet, cursor)
    if position != -1 and text.find(snippet, position + 1) == -1:
        return position
    if position == -1:
        position = text.find(snippet)
        if position != -1 and text.find(snippet, position + 1) == -1:
            return position
    return -1


def apply_changes(text, changes):
    """Return ``text`` with every change applied (changes must not overlap)."""
    output = []
    position = 0
    for change in sorted(changes, key=lambda change: (change.start, change.end)):
        output.append(text[position:change.start])
        output.append(change.proposed_text)
        position = change.end
    output.append(text[position:])
    return "".join(output)


def parse_combined(text, answer, options=None):
    """Turn the combined answer into one ``CheckResult`` per enabled check.

    Every edit is anchored by an exact ``find`` of its ``original`` from a
    moving cursor (falling back to a unique match anywhere); the snippet is
    then diffed against its replacement so the changes are word-level, like
    the separate mode's. Returns ``None`` when the JSON is malformed or any
    edit cannot be anchored unambiguously, so the caller can fall back.
    """
    options = options or ProjectOptions()
    checks = options.enabled_checks() or list(CHECKS)
    data = _parse_json_object(answer)
    if not isinstance(data, dict) or not isinstance(data.get("edits"), list):
        return None
    per_check = {check: [] for check in checks}
    cursor = 0
    for item in data["edits"]:
        if not isinstance(item, dict):
            return None
        category = str(item.get("category", "")).strip().lower()
        original = item.get("original")
        replacement = item.get("replacement")
        if category not in CHECKS or not isinstance(original, str) or not isinstance(replacement, str):
            return None
        if not original:
            return None  # an insertion without context cannot be placed
        position = _anchor(text, original, cursor)
        if position == -1:
            return None
        cursor = max(cursor, position + len(original))
        if category not in per_check or original == replacement:
            continue
        reason = " ".join(str(item.get("reason") or "").split())[:200]
        existing = per_check[category]
        for change in extract_changes(original, replacement, category):
            change.start += position
            change.end += position
            if any(overlaps(change, other) for other in existing):
                continue  # the model listed the same words twice for one category
            change.explanation = reason
            existing.append(change)

    matcher = glossary_matcher(options.glossary)
    results = {}
    for check in checks:
        changes = sorted(per_check[check], key=lambda change: (change.start, change.end))
        for number, change in enumerate(changes, 1):
            change.change_id = "{0}-{1}".format(check, number)
        kept, suppressed = suppress_glossary_changes(text, changes, matcher)
        flag_changes(text, kept)
        for change in kept:
            change.explanation = change.explanation or FALLBACK_EXPLANATIONS[check]
        results[check] = CheckResult(
            check, "done", apply_changes(text, kept), kept, explained=any(
                change.explanation != FALLBACK_EXPLANATIONS[check] for change in kept
            ), suppressed=suppressed, method=EVALUATION_COMBINED,
        )
    return results


def run_segment_combined(service, model, text, options, cancel_event, structured=True,
                         on_structured_unsupported=None):
    """Evaluate every enabled check of one segment with a single request.

    Falls back to ``run_check`` per check (results marked ``separate``) when
    the answer cannot be parsed or anchored, when the backend fails, or when
    it rejects the JSON schema; in the last case ``on_structured_unsupported``
    is called so the runner can stop asking for structured output.
    """
    started = time.time()
    checks = options.enabled_checks() or list(CHECKS)
    messages = build_combined_messages(text, options)
    results = None
    try:
        answer = service.generate(
            model, messages, cancel_event, max_tokens=COMBINED_MAX_TOKENS,
            response_format=COMBINED_SCHEMA if structured else None,
        )
        results = parse_combined(text, strip_fences(answer), options)
    except EditCancelled:
        raise
    except StructuredOutputUnsupported:
        if on_structured_unsupported is not None:
            on_structured_unsupported()
    except Exception:  # any backend failure: the separate mode has its own retries
        results = None
    if results is not None:
        duration = time.time() - started
        for result in results.values():
            result.model = model
            result.duration = duration
        return results
    return {
        check: run_check(
            service, model, text, check, cancel_event, explain=options.explain,
            language=options.language, style_guide=options.style_guide, glossary=options.glossary,
        )
        for check in checks
    }


class ProjectRunner:
    """Evaluate every missing (segment, check) pair on background threads.

    Events: ``("workflow_started", chapter_index, segment_index, check)`` when
    a task begins, ``("workflow_result", chapter_index, segment_index, check,
    CheckResult)`` followed by ``("workflow_progress", done, total, running)``
    when it ends, and finally ``("workflow_finished", cancelled)``. The caller applies results to the
    project on its own thread, so the runner never mutates project state.

    In combined mode a task covers a whole segment: one request answers every
    pending check, and the runner still emits one ``workflow_started`` and one
    ``workflow_result`` per check so the UI needs no special case. When the
    server rejects the JSON schema once, ``structured_output`` turns off for
    the rest of the run.
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
        self.structured_output = True  # cleared when the backend rejects response_format

    @property
    def active(self):
        return any(thread.is_alive() for thread in self._threads)

    def start(self):
        """Queue every pending task and start the workers; returns the task count.

        The queue keeps ``pending_tasks()`` order, (chapter, segment, check):
        all checks of one segment are dispatched consecutively, and because
        ``run_check`` puts the segment text before the instruction, the GPU
        server sees the same prompt prefix back to back and can reuse it from
        its prefix cache (vLLM ``--enable-prefix-caching``) instead of
        re-encoding the segment for every check. Keep the order when changing
        the scheduling.
        """
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
                checks = self.project.pending_checks(segment) if check is None else [check]
                if not checks:
                    continue
                with self._lock:
                    self.running += 1
                for pending in checks:
                    self.events.put(("workflow_started", chapter_index, segment_index, pending))
                try:
                    if check is None:
                        results = run_segment_combined(
                            self.service, self.model, segment.text, options, self.cancel_event,
                            structured=self.structured_output,
                            on_structured_unsupported=self._disable_structured_output,
                        )
                        results = {pending: results[pending] for pending in checks}
                    else:
                        results = {check: run_check(
                            self.service, self.model, segment.text, check, self.cancel_event,
                            explain=options.explain, language=options.language, style_guide=options.style_guide,
                            glossary=options.glossary,
                        )}
                except EditCancelled:
                    with self._lock:
                        self.running -= 1
                    break
                except Exception as exc:  # defensive: a crash must not kill the worker
                    results = {
                        pending: CheckResult(pending, "error", error="Unexpected error: {0!r}".format(exc), model=self.model)
                        for pending in checks
                    }
                with self._lock:
                    self.running -= 1
                    self.done += 1
                    if any(result.status == "error" for result in results.values()):
                        self.failed += 1
                    done, running = self.done, self.running
                for pending in checks:
                    self.events.put(("workflow_result", chapter_index, segment_index, pending, results[pending]))
                self.events.put(("workflow_progress", done, self.total, running))
        finally:
            with self._lock:
                self._active_workers -= 1
                last = self._active_workers == 0
            if last:
                self.events.put(("workflow_finished", self.cancel_event.is_set()))

    def _disable_structured_output(self):
        self.structured_output = False

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
