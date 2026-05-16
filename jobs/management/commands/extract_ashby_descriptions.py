"""
Extract job descriptions from Ashby api_response in raw_data.
Targets jobs that were previously filled with fallback text.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from jobs.models import Job
from bs4 import BeautifulSoup


def extract_and_format_html(html):
    """Extract text from HTML and format as proper HTML."""
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        result_parts = []
        processed_elements = set()
        
        # Walk through the HTML tree, processing top-level block elements only
        for element in soup.children:
            if isinstance(element, str):
                text = element.strip()
                if text:
                    result_parts.append(f"<p>{text}</p>")
                continue
            
            if element.name == 'p':
                text = element.get_text(strip=True)
                if text:
                    result_parts.append(f"<p>{text}</p>")
                processed_elements.add(id(element))
                
            elif element.name in ['ul', 'ol']:
                list_items = []
                for li in element.find_all('li', recursive=False):
                    text = li.get_text(strip=True)
                    if text:
                        list_items.append(f"<li>{text}</li>")
                    processed_elements.add(id(li))
                
                if list_items:
                    tag = 'ul' if element.name == 'ul' else 'ol'
                    result_parts.append(f"<{tag}>{''.join(list_items)}</{tag}>")
                processed_elements.add(id(element))
                
            elif element.name == 'h1':
                text = element.get_text(strip=True)
                if text:
                    result_parts.append(f"<h2>{text}</h2>")
                processed_elements.add(id(element))
                
            elif element.name in ['h2', 'h3', 'h4', 'h5', 'h6']:
                text = element.get_text(strip=True)
                if text:
                    result_parts.append(f"<{element.name}>{text}</{element.name}>")
                processed_elements.add(id(element))
                
            elif element.name == 'div':
                # Process div contents
                for child in element.children:
                    if isinstance(child, str):
                        text = child.strip()
                        if text:
                            result_parts.append(f"<p>{text}</p>")
                    elif hasattr(child, 'name'):
                        if child.name == 'p':
                            text = child.get_text(strip=True)
                            if text:
                                result_parts.append(f"<p>{text}</p>")
                        elif child.name in ['ul', 'ol']:
                            list_items = []
                            for li in child.find_all('li', recursive=False):
                                text = li.get_text(strip=True)
                                if text:
                                    list_items.append(f"<li>{text}</li>")
                            if list_items:
                                tag = 'ul' if child.name == 'ul' else 'ol'
                                result_parts.append(f"<{tag}>{''.join(list_items)}</{tag}>")
                processed_elements.add(id(element))
        
        html_out = "".join(result_parts)
        return html_out if html_out else soup.get_text(separator='\n').strip()
    except Exception as e:
        return html


class Command(BaseCommand):
    help = 'Extract real descriptions from Ashby api_response data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without saving'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of jobs to process'
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        
        # Find jobs from Ashby with api_response containing descriptionHtml
        jobs = Job.objects.filter(source='ashby')
        
        updated = 0
        total = 0
        
        for job in jobs:
            if not job.raw_data or 'api_response' not in job.raw_data:
                continue
            
            api = job.raw_data.get('api_response', {})
            if not isinstance(api, dict) or 'descriptionHtml' not in api:
                continue
            
            desc_html = api.get('descriptionHtml')
            if not desc_html:
                continue
            
            total += 1
            
            # Extract and format as HTML
            text = extract_and_format_html(desc_html)
            if not text or len(text) < 50:
                continue
            
            # Always update if API has better (longer) text than current description
            current_desc = job.description or ""
            
            # Skip if current description is already good (longer than API text)
            if len(current_desc) > len(text):
                continue
            
            if not dry_run:
                job.description = text
                job.save(update_fields=['description'])
                updated += 1
            else:
                self.stdout.write(f"Would update job {job.id}: {job.title[:50]}")
            
            if limit and updated >= limit:
                break
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {total} Ashby jobs. Updated {updated} with real descriptions."
            )
        )
