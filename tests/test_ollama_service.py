"""Tests for model discovery and streaming cancellation."""

import threading
from types import SimpleNamespace

import pytest

from core.ollama_service import EditCancelled, OllamaService, OllamaUnavailable


class FakeClient:
    def __init__(self, chunks=None, models=None):
        self.chunks = chunks or []
        self.models = models or []
        self.chat_arguments = None

    def list(self):
        return SimpleNamespace(
            models=[SimpleNamespace(model=name) for name in self.models]
        )

    def chat(self, **kwargs):
        self.chat_arguments = kwargs
        return iter(
            SimpleNamespace(message=SimpleNamespace(content=chunk))
            for chunk in self.chunks
        )


def test_list_models_returns_unique_sorted_names():
    service = OllamaService(FakeClient(models=["z-model", "a-model", "a-model"]))
    assert service.list_models() == ["a-model", "z-model"]


def test_stream_edit_combines_chunks_and_uses_deterministic_options():
    client = FakeClient(chunks=["Edited ", "text."])
    service = OllamaService(client)

    result = service.stream_edit(
        "local-model", "Fix grammar.", "Original text.", threading.Event()
    )

    assert result == "Edited text."
    assert client.chat_arguments["stream"] is True
    assert client.chat_arguments["options"]["temperature"] == 0.1


def test_stream_edit_text_first_puts_the_text_before_the_instruction():
    client = FakeClient(chunks=["Edited."])
    service = OllamaService(client)

    service.stream_edit("local-model", "Fix grammar.", "Original text.", threading.Event(), text_first=True)

    system, user = client.chat_arguments["messages"]
    assert user["content"] == "Text:\nOriginal text.\n\nInstruction:\nFix grammar."
    assert system["content"].endswith(" The instruction follows the text.")


def test_cancelled_before_request_does_not_call_client():
    client = FakeClient(chunks=["Unused"])
    service = OllamaService(client)
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(EditCancelled):
        service.stream_edit("model", "instruction", "text", cancelled)
    assert client.chat_arguments is None


def test_cancellation_during_stream_discards_partial_output():
    cancelled = threading.Event()

    class CancellingClient(FakeClient):
        def chat(self, **kwargs):
            def stream():
                yield {"message": {"content": "partial"}}
                cancelled.set()
                yield {"message": {"content": "ignored"}}

            return stream()

    service = OllamaService(CancellingClient())
    with pytest.raises(EditCancelled):
        service.stream_edit("model", "instruction", "text", cancelled)


def test_connection_failure_has_actionable_error():
    class BrokenClient(FakeClient):
        def list(self):
            raise ConnectionError("offline")

    with pytest.raises(OllamaUnavailable, match="Ensure the Ollama service is running"):
        OllamaService(BrokenClient()).list_models()
