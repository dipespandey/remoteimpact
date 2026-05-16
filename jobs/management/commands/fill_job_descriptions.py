"""
Management command to extract and format job descriptions from raw_data JSON.
This fills in empty description/requirements/benefits fields without using AI.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from jobs.models import Job
import re
from html.parser import HTMLParser
from bs4 import BeautifulSoup


class HTMLToText(HTMLParser):
    """Convert HTML to plain text."""
    def __init__(self):
        super().__init__()
        self.text = []
    
    def handle_data(self, data):
        self.text.append(data)
    
    def get_text(self):
        return ' '.join(self.text).strip()


def extract_text_from_html(html):
    """Convert HTML to plain text."""
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        return soup.get_text(separator='\n').strip()
    except:
        return html


def extract_from_raw_data(raw_data):
    """Extract description fields from raw_data JSON."""
    if not raw_data:
        return {}
    
    result = {}
    
    # Check if this is an Ashby job with api_response
    if 'api_response' in raw_data and isinstance(raw_data['api_response'], dict):
        api = raw_data['api_response']
        if 'descriptionHtml' in api and api['descriptionHtml']:
            text = extract_text_from_html(api['descriptionHtml'])
            if text and len(text) > 20:
                result['description'] = text
                return result
    
    # Try common description field names
    desc_keys = ['description_html', 'job_description', 'description', 'body', 
                 'content', 'job_summary', 'description_summary', 'job_details']
    
    for key in desc_keys:
        if key in raw_data and raw_data[key]:
            text = extract_text_from_html(raw_data[key])
            if text and len(text) > 20:
                result['description'] = text
                break
    
    # Try requirements/qualifications
    req_keys = ['qualifications', 'requirements', 'job_qualifications', 
                'requirements_summary', 'skills_required', 'what_we_are_looking_for']
    
    for key in req_keys:
        if key in raw_data and raw_data[key]:
            text = extract_text_from_html(raw_data[key])
            if text and len(text) > 20:
                result['requirements'] = text
                break
    
    # Try benefits/compensation
    benefits_keys = ['benefits', 'compensation', 'benefits_summary', 'perks',
                    'salary', 'what_we_offer', 'salary_range']
    
    for key in benefits_keys:
        if key in raw_data and raw_data[key]:
            text = extract_text_from_html(raw_data[key])
            if text and len(text) > 10:
                result['benefits'] = text
                break
    
    return result


def format_as_html(text):
    """Format text as HTML with bullet points if it looks like a list."""
    if not text:
        return ""
    
    lines = text.split('\n')
    result = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Check if it looks like a bullet point
        if re.match(r'^[-•*]\s', line) or re.match(r'^\d+\.\s', line):
            # Already has bullet, keep it
            clean_line = re.sub(r'^[-•*\d.]\s+', '', line)
            result.append(f"<li>{clean_line}</li>")
        elif len(line) > 50:
            # Long line, treat as paragraph
            result.append(f"<p>{line}</p>")
        else:
            # Short line, might be a bullet point
            if not line.endswith(':'):
                result.append(f"<li>{line}</li>")
    
    # Wrap lists in <ul>
    html = ""
    in_list = False
    
    for item in result:
        if item.startswith('<li>'):
            if not in_list:
                html += "<ul>"
                in_list = True
            html += item
        else:
            if in_list:
                html += "</ul>"
                in_list = False
            html += item
    
    if in_list:
        html += "</ul>"
    
    return html


class Command(BaseCommand):
    help = 'Extract and format job descriptions from raw_data JSON'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of jobs to process'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without saving'
        )

    @transaction.atomic
    def handle(self, *args, **options):
        limit = options['limit']
        dry_run = options['dry_run']
        
        # Find jobs with empty or very short descriptions
        jobs = Job.objects.filter(
            description__isnull=True
        ) | Job.objects.filter(
            description=''
        ) | Job.objects.filter(
            description__regex=r'^\s*$'
        )
        
        if limit:
            jobs = jobs[:limit]
        
        total = jobs.count()
        self.stdout.write(f"Found {total} jobs with empty descriptions")
        
        updated = 0
        
        for i, job in enumerate(jobs, 1):
            extracted = extract_from_raw_data(job.raw_data)
            
            if not extracted:
                # Generate fallback description
                fallback = f"This is a {job.get_job_type_display()} role at {job.organization.name}."
                if job.category:
                    fallback += f" {job.category.name} impact position."
                extracted['description'] = fallback
            
            # Update fields
            if 'description' in extracted:
                job.description = format_as_html(extracted['description'])
            
            if 'requirements' in extracted:
                job.requirements = format_as_html(extracted['requirements'])
            elif job.description:
                # Use description as requirements if not available
                job.requirements = job.description
            
            if 'benefits' in extracted:
                job.benefits = format_as_html(extracted['benefits'])
            
            if not dry_run:
                job.save(update_fields=['description', 'requirements', 'benefits'])
                updated += 1
            
            if i % 50 == 0:
                self.stdout.write(f"  Processed {i}/{total}...")
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully updated {updated} jobs with descriptions"
            )
        )
