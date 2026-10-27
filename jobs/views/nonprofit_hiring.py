"""The same public evidence in a readable page and an agent-friendly JSON file."""
import json
from datetime import datetime

from django.http import JsonResponse
from django.views.generic import TemplateView
from django.views.decorators.http import require_safe

from jobs.services.nonprofit_hiring import get_snapshot


class NonprofitHiringLocationsView(TemplateView):
    template_name = "jobs/nonprofit_hiring_locations.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        data = get_snapshot()
        schema = {
            "@context": "https://schema.org", "@type": "Dataset", "name": data["title"],
            "url": data["url"], "description": "Dated Remote Impact catalogue country-label counts, including unknowns and classification limits. Not verified applicant eligibility or a census of vacancies.",
            "creator": {"@type": "Organization", "name": data["publisher"]},
            "dateModified": data["observed_at"], "isAccessibleForFree": True,
            "measurementTechnique": "Aggregation of stored catalogue fields; see the report methodology.",
            "distribution": {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": data["data_url"]},
            "variableMeasured": ["Catalogue listing records by country-label group", "Catalogue employer count"],
        }
        context.update(report=data, observed_at=datetime.fromisoformat(data["observed_at"]),
                       schema_json=json.dumps(schema))
        return context


@require_safe
def nonprofit_hiring_locations_data(request):
    response = JsonResponse(get_snapshot(), json_dumps_params={"ensure_ascii": False})
    response["X-Robots-Tag"] = "noindex"
    # Cache is held in the aggregate service, so page and data share one timestamp.
    response["Cache-Control"] = "public, max-age=60"
    return response
