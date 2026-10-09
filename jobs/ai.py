import json
import logging
from django.conf import settings
from openai import OpenAI
from jobs.services.json_utils import parse_json_object

logger = logging.getLogger(__name__)


class JobParser:
    def __init__(self):
        if not settings.DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY is required for job parsing.")
        self.client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )

    def parse(self, text: str) -> dict:
        """
        Parses raw job text into structured data using DeepSeek.
        Returns a dictionary with keys matching Job model and raw_data fields.
        """
        prompt = """
        You are an expert job post structured data extractor.
        Your goal is to extract specific fields from the provided job post text and return them in a strict JSON format.

        Extract the following fields:
        - title: The job title.
        - organization_name: The name of the hiring organization.
        - organization_description: A brief description of the organization.
        - description: The "Mission" or role overview (what they will do).
        - requirements: The "Profile" or requirements (skills, experience).
        - impact: Specific impact the role will drive (if mentioned separately).
        - benefits: Salary, perks, benefits, culture notes.
        - location: Remote status or specific location.
        - salary_min: Minimum salary (number only).
        - salary_max: Maximum salary (number only).
        - salary_currency: Currency code (e.g., USD).
        - application_url: URL to apply.
        - application_email: Email to apply.
        - how_to_apply_text: Specific instructions on how to apply.

        Guidelines:
        - Clean up the text (remove artifacts like "Jobs • Senior Web Manager...").
        - Format `description` and `requirements` as clean HTML (paragraphs <p>, headers <h3>, lists <ul>/<li>).
        - If a field is not found, return null (or reasonable default).
        - Ensure `salary_min` and `salary_max` are integers or floats.

        Input Text:
        {text}
        """

        try:
            response = self.client.chat.completions.create(
                model=settings.DEEPSEEK_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant that extracts structured data from job posts.",
                    },
                    {"role": "user", "content": prompt.format(text=text)},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,  # Low temperature for consistent extraction
                extra_body={"thinking": {"type": "disabled"}},
            )

            content = response.choices[0].message.content
            return parse_json_object(content)

        except Exception as e:
            logger.error(f"Error parsing job text with AI: {e}")
            return {}
