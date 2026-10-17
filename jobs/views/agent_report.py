import json
from pathlib import Path
from django.conf import settings
from django.views.generic import TemplateView


class AgentEvidenceReportView(TemplateView):
    template_name = 'jobs/agent_evidence_report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        data = json.loads((Path(settings.BASE_DIR) / 'static/reports/remote-impact-jobs-2026-10-17.json').read_text())
        context['report'] = data
        n = data['total_listings']
        context['metrics'] = [
            {'label':label, 'count':data['counts'].get(key,0),
             'percent':round(data['counts'].get(key,0)*100/n,1) if n else 0}
            for key,label in [('worldwide_source_evidence','Worldwide source evidence'),('publishable_annual_pay','Supported annual salary'),('stored_pay_amounts','Stored pay amounts (not necessarily source-supported)')]
        ]
        return context
