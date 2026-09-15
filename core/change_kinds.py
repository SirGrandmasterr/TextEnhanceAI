"""Classify a word-level change by what kind of edit it is.

The kind lets the review UI filter or bulk-decide changes ("accept all
punctuation fixes") independently of which check proposed them. Rules are
applied in the order of ``CHANGE_KINDS`` and the first match wins; they are
heuristics on the two text fragments alone, without any language knowledge.
"""

import unicodedata

KIND_WHITESPACE = "whitespace"
KIND_PUNCTUATION = "punctuation"
KIND_CAPITALIZATION = "capitalization"
KIND_SPELLING = "spelling"
KIND_WORD_CHOICE = "word_choice"
KIND_INSERTION = "insertion"
KIND_DELETION = "deletion"
KIND_REWRITE = "rewrite"
CHANGE_KINDS = (
    KIND_WHITESPACE,
    KIND_PUNCTUATION,
    KIND_CAPITALIZATION,
    KIND_SPELLING,
    KIND_WORD_CHOICE,
    KIND_INSERTION,
    KIND_DELETION,
    KIND_REWRITE,
)
CHANGE_KIND_LABELS = {
    KIND_WHITESPACE: "Whitespace",
    KIND_PUNCTUATION: "Punctuation",
    KIND_CAPITALIZATION: "Capitalization",
    KIND_SPELLING: "Spelling",
    KIND_WORD_CHOICE: "Word choice",
    KIND_INSERTION: "Insertion",
    KIND_DELETION: "Deletion",
    KIND_REWRITE: "Rewrite",
}
SPELLING_MAX_DISTANCE = 2
WORD_CHOICE_MAX_WORDS = 3


def collapse_whitespace(text):
    """Return ``text`` with runs of whitespace reduced to one space and the ends trimmed."""
    return " ".join(text.split())


def strip_punctuation(text):
    """Remove every character in a Unicode punctuation category (P*)."""
    return "".join(ch for ch in text if not unicodedata.category(ch).startswith("P"))


def levenshtein(a, b):
    """Return the edit distance between two strings (insert, delete, substitute)."""
    if a == b:
        return 0
    if not a or not b:
        return len(a) + len(b)
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, 1):
        current = [i]
        for j, ch_b in enumerate(b, 1):
            current.append(min(
                previous[j] + 1,  # deletion
                current[j - 1] + 1,  # insertion
                previous[j - 1] + (ch_a != ch_b),  # substitution
            ))
        previous = current
    return previous[-1]


def edit_distance(original_text, proposed_text):
    """Return the Levenshtein distance between the whitespace-collapsed texts."""
    return levenshtein(collapse_whitespace(original_text), collapse_whitespace(proposed_text))


def classify_change(original_text, proposed_text):
    """Return the ``CHANGE_KINDS`` entry describing an edit of ``original_text`` into ``proposed_text``."""
    original = collapse_whitespace(original_text)
    proposed = collapse_whitespace(proposed_text)
    if original == proposed:
        return KIND_WHITESPACE
    if collapse_whitespace(strip_punctuation(original)) == collapse_whitespace(strip_punctuation(proposed)):
        return KIND_PUNCTUATION
    # lower() rather than casefold(): casefold turns "ß" into "ss", which would
    # file the German spelling fix "Grossmutter" -> "Großmutter" under capitalization.
    if original.lower() == proposed.lower():
        return KIND_CAPITALIZATION
    if not original:
        return KIND_INSERTION
    if not proposed:
        return KIND_DELETION
    original_words = original.split()
    proposed_words = proposed.split()
    if (
        len(original_words) == 1 and len(proposed_words) == 1
        and edit_distance(original, proposed) <= SPELLING_MAX_DISTANCE
    ):
        return KIND_SPELLING
    if len(original_words) <= WORD_CHOICE_MAX_WORDS and len(proposed_words) <= WORD_CHOICE_MAX_WORDS:
        return KIND_WORD_CHOICE
    return KIND_REWRITE
