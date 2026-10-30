"""Read-only evaluation: never imports prospects or starts outreach."""
import json
from datetime import date
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from jobs.services.prospect_discovery import assess_page, discover_prospects, VERSION
from jobs.services.safe_fetch import FetchResult


class Command(BaseCommand):
    help = 'Evaluate frozen labelled pages, or run bounded live discovery without database writes.'

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument('--dataset', type=Path, help='JSON list of labelled page snapshots; no network calls.')
        mode.add_argument('--live', choices=['climate','health','ai','impact'])
        parser.add_argument('--output', type=Path)

    def handle(self, *args, **options):
        if options['live']:
            report = discover_prospects(options['live'], persist=False)
            report['evaluation_note'] = 'Unlabelled live sample. Rule scores are not measured precision; no database writes.'
        else:
            try:
                if options['dataset'].stat().st_size > 5_000_000:
                    raise ValueError('Dataset too large')
                dataset = json.loads(options['dataset'].read_text())
                cases = dataset['cases']
                today = date.fromisoformat(dataset['as_of'])
                if not isinstance(cases, list) or not cases or len(cases)>200:
                    raise ValueError('Expected 1–200 cases')
                results = []
                tp=fp=fn=correct=0
                for case in cases:
                    result=assess_page(case['url'], case['sector'], FetchResult(case.get('fetch_status','ok'), text=case.get('html','')), today=today)
                    predicted=result['status'];expected=case['expected_status']
                    if expected not in ('promising','needs_review','unsuitable','unverified'):
                        raise ValueError('Unknown expected status')
                    correct += predicted==expected
                    tp += predicted=='promising' and expected=='promising'
                    fp += predicted=='promising' and expected!='promising'
                    fn += predicted!='promising' and expected=='promising'
                    results.append({'id':case['id'],'expected':expected,'actual':predicted,'reasons':result['reasons']})
                report={'version':VERSION,'dataset':dataset.get('description',''), 'cases':len(cases),'correct':correct,
                        'promising_precision':tp/(tp+fp) if tp+fp else None,
                        'promising_recall':tp/(tp+fn) if tp+fn else None,'false_positives':fp,'results':results,
                        'evaluation_note':'Valid only for this labelled sample; synthetic regressions do not establish live lead precision.'}
            except (OSError,ValueError,TypeError,KeyError) as exc:
                raise CommandError('Invalid evaluation dataset') from exc
        rendered=json.dumps(report,indent=2)
        if options['output']:
            options['output'].write_text(rendered+'\n')
        self.stdout.write(rendered)
