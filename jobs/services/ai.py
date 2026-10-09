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
        # Keep product AI on one provider so behavior and costs are predictable.
        if not settings.DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY is required for product AI.")
        self.api_key = settings.DEEPSEEK_API_KEY
        self.base_url = "https://api.deepseek.com"
        self.model = model or settings.DEEPSEEK_MODEL

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    def generate(
        self,
        prompt: str,
        max_tokens: int = 1500,
        temperature: float = 0.7,
        *,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        system_message: Optional[str] = None,
    ) -> str:
        """Send prompt to chat completions API and return text output."""
        client = self.client
        if timeout is not None or max_retries is not None:
            options = {}
            if timeout is not None: options["timeout"] = timeout
            if max_retries is not None: options["max_retries"] = max_retries
            client = client.with_options(**options)
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_message or "You are a helpful assistant for job applications."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body={"thinking": {"type": "disabled"}},
        )
        content = resp.choices[0].message.content if resp.choices else None
        return (content or "No content returned from the model.").strip()
