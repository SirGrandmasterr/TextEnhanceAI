"""Thread-friendly Ollama access with streaming cancellation."""

from .backend import (
    DEFAULT_MAX_TOKENS,
    SYSTEM_PROMPT,
    TEMPERATURE,
    TOP_P,
    BackendUnavailable,
    EditCancelled,
    OutputTruncated,
    build_messages,
    strip_thinking,
    truncated_message,
)

__all__ = [
    "EditCancelled",
    "OllamaService",
    "OllamaUnavailable",
    "SYSTEM_PROMPT",
]


class OllamaUnavailable(BackendUnavailable):
    """Raised when the Ollama package or service is unavailable."""


try:
    from ollama import Client
except ImportError:  # pragma: no cover - environment-dependent
    Client = None


class OllamaService:
    """Provide model discovery and cancellable text editing via local Ollama."""

    display_name = "Ollama"
    backend_id = "ollama"

    def __init__(self, client=None, max_tokens=DEFAULT_MAX_TOKENS):
        self.client = client
        self.max_tokens = max_tokens
        if self.client is None and Client is not None:
            self.client = Client()

    @property
    def package_available(self):
        """Return whether the Python client is installed."""
        return Client is not None or self.client is not None

    def connection_summary(self):
        """Return a short label describing the last successful connection."""
        return "Connected"

    def no_models_hint(self):
        """Return guidance shown when the model list is empty."""
        return "Install a local Ollama model, then refresh the list."

    def list_models(self):
        """Return sorted local model names or raise OllamaUnavailable."""
        if self.client is None:
            raise OllamaUnavailable(
                "The Ollama Python package is not installed. Run: pip install ollama"
            )
        try:
            response = self.client.list()
        except Exception as exc:
            raise OllamaUnavailable(
                "Cannot connect to Ollama. Ensure the Ollama service is running."
            ) from exc

        items = getattr(response, "models", None)
        if items is None and isinstance(response, dict):
            items = response.get("models", [])

        names = []
        for item in items or []:
            if isinstance(item, dict):
                name = item.get("model") or item.get("name")
            else:
                name = getattr(item, "model", None) or getattr(item, "name", None)
            if name:
                names.append(str(name))
        return sorted(set(names))

    def stream_edit(self, model, instruction, text, cancel_event, on_progress=None):
        """Return an edited document while honoring a cancellation event.

        ``on_progress`` (optional) receives the number of characters received
        so far and is called from the worker thread.
        """
        if self.client is None:
            raise OllamaUnavailable("The Ollama Python package is not installed.")
        if cancel_event.is_set():
            raise EditCancelled("Editing was cancelled.")

        messages = build_messages(instruction, text)
        done_reason = None
        try:
            stream = self.client.chat(
                model=model,
                messages=messages,
                stream=True,
                options={
                    "num_predict": self.max_tokens,
                    "temperature": TEMPERATURE,
                    "top_p": TOP_P,
                },
            )
            chunks = []
            received = 0
            for response in stream:
                if cancel_event.is_set():
                    close = getattr(stream, "close", None)
                    if close:
                        close()
                    raise EditCancelled("Editing was cancelled.")
                message = getattr(response, "message", None)
                if message is None and isinstance(response, dict):
                    message = response.get("message", {})
                content = getattr(message, "content", None)
                if content is None and isinstance(message, dict):
                    content = message.get("content")
                if content:
                    chunks.append(content)
                    received += len(content)
                    if on_progress:
                        on_progress(received)
                reason = getattr(response, "done_reason", None)
                if reason is None and isinstance(response, dict):
                    reason = response.get("done_reason")
                if reason:
                    done_reason = reason
        except EditCancelled:
            raise
        except Exception as exc:
            raise OllamaUnavailable("Ollama could not complete the edit: {0}".format(exc)) from exc

        if done_reason == "length":
            raise OutputTruncated(truncated_message(model))
        result = strip_thinking("".join(chunks))
        if not result.strip():
            raise OllamaUnavailable("Ollama returned an empty response.")
        return result
