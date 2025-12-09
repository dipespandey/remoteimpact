import os
from typing import Optional

from openai import OpenAI

from django.conf import settings


class AIClient:
    """Light wrapper around OpenAI Responses API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = settings.OPENAI_API_KEY
        self.base_url = "https://api.openai.com/v1"
        self.model = model or os.getenv("AI_MODEL", "gpt-5.1")
        kwargs = {
            "api_key": self.api_key,
            "base_url": self.base_url,
        }

        self.client = OpenAI(**kwargs)

    def generate(self, prompt: str) -> str:
        """Send prompt to Responses API and normalize text output."""
        resp = self.client.responses.create(
            model=self.model,
            input=prompt,
            max_output_tokens=600,
        )
        content = resp.output_text if hasattr(resp, "output_text") else None
        if callable(content):
            content = content()
        return (content or "No content returned from the model.").strip()
