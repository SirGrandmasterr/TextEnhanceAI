"""Tests for building a backend service from settings (shared by UI and scripts)."""

import pytest

from core.ollama_service import OllamaService
from core.remote_service import RemoteService
from core.services import build_service
from core.settings import BACKEND_OLLAMA, BACKEND_REMOTE, AppSettings


def test_remote_service_takes_every_remote_setting(tmp_path):
    settings = AppSettings.load(tmp_path / "settings.json", environ={
        "TEAI_BACKEND": "remote",
        "TEAI_REMOTE_URL": "https://relay.example.com",
        "TEAI_REMOTE_API_KEY": "Bearer abc",
        "TEAI_REMOTE_MAX_TOKENS": "8192",
        "TEAI_REMOTE_THINKING": "yes",
    })

    service = build_service(settings)

    assert isinstance(service, RemoteService)
    assert service.base_url == "https://relay.example.com"
    assert service.api_key == "abc"
    assert service.max_tokens == 8192
    assert service.enable_thinking is True


def test_backend_argument_overrides_the_configured_backend(tmp_path):
    settings = AppSettings.load(tmp_path / "settings.json", environ={})
    assert settings.backend == BACKEND_OLLAMA

    assert isinstance(build_service(settings, BACKEND_REMOTE), RemoteService)
    assert isinstance(build_service(settings, BACKEND_OLLAMA), OllamaService)
    with pytest.raises(ValueError):
        build_service(settings, "cloud")
