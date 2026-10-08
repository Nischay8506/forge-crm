"""Provider clients. Groq, Gemini and Anthropic all expose an OpenAI-compatible
chat-completions endpoint, so one small httpx client covers all three."""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import get_settings


@dataclass
class ProviderResponse:
    text: str
    input_tokens: int
    output_tokens: int


class Provider(Protocol):
    name: str

    async def chat(
        self, model: str, messages: list[dict[str, str]], **kwargs: Any
    ) -> ProviderResponse: ...


class OpenAICompatibleProvider:
    def __init__(self, name: str, base_url: str, api_key_setting: str):
        self.name = name
        self.base_url = base_url
        self.api_key_setting = api_key_setting

    def _key(self) -> str:
        key = getattr(get_settings(), self.api_key_setting)
        if not key:
            raise RuntimeError(f"{self.api_key_setting.upper()} is not set")
        return key

    async def chat(
        self, model: str, messages: list[dict[str, str]], **kwargs: Any
    ) -> ProviderResponse:
        body: dict[str, Any] = {"model": model, "messages": messages, **kwargs}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._key()}"},
                json=body,
            )
            r.raise_for_status()
            data = r.json()
        usage = data.get("usage") or {}
        return ProviderResponse(
            text=data["choices"][0]["message"]["content"] or "",
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
        )


PROVIDERS: dict[str, Provider] = {
    "groq": OpenAICompatibleProvider("groq", "https://api.groq.com/openai/v1", "groq_api_key"),
    "gemini": OpenAICompatibleProvider(
        "gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini_api_key"
    ),
    "anthropic": OpenAICompatibleProvider(
        "anthropic", "https://api.anthropic.com/v1", "anthropic_api_key"
    ),
}
