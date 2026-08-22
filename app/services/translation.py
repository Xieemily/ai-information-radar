from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.models import Item, ItemTranslation
from app.services.providers import OpenAICompatibleProvider


class TranslationNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class TranslationResult:
    translated_title: str
    translated_text: str | None
    provider: str
    is_mock: bool = False


@dataclass(frozen=True)
class TranslationOutcome:
    translation: ItemTranslation
    cached: bool


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
        payload = await self.client.translate(title, text, target_language)
        return validate_translation_result(payload, self.name)


def validate_translation_result(
    payload: dict[str, Any],
    provider: str,
) -> TranslationResult:
    title = payload.get("translated_title")
    text = payload.get("translated_text")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("翻译服务没有返回有效标题")
    if text is not None and not isinstance(text, str):
        raise ValueError("翻译服务返回了无效正文")
    return TranslationResult(
        translated_title=title.strip(),
        translated_text=text.strip() if isinstance(text, str) and text.strip() else None,
        provider=provider,
    )


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


async def translate_item(
    session: Session,
    item: Item,
    *,
    provider: TranslationProvider | None = None,
    force: bool = False,
    target_language: str = "zh-CN",
) -> TranslationOutcome:
    if item.translation is not None and not force:
        return TranslationOutcome(item.translation, cached=True)

    selected_provider = provider or configured_translation_provider()
    result = await selected_provider.translate(
        item.title,
        item.clean_text or "",
        target_language,
    )
    if not isinstance(result.translated_title, str) or not result.translated_title.strip():
        raise ValueError("翻译服务没有返回有效标题")
    if result.translated_text is not None and not isinstance(result.translated_text, str):
        raise ValueError("翻译服务返回了无效正文")

    translation = item.translation or ItemTranslation(item=item)
    translation.target_language = target_language
    translation.translated_title = result.translated_title.strip()
    translation.translated_text = (
        result.translated_text.strip()
        if isinstance(result.translated_text, str) and result.translated_text.strip()
        else None
    )
    translation.provider = result.provider
    translation.is_mock = result.is_mock
    session.add(translation)
    session.commit()
    session.refresh(translation)
    return TranslationOutcome(translation, cached=False)
