"""Small helpers for parsing structured responses from language models."""

import json
from typing import Any


def parse_json_object(content: str | None) -> dict[str, Any]:
    """Parse the first valid JSON object from a model response.

    JSON mode usually returns a bare object, but models can still add a
    markdown fence or a short sentence around it. ``raw_decode`` stops at the
    end of the object and avoids greedy matching when fields contain braces.
    """
    text = (content or "").strip()
    if not text:
        raise ValueError("Empty model response")

    decoder = json.JSONDecoder()
    for start, character in enumerate(text):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("No valid JSON object in model response")
