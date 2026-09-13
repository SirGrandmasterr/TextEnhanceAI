"""Split a manuscript into chapters and reviewable segments.

Every split keeps the exact source text: chapters concatenate back to the
original document (``heading + body + trailing``), and segments concatenate
back to their chapter body (``text + trailing``). Nothing is normalised, so
whatever the author wrote survives untouched until the model proposes edits.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .diff_engine import _sentence_units

DEFAULT_TARGET_CHARS = 1800
DEFAULT_MAX_CHARS = 3000
DEFAULT_MAX_CHAPTER_CHARS = 30000
MIN_CHAPTER_BODY = 200  # candidate heading patterns producing tinier chapters are ignored
MAX_HEADING_LENGTH = 90

CHAPTER_WORDS = (
    "chapter|kapitel|cap[ií]tulo|chapitre|capitolo|hoofdstuk|rozdzia[łl]|глава|"
    "part|teil|parte|partie|book|buch|libro|livre|prolog|prologue|epilog|epilogue|"
    "vorwort|nachwort|interlude|zwischenspiel|intro|introduction|einleitung"
)
_PATTERNS = [
    ("markdown", re.compile(r"^#{1,6}\s+\S.*$")),
    (
        "chapter-word",
        re.compile(
            r"^(?:{0})\b[\s.:\-–—]*(?:\d{{1,4}}|[ivxlcdm]{{1,8}}|[a-zäöüß]+)?\b.{{0,80}}$".format(
                CHAPTER_WORDS
            ),
            re.IGNORECASE,
        ),
    ),
    ("numbered-title", re.compile(r"^(?:\d{1,3}|[IVXLCDM]{1,7})[.)]\s+\S.{0,80}$")),
    ("numeral", re.compile(r"^(?:\d{1,3}|[IVXLCDM]{1,7})[.)]?$")),
    ("caps-title", re.compile(r"^[^\W\d_](?:[^a-zäöüß\n]{2,70})$")),
    ("separator", re.compile(r"^(?:[*#\-_=~•·]\s*){3,}$")),
]
_PRIORITY = [name for name, _ in _PATTERNS]
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")
_WORD = re.compile(r"\S+")


@dataclass
class Segment:
    """One reviewable chunk of a chapter body."""

    index: int
    text: str
    trailing: str = ""

    @property
    def word_count(self):
        return len(_WORD.findall(self.text))


@dataclass
class Chapter:
    """A chapter: verbatim heading, body text and trailing whitespace."""

    index: int
    title: str
    heading: str
    body: str
    trailing: str = ""
    segments: List[Segment] = field(default_factory=list)

    @property
    def word_count(self):
        return len(_WORD.findall(self.body))


@dataclass
class SplitResult:
    chapters: List[Chapter]
    method: str  # e.g. "headings:markdown", "separators", "size", "single"


# ----------------------------------------------------------------- helpers
def word_count(text):
    """Return the number of whitespace-separated words."""
    return len(_WORD.findall(text))


def join_chapters(chapters):
    """Reconstruct the document from chapters."""
    return "".join(chapter.heading + chapter.body + chapter.trailing for chapter in chapters)


def join_segments(segments):
    """Reconstruct a chapter body from its segments."""
    return "".join(segment.text + segment.trailing for segment in segments)


def _line_spans(text):
    """Yield (start, end_without_newline, line_text) for each line."""
    position = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        yield position, position + len(stripped), stripped
        position += len(line)


def _is_isolated(lines, index):
    """Return whether the line has blank (or no) neighbours above and below."""
    above = lines[index - 1][2].strip() if index > 0 else ""
    below = lines[index + 1][2].strip() if index + 1 < len(lines) else ""
    return above == "" and below == ""


def clean_title(line, fallback):
    """Turn a heading line into a display title."""
    title = re.sub(r"^[#*\s]+|[#*\s]+$", "", line).strip()
    title = re.sub(r"\s+", " ", title)
    if not title or re.fullmatch(r"[*#\-_=~•·\s]+", title):
        return fallback
    return title[:MAX_HEADING_LENGTH]


def _looks_like_prose(line):
    """Reject heading candidates that read like an ordinary sentence."""
    words = len(_WORD.findall(line))
    if words > 12:
        return True
    return words > 4 and line[-1] in ".!?…,;"


def _find_headings(text):
    """Return {pattern_name: [(line_start, line_end, line_text), ...]}."""
    lines = list(_line_spans(text))
    found = {name: [] for name in _PRIORITY}
    for index, (start, end, line) in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > MAX_HEADING_LENGTH:
            continue
        for name, pattern in _PATTERNS:
            if not pattern.match(stripped):
                continue
            if name != "markdown" and not _is_isolated(lines, index):
                continue
            if name in ("chapter-word", "caps-title") and _looks_like_prose(stripped):
                continue
            if name == "caps-title" and (len(stripped) < 3 or not re.search(r"[A-ZÄÖÜ]", stripped)):
                continue
            found[name].append((start, end, stripped))
    return found


def _chapters_from_boundaries(text, boundaries, method):
    """Build chapters from heading line spans [(start, end, title), ...]."""
    chapters = []
    cursor = 0
    counter = 0

    def add(heading_start, heading_end, title, next_start):
        nonlocal cursor, counter
        heading = text[heading_start:heading_end]
        rest = text[heading_end:next_start]
        lead = len(rest) - len(rest.lstrip())
        heading += rest[:lead]
        content = rest[lead:]
        body = content.rstrip()
        trailing = content[len(body):]
        counter += 1
        chapters.append(Chapter(counter, title, heading, body, trailing))
        cursor = next_start

    first_start = boundaries[0][0] if boundaries else len(text)
    preamble = text[:first_start]
    if preamble.strip():
        lead = len(preamble) - len(preamble.lstrip())
        body = preamble.strip()
        counter += 1
        chapters.append(
            Chapter(counter, "Front matter", preamble[:lead], body, preamble[lead + len(body):])
        )
    elif preamble and boundaries:
        # Pure whitespace before the first heading belongs to that heading.
        boundaries[0] = (0, boundaries[0][1], boundaries[0][2])
    for position, (start, end, title) in enumerate(boundaries):
        next_start = boundaries[position + 1][0] if position + 1 < len(boundaries) else len(text)
        add(start, end, title, next_start)
    return SplitResult(chapters, method)


def _split_by_size(text, max_chars, title_prefix="Part"):
    """Split at paragraph breaks into parts of roughly ``max_chars``."""
    paragraphs = _paragraphs(text)
    parts = []
    current = []
    size = 0
    for paragraph, separator in paragraphs:
        if current and size + len(paragraph) > max_chars:
            parts.append(current)
            current, size = [], 0
        current.append((paragraph, separator))
        size += len(paragraph) + len(separator)
    if current:
        parts.append(current)
    chapters = []
    for index, part in enumerate(parts, 1):
        raw = "".join(paragraph + separator for paragraph, separator in part)
        lead = len(raw) - len(raw.lstrip())
        body = raw.strip()
        chapters.append(
            Chapter(
                index,
                "{0} {1}".format(title_prefix, index),
                raw[:lead],
                body,
                raw[lead + len(body):],
            )
        )
    return chapters


def split_chapters(text, mode="auto", max_chapter_chars=DEFAULT_MAX_CHAPTER_CHARS):
    """Split a document into chapters.

    ``mode`` is ``"auto"`` (headings, then separators, then size), ``"size"``
    (ignore headings) or ``"single"`` (one chapter).
    """
    if not text.strip():
        return SplitResult([], "empty")
    if mode == "single":
        return _chapters_from_boundaries(text, [], "single")
    if mode != "size":
        found = _find_headings(text)
        for name in _PRIORITY:
            hits = found[name]
            if len(hits) < 2:
                continue
            boundaries = []
            for number, (start, end, line) in enumerate(hits, 1):
                fallback = "Section {0}".format(number) if name == "separator" else line
                boundaries.append((start, end, clean_title(line, fallback)))
            result = _chapters_from_boundaries(text, boundaries, "headings:" + name)
            bodies = [len(chapter.body) for chapter in result.chapters]
            small = sum(1 for size in bodies if size < MIN_CHAPTER_BODY)
            if small > len(bodies) // 2 or len(bodies) > 500:
                continue  # the pattern matched noise, try the next one
            if name == "separator":
                result.method = "separators"
            return result
    if len(text) > max_chapter_chars:
        return SplitResult(_split_by_size(text, max_chapter_chars), "size")
    return _chapters_from_boundaries(text, [], "single")


def chapters_from_outline(text, starts, titles=None):
    """Build chapters from paragraph indices chosen by a model (or a person).

    ``starts`` are 0-based indices into ``paragraph_outline(text)``; the first
    paragraph is always a chapter start. Invalid indices are ignored.
    """
    paragraphs = _paragraphs(text)
    offsets = []
    position = 0
    for paragraph, separator in paragraphs:
        offsets.append(position)
        position += len(paragraph) + len(separator)
    valid = sorted({index for index in starts if isinstance(index, int) and 0 < index < len(paragraphs)})
    boundaries = []
    for number, index in enumerate([0] + valid, 1):
        line = paragraphs[index][0].strip().splitlines()[0] if paragraphs[index][0].strip() else ""
        title = None
        if titles and number - 1 < len(titles) and titles[number - 1]:
            title = str(titles[number - 1])[:MAX_HEADING_LENGTH]
        boundaries.append((offsets[index], offsets[index], title or clean_title(line, "Chapter {0}".format(number))))
    if len(boundaries) < 2:
        return _chapters_from_boundaries(text, [], "single")
    return _chapters_from_boundaries(text, boundaries, "model")


def paragraph_outline(text, max_paragraphs=400, preview_chars=100):
    """Return ``[(index, preview)]`` describing paragraphs for a model prompt."""
    paragraphs = _paragraphs(text)
    outline = []
    step = max(1, len(paragraphs) // max_paragraphs) if len(paragraphs) > max_paragraphs else 1
    for index, (paragraph, _) in enumerate(paragraphs):
        if index % step and index != 0:
            continue
        preview = re.sub(r"\s+", " ", paragraph.strip())[:preview_chars]
        outline.append((index, preview))
    return outline


# ---------------------------------------------------------------- segments
def _paragraphs(text):
    """Return [(paragraph_text, following_separator)] preserving every character."""
    result = []
    position = 0
    for match in _PARAGRAPH_BREAK.finditer(text):
        result.append((text[position:match.start()], match.group(0)))
        position = match.end()
    result.append((text[position:], ""))
    if len(result) > 1 and result[-1] == ("", ""):
        result.pop()
    return result


def _split_long_paragraph(paragraph, max_chars):
    """Split one oversized paragraph at sentence boundaries."""
    pieces = []
    current = ""
    for unit in _sentence_units(paragraph):
        if current and len(current) + len(unit) > max_chars:
            pieces.append(current)
            current = ""
        current += unit
        while len(current) > max_chars:  # a single monstrous sentence
            pieces.append(current[:max_chars])
            current = current[max_chars:]
    if current:
        pieces.append(current)
    return pieces


def split_segments(body, target_chars=DEFAULT_TARGET_CHARS, max_chars=DEFAULT_MAX_CHARS):
    """Split a chapter body into paragraph-aligned segments of ~target size."""
    if not body:
        return []
    max_chars = max(max_chars, target_chars)
    units = []  # (text, trailing) units that never exceed max_chars
    for paragraph, separator in _paragraphs(body):
        if len(paragraph) <= max_chars:
            units.append((paragraph, separator))
            continue
        pieces = _split_long_paragraph(paragraph, max_chars)
        for piece in pieces[:-1]:
            body_part = piece.rstrip()
            units.append((body_part, piece[len(body_part):]))
        units.append((pieces[-1], separator))

    segments = []
    current_text = ""
    current_trailing = ""
    for text, trailing in units:
        candidate = len(current_text) + len(current_trailing) + len(text)
        if current_text and candidate > target_chars:
            segments.append((current_text, current_trailing))
            current_text, current_trailing = "", ""
        if current_text:
            current_text += current_trailing
        current_text += text
        current_trailing = trailing
    if current_text or current_trailing:
        segments.append((current_text, current_trailing))

    # Avoid a dangling tiny tail when it comfortably fits the previous segment.
    if len(segments) >= 2:
        last_text, last_trailing = segments[-1]
        prev_text, prev_trailing = segments[-2]
        if len(last_text) < target_chars // 4 and len(prev_text) + len(prev_trailing) + len(last_text) <= max_chars:
            segments[-2] = (prev_text + prev_trailing + last_text, last_trailing)
            segments.pop()

    result = []
    for index, (text, trailing) in enumerate(segments, 1):
        # Leading whitespace never carries meaning for the model; keep it in the
        # previous segment's trailing part so reconstruction stays exact.
        lead = len(text) - len(text.lstrip())
        if lead and result:
            previous = result[-1]
            previous.trailing += text[:lead]
            text = text[lead:]
        elif lead:
            # First segment: keep leading whitespace inside the text.
            pass
        result.append(Segment(index, text, trailing))
    return [segment for segment in result if segment.text or segment.trailing]


def split_document(text, mode="auto", target_chars=DEFAULT_TARGET_CHARS, max_chars=DEFAULT_MAX_CHARS,
                   max_chapter_chars=DEFAULT_MAX_CHAPTER_CHARS):
    """Split into chapters and segments in one go."""
    result = split_chapters(text, mode=mode, max_chapter_chars=max_chapter_chars)
    for chapter in result.chapters:
        chapter.segments = split_segments(chapter.body, target_chars, max_chars)
    return result


def read_text_file(path):
    """Read a manuscript with sensible encoding fallbacks and ``\\n`` newlines."""
    raw = open(path, "rb").read()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    return text.replace("\r\n", "\n").replace("\r", "\n")
