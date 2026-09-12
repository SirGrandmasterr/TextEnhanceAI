"""Shared contract for text-editing backends (local Ollama or a remote relay).

Every backend exposes the same small surface so the UI does not need to know
where the model runs:

- ``display_name``: short human label used in status messages.
- ``list_models()``: sorted model names, or raise ``BackendUnavailable``.
- ``connection_summary()``: one-line connection state after ``list_models``.
- ``no_models_hint()``: what the user should do when no model is listed.
- ``stream_edit(model, instruction, text, cancel_event, on_progress=None)``:
  return the edited document or raise ``EditCancelled``/``BackendUnavailable``.
"""

import re


class BackendUnavailable(RuntimeError):
    """Raised when a backend cannot be reached or cannot complete a request."""


class EditCancelled(RuntimeError):
    """Raised when the user cancels an active generation."""


class OutputTruncated(BackendUnavailable):
    """Raised when the model stopped because the output token limit was hit."""


SYSTEM_PROMPT = (
    "You are a careful text editor. Apply only the requested edits. Preserve "
    "the original language, meaning, paragraphs, line breaks, quotations, and "
    "formatting unless the instruction explicitly requires changing them. Make "
    "the smallest necessary changes. Return only the complete edited text, "
    "without commentary, labels, or Markdown fences."
)

# Deterministic, conservative sampling shared by every backend.
DEFAULT_MAX_TOKENS = 4096
TEMPERATURE = 0.1
TOP_P = 0.9

_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_OPEN_THINK = re.compile(r"^\s*<think>.*", re.DOTALL)


def build_messages(instruction, text):
    """Return the chat messages used for one editing request."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Instruction:\n{0}\n\nText:\n{1}".format(instruction, text),
        },
    ]


def strip_thinking(text):
    """Remove ``<think>...</think>`` reasoning blocks some models emit."""
    if "<think>" not in text:
        return text
    cleaned = _THINK_BLOCK.sub("", text)
    if "</think>" not in cleaned and "<think>" in cleaned:
        # Unterminated block: the whole remainder is reasoning, keep nothing.
        cleaned = _OPEN_THINK.sub("", cleaned)
    return cleaned.lstrip("\n")


def truncated_message(model):
    """Return the user-facing explanation for a length-limited response."""
    return (
        "{0} stopped before finishing because the output token limit was "
        "reached. Edit a shorter passage or raise the output token limit."
    ).format(model)
