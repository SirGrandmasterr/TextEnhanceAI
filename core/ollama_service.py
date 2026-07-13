"""Thread-friendly Ollama access with streaming cancellation."""


class OllamaUnavailable(RuntimeError):
    """Raised when the Ollama package or service is unavailable."""


class EditCancelled(RuntimeError):
    """Raised when the user cancels an active generation."""


try:
    from ollama import Client
except ImportError:  # pragma: no cover - environment-dependent
    Client = None


SYSTEM_PROMPT = (
    "You are a careful text editor. Apply only the requested edits. Preserve "
    "the original language, meaning, paragraphs, line breaks, quotations, and "
    "formatting unless the instruction explicitly requires changing them. Make "
    "the smallest necessary changes. Return only the complete edited text, "
    "without commentary, labels, or Markdown fences."
)


class OllamaService:
    """Provide model discovery and cancellable text editing."""

    def __init__(self, client=None):
        self.client = client
        if self.client is None and Client is not None:
            self.client = Client()

    @property
    def package_available(self):
        """Return whether the Python client is installed."""
        return Client is not None or self.client is not None

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

    def stream_edit(self, model, instruction, text, cancel_event):
        """Return an edited document while honoring a cancellation event."""
        if self.client is None:
            raise OllamaUnavailable("The Ollama Python package is not installed.")
        if cancel_event.is_set():
            raise EditCancelled("Editing was cancelled.")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Instruction:\n{0}\n\nText:\n{1}".format(
                    instruction, text
                ),
            },
        ]
        try:
            stream = self.client.chat(
                model=model,
                messages=messages,
                stream=True,
                options={
                    "num_predict": 4096,
                    "temperature": 0.1,
                    "top_p": 0.9,
                },
            )
            chunks = []
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
        except EditCancelled:
            raise
        except Exception as exc:
            raise OllamaUnavailable("Ollama could not complete the edit: {0}".format(exc)) from exc

        result = "".join(chunks)
        if not result.strip():
            raise OllamaUnavailable("Ollama returned an empty response.")
        return result
