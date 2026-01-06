import os
from typing import Optional

from openai import OpenAI

from django.conf import settings


class AIClient:
    """Light wrapper around DeepSeek/OpenAI-compatible chat API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        # Use DeepSeek by default (cheapest), fallback to Groq, then OpenAI
        if settings.DEEPSEEK_API_KEY:
            self.api_key = settings.DEEPSEEK_API_KEY
            self.base_url = "https://api.deepseek.com"
            self.model = model or "deepseek-chat"
        elif settings.GROQ_API_KEY:
            self.api_key = settings.GROQ_API_KEY
            self.base_url = "https://api.groq.com/openai/v1"
            self.model = model or "llama-3.1-70b-versatile"
        else:
            self.api_key = settings.OPENAI_API_KEY
            self.base_url = "https://api.openai.com/v1"
            self.model = model or "gpt-4o-mini"

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    def generate(self, prompt: str, max_tokens: int = 1500) -> str:
        """Send prompt to chat completions API and return text output."""
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant for job applications."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.7,
        )
        content = resp.choices[0].message.content if resp.choices else None
        return (content or "No content returned from the model.").strip()
