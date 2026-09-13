"""Tests for chapter detection and segment splitting."""

import random

from core.chunking import (
    chapters_from_outline,
    join_chapters,
    join_segments,
    paragraph_outline,
    split_chapters,
    split_document,
    split_segments,
)

random.seed(7)
WORDS = "the quick brown fox jumps over a lazy dog while rain falls softly on the old roof".split()


def prose(sentences, words=12):
    parts = []
    for _ in range(sentences):
        chosen = [random.choice(WORDS) for _ in range(words)]
        parts.append(" ".join(chosen).capitalize() + ".")
    return " ".join(parts)


def paragraphs(count, sentences=4):
    return "\n\n".join(prose(sentences) for _ in range(count))


def assert_roundtrip(text, result):
    assert join_chapters(result.chapters) == text
    for chapter in result.chapters:
        assert join_segments(chapter.segments) == chapter.body


def test_markdown_headings_become_chapters_with_front_matter():
    text = "Title page\n\n# One\n\n" + paragraphs(3) + "\n\n## Two: The Return\n\n" + paragraphs(2) + "\n"
    result = split_document(text)

    assert result.method == "headings:markdown"
    assert [c.title for c in result.chapters] == ["Front matter", "One", "Two: The Return"]
    assert result.chapters[1].heading == "# One\n\n"
    assert result.chapters[-1].trailing == "\n"
    assert_roundtrip(text, result)


def test_german_chapter_words_and_numbered_titles():
    text = "Kapitel 1\n\n" + paragraphs(3) + "\n\nKapitel 2: Die Reise\n\n" + paragraphs(3)
    result = split_chapters(text)
    assert result.method == "headings:chapter-word"
    assert [c.title for c in result.chapters] == ["Kapitel 1", "Kapitel 2: Die Reise"]
    assert_roundtrip(text, split_document(text))

    numbered = "1. Der Anfang\n\n" + paragraphs(3) + "\n\n2. Das Ende\n\n" + paragraphs(3)
    assert split_chapters(numbered).method == "headings:numbered-title"


def test_scene_separators_are_used_only_without_headings():
    text = paragraphs(3) + "\n\n* * *\n\n" + paragraphs(3) + "\n\n***\n\n" + paragraphs(3)
    result = split_document(text)
    assert result.method == "separators"
    assert [c.title for c in result.chapters] == ["Front matter", "Section 1", "Section 2"]
    assert_roundtrip(text, result)

    with_headings = "CHAPTER ONE\n\n" + paragraphs(3) + "\n\n* * *\n\n" + paragraphs(3) + "\n\nCHAPTER TWO\n\n" + paragraphs(3)
    result = split_chapters(with_headings)
    assert result.method == "headings:chapter-word"
    assert len(result.chapters) == 2


def test_prose_lines_are_not_mistaken_for_headings():
    text = "\n\n".join(
        [prose(3), "Part of me wanted to leave the house before dawn.", prose(3),
         "Book your tickets early if you want a window seat.", prose(3)]
    )
    result = split_chapters(text)
    assert result.method == "single"


def test_large_unstructured_text_is_split_by_size_and_small_is_single():
    small = paragraphs(4)
    assert split_chapters(small).method == "single"
    assert split_chapters(small).chapters[0].body == small

    large = paragraphs(60)
    assert len(large) > 8000
    result = split_document(large, max_chapter_chars=8000)
    assert result.method == "size"
    assert len(result.chapters) >= 2
    assert all(len(c.body) <= 8000 + 800 for c in result.chapters)
    assert [c.title for c in result.chapters][:2] == ["Part 1", "Part 2"]
    assert_roundtrip(large, result)


def test_segments_respect_target_and_paragraph_boundaries():
    body = paragraphs(12, sentences=5)
    segments = split_segments(body, target_chars=900, max_chars=1500)

    assert join_segments(segments) == body
    assert all(len(s.text) <= 1500 for s in segments)
    assert all(not s.text.startswith("\n") for s in segments)
    assert all(not s.text.endswith("\n") for s in segments)
    assert sum(1 for s in segments if len(s.text) < 225) == 0  # no dangling tail
    assert [s.index for s in segments] == list(range(1, len(segments) + 1))


def test_oversized_paragraph_is_split_at_sentence_boundaries():
    monster = prose(60, words=15)
    assert len(monster) > 3000
    segments = split_segments(monster, target_chars=1000, max_chars=1400)

    assert join_segments(segments) == monster
    assert all(len(s.text) <= 1400 for s in segments)
    assert all(s.text.endswith(".") for s in segments)


def test_empty_and_whitespace_only_inputs():
    assert split_chapters("").chapters == []
    assert split_chapters("   \n\n  ").chapters == []
    assert split_segments("") == []


def test_outline_and_model_chosen_boundaries():
    text = paragraphs(6)
    outline = paragraph_outline(text)
    assert [index for index, _ in outline] == list(range(6))
    assert all(len(preview) <= 100 for _, preview in outline)

    result = chapters_from_outline(text, [2, 4, 99, -1, 4], titles=["Start", "Middle", "End"])
    assert result.method == "model"
    assert [c.title for c in result.chapters] == ["Start", "Middle", "End"]
    assert join_chapters(result.chapters) == text
    assert chapters_from_outline(text, []).method == "single"


def test_crlf_input_is_normalised_by_reader(tmp_path):
    from core.chunking import read_text_file

    path = tmp_path / "m.txt"
    path.write_bytes("﻿Kapitel 1\r\n\r\nText.\r\n".encode("utf-8"))
    assert read_text_file(path) == "Kapitel 1\n\nText.\n"
    path.write_bytes("Gr\xfc\xdfe".encode("cp1252"))
    assert read_text_file(path) == "Grüße"
