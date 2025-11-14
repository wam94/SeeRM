"""OpenAI helper for the SayRM service."""

from __future__ import annotations

from typing import List, Optional

from openai import OpenAI

from ..config import SayRMSettings


class LLMClient:
    """Wraps OpenAI chat completions with sane defaults."""

    def __init__(self, settings: SayRMSettings) -> None:
        self._client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature

    def run_chat(self, messages: List[dict], *, response_format: Optional[dict] = None) -> str:
        """Execute a chat completion and return the text content."""
        completion = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            response_format=response_format,
        )
        return completion.choices[0].message.content or ""

