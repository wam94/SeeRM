"""OpenAI helper for the SayRM service."""

from __future__ import annotations

from typing import List, Optional

from openai import OpenAI

from ..config import SayRMSettings


class LLMClient:
    """Wraps OpenAI chat completions with sane defaults."""

    def __init__(self, settings: SayRMSettings) -> None:
        """Initialize the OpenAI client using provided settings."""
        self._client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature

    def run_chat(self, messages: List[dict], *, response_format: Optional[dict] = None) -> str:
        """Execute a chat completion and return the text content."""
        params: dict = {
            "model": self.model,
            "messages": messages,
        }
        # Some newer models (e.g. gpt-5-mini) only support the default
        # temperature of 1. Passing a custom value triggers a 400.
        if not self.model.startswith("gpt-5"):
            params["temperature"] = self.temperature
        if response_format is not None:
            params["response_format"] = response_format

        completion = self._client.chat.completions.create(**params)
        return completion.choices[0].message.content or ""
