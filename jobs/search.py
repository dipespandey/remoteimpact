from django.contrib.postgres.search import SearchVector
from django.db.models import TextField
from django.db.models.functions import Cast


def weighted_job_search_vector():
    """Weighted lexical search vector for public job search."""
    return (
        # Highest signal: the role title should dominate broad searches.
        SearchVector("title", config="english", weight="A")
        # Structured labels and extracted skills are usually concise and intentional.
        + SearchVector(Cast("skills", TextField()), config="english", weight="B")
        + SearchVector("experience_level", config="english", weight="B")
        + SearchVector("job_type", config="english", weight="B")
        # Medium signal: requirements and impact summary are focused but longer.
        + SearchVector("requirements", config="english", weight="C")
        + SearchVector("impact", config="english", weight="C")
        + SearchVector("education_level", config="english", weight="C")
        # Lowest signal: long descriptive text can mention many secondary terms.
        + SearchVector("description", config="english", weight="D")
        + SearchVector("benefits", config="english", weight="D")
        + SearchVector("company_description", config="english", weight="D")
        + SearchVector("country", config="english", weight="D")
    )
