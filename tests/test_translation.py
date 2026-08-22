from __future__ import annotations

import asyncio

import pytest

from app.services.translation import (
    MockTranslationProvider,
    ModelTranslationProvider,
    TranslationNotConfigured,
    configured_translation_provider,
    validate_translation_result,
)


def test_mock_provider_is_explicit_and_does_not_need_network():
    result = asyncio.run(
        MockTranslationProvider().translate("Original title", "Original summary")
    )

    assert result.translated_title == "【模拟译文】Original title"
    assert result.translated_text == "【模拟中文摘要】Original summary"
    assert result.provider == "mock · 非真实翻译"
    assert result.is_mock is True


def test_auto_provider_requires_a_configured_engine(monkeypatch):
    for key in ("OLLAMA_MODEL", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRANSLATION_PROVIDER", "auto")

    with pytest.raises(TranslationNotConfigured):
        configured_translation_provider()


def test_auto_provider_prefers_configured_ollama(monkeypatch):
    monkeypatch.setenv("TRANSLATION_PROVIDER", "auto")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen-local")
    monkeypatch.setenv("LLM_BASE_URL", "https://cloud.example/v1")
    monkeypatch.setenv("LLM_MODEL", "cloud-model")

    provider = configured_translation_provider()

    assert isinstance(provider, ModelTranslationProvider)
    assert provider.client.base_url == "http://127.0.0.1:11434/v1"
    assert provider.client.model == "qwen-local"


def test_explicit_openai_compatible_provider_and_invalid_mode(monkeypatch):
    monkeypatch.setenv("TRANSLATION_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://cloud.example/v1")
    monkeypatch.setenv("LLM_MODEL", "cloud-model")
    provider = configured_translation_provider()
    assert isinstance(provider, ModelTranslationProvider)
    assert provider.client.model == "cloud-model"

    monkeypatch.setenv("TRANSLATION_PROVIDER", "mystery")
    with pytest.raises(ValueError, match="TRANSLATION_PROVIDER"):
        configured_translation_provider()


def test_translation_rejects_empty_provider_title():
    with pytest.raises(ValueError, match="有效标题"):
        validate_translation_result(
            {"translated_title": " ", "translated_text": "text"},
            provider="model",
        )


def test_translation_rejects_non_string_text():
    with pytest.raises(ValueError, match="无效正文"):
        validate_translation_result(
            {"translated_title": "标题", "translated_text": ["bad"]},
            provider="model",
        )
