from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Protocol

from app.services.providers import OpenAICompatibleProvider


class TranslationNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class TranslationResult:
    translated_title: str
    translated_text: str | None
    provider: str
    is_mock: bool = False


class TranslationProvider(Protocol):
    name: str

    async def translate(
        self,
        title: str,
        text: str,
        target_language: str = "zh-CN",
    ) -> TranslationResult: ...


class MockTranslationProvider:
    name = "mock · 非真实翻译"

    async def translate(
        self,
        title: str,
        text: str,
        target_language: str = "zh-CN",
    ) -> TranslationResult:
        return TranslationResult(
            translated_title=f"【模拟译文】{title}",
            translated_text=f"【模拟中文摘要】{text}" if text else None,
            provider=self.name,
            is_mock=True,
        )


class ModelTranslationProvider:
    def __init__(self, client: OpenAICompatibleProvider):
        self.client = client
        self.name = client.name

    async def translate(
        self,
        title: str,
        text: str,
        target_language: str = "zh-CN",
    ) -> TranslationResult:
        raise NotImplementedError


def _ollama_provider() -> ModelTranslationProvider:
    model = os.getenv("OLLAMA_MODEL", "").strip()
    if not model:
        raise TranslationNotConfigured("尚未配置 OLLAMA_MODEL。")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").strip()
    client = OpenAICompatibleProvider(base_url, model, os.getenv("OLLAMA_API_KEY") or "ollama")
    return ModelTranslationProvider(client)


def _openai_compatible_provider() -> ModelTranslationProvider:
    base_url = os.getenv("LLM_BASE_URL", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not base_url or not model:
        raise TranslationNotConfigured(
            "尚未配置 LLM_BASE_URL 和 LLM_MODEL。"
        )
    client = OpenAICompatibleProvider(base_url, model, os.getenv("LLM_API_KEY") or "ollama")
    return ModelTranslationProvider(client)


def configured_translation_provider() -> TranslationProvider:
    mode = os.getenv("TRANSLATION_PROVIDER", "auto").strip().lower()
    if mode == "mock":
        return MockTranslationProvider()
    if mode == "ollama":
        return _ollama_provider()
    if mode == "openai-compatible":
        return _openai_compatible_provider()
    if mode == "auto":
        if os.getenv("OLLAMA_MODEL", "").strip():
            return _ollama_provider()
        if os.getenv("LLM_BASE_URL", "").strip() and os.getenv("LLM_MODEL", "").strip():
            return _openai_compatible_provider()
        raise TranslationNotConfigured(
            "尚未配置翻译模型。可设置 OLLAMA_MODEL，或设置 LLM_BASE_URL 和 LLM_MODEL。"
        )
    raise ValueError(f"不支持的 TRANSLATION_PROVIDER: {mode}")
