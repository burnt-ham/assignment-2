"""Unit tests: keys stay hidden."""

import httpx
import pytest

from course_assistant.config import ServiceConfig, load_settings, redact
from course_assistant.services import OpenAIChatModel, ServiceError


def test_redact_hides_keys_and_bearer_tokens():
    text = 'curl -H "Authorization: Bearer abc123" with key abc123'
    assert "abc123" not in redact(text, ["abc123"])


def test_settings_read_from_env_file(tmp_path, monkeypatch):
    for name in ["LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "CLASS_API_KEY", "USE_RERANK", "RETRIEVAL_MODE"]:
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("LLM_BASE_URL=http://example.invalid:9001/v1/\nLLM_MODEL=vision-model\nCLASS_API_KEY=dummy-key\nUSE_RERANK=false\n")
    settings = load_settings(env)
    assert settings.llm.base_url == "http://example.invalid:9001/v1"
    assert settings.llm.api_key == "dummy-key" and settings.llm.configured
    assert not settings.text_embed.configured
    assert settings.use_rerank is False and settings.retrieval_mode == "hybrid"


def test_service_errors_never_include_the_key(monkeypatch):
    def fake_post(url, json, headers, timeout):
        return httpx.Response(401, text=f"bad token: {headers['Authorization']}")

    monkeypatch.setattr(httpx, "post", fake_post)
    model = OpenAIChatModel(ServiceConfig("http://example.invalid/v1", "secret-key-123", "m"))
    with pytest.raises(ServiceError) as error:
        model.complete("system", "hello")
    assert "secret-key-123" not in str(error.value)


def test_unreachable_service_gives_a_readable_error(monkeypatch):
    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    model = OpenAIChatModel(ServiceConfig("http://example.invalid/v1", "k", "m"))
    with pytest.raises(ServiceError, match="couldn't reach the chat model service"):
        model.complete("system", "hello")
