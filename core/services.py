"""Build a text-editing backend from persisted settings (no Tk involved).

The desktop app and command-line tools need the same ``RemoteService`` /
``OllamaService`` construction, so it lives here rather than in ``ui/``.
"""

from .ollama_service import OllamaService
from .remote_service import RemoteService
from .settings import BACKEND_OLLAMA, BACKEND_REMOTE


def build_service(settings, backend=None):
    """Return the backend service for ``backend`` (defaults to ``settings.backend``)."""
    backend = backend or settings.backend
    if backend == BACKEND_REMOTE:
        return RemoteService(
            settings.remote_url,
            settings.remote_api_key,
            max_tokens=settings.remote_max_tokens,
            enable_thinking=settings.remote_enable_thinking,
        )
    if backend == BACKEND_OLLAMA:
        return OllamaService()
    raise ValueError("Unknown backend: {0!r}".format(backend))
