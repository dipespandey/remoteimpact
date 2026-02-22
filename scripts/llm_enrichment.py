#!/usr/bin/env python
"""
LLM Job Enrichment Script
Fetches job pages and uses Groq/DeepSeek to extract structured data
"""
import os
import sys
import json
import re
import time
import requests
from urllib.parse import urlparse

# Django setup
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'jobboard.settings')

import django
django.setup()

from django.db.models.functions import Length
from jobs.models import Job

# Groq API settings
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
GROQ_MODEL = "deepseek-r1-distill-llama-70b"

def fetch_page_content(url):
    """Fetch and extract text from a URL"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        
        # Simple HTML to text extraction
        from html.parser import HTMLParser
        
        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.skip_tags = {'script', 'style', 'nav', 'footer', 'header'}
                self.current_skip = 0
                
            def handle_starttag(self, tag, attrs):
                if tag in self.skip_tags:
                    self.current_skip += 1
                    
            def handle_endtag(self, tag):
                if tag in self.skip_tags:
                    self.current_skip -= 1
                    
            def handle_data(self, data):
                if self.current_skip == 0:
                    text = data.strip()
                    if text:
                        self.text.append(text)
                        
            def get_text(self):
                return '\n'.join(self.text)
        
        parser = TextExtractor()
        parser.feed(resp.text)
        text = parser.get_text()
        
        # Limit text length for LLM
        return text[:15000]
    except Exception as e:
        return f"Error fetching: {e}"

def extract_job_data(page_text, job_title):
    """Use Groq to extract structured job data"""
    if not GROQ_API_KEY:
        return None
        
    prompt = f"""Extract job posting information from this page content.

Job Title: {job_title}

Page Content:
{page_text[:12000]}

Extract and return ONLY a JSON object with these fields:
- description: Full job description (2-4 paragraphs, include responsibilities)
- requirements: List of requirements/qualifications as bullet points
- salary_min: Minimum salary as integer (null if not mentioned)
- salary_max: Maximum salary as integer (null if not mentioned)  
- salary_currency: Currency code like USD, EUR, GBP (null if not mentioned)
- location: Job location (null if not clear)
- job_type: One of: full_time, part_time, contract, internship (null if unclear)
- experience_level: One of: entry, mid, senior, executive (null if unclear)

Return ONLY valid JSON, no markdown, no explanation."""

    try:
        resp = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {GROQ_API_KEY}',
                'Content-Type': 'application/json'
            },
            json={
                'model': GROQ_MODEL,
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.1,
                'max_tokens': 2000
            },
            timeout=60
        )
        resp.raise_for_status()
        content = resp.json()['choices'][0]['message']['content']
        
        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            return json.loads(json_match.group())
        return None
    except Exception as e:
        print(f"  LLM Error: {e}")
        return None

def main():
    # Get jobs with empty/short descriptions
    jobs = Job.objects.annotate(
        desc_len=Length('description')
    ).filter(
        desc_len__lt=100,
        is_active=True,
        application_url__isnull=False
    ).exclude(
        application_url=''
    ).order_by('desc_len')[:30]
    
    print(f"Found {len(jobs)} jobs to enrich")
    
    enriched = 0
    failed = 0
    skipped = 0
    
    for job in jobs:
        print(f"\n[{enriched+failed+skipped+1}/30] {job.title[:50]}...")
        print(f"  URL: {job.application_url[:80]}")
        
        # Skip PDF links
        if job.application_url.endswith('.pdf'):
            print("  Skipping PDF")
            skipped += 1
            continue
            
        # Skip internal probablygood links (they redirect)
        if 'jobs.probablygood.org/job-postings/' in job.application_url:
            print("  Skipping internal ProbablyGood link")
            skipped += 1
            continue
        
        # Fetch page content
        page_text = fetch_page_content(job.application_url)
        if page_text.startswith('Error'):
            print(f"  {page_text}")
            failed += 1
            continue
            
        if len(page_text) < 200:
            print(f"  Page too short ({len(page_text)} chars)")
            failed += 1
            continue
        
        # Extract with LLM
        data = extract_job_data(page_text, job.title)
        if not data:
            print("  LLM extraction failed")
            failed += 1
            continue
            
        # Update job
        updated_fields = []
        
        if data.get('description') and len(data['description']) > len(job.description or ''):
            job.description = data['description']
            updated_fields.append('description')
            
        if data.get('requirements') and not job.requirements:
            if isinstance(data['requirements'], list):
                job.requirements = '\n'.join(f"• {r}" for r in data['requirements'])
            else:
                job.requirements = data['requirements']
            updated_fields.append('requirements')
            
        if data.get('salary_min') and not job.salary_min:
            job.salary_min = data['salary_min']
            updated_fields.append('salary_min')
            
        if data.get('salary_max') and not job.salary_max:
            job.salary_max = data['salary_max']
            updated_fields.append('salary_max')
            
        if data.get('salary_currency') and not job.salary_currency:
            job.salary_currency = data['salary_currency']
            updated_fields.append('salary_currency')
            
        if data.get('location') and not job.location:
            job.location = data['location']
            updated_fields.append('location')
            
        if data.get('job_type') and not job.job_type:
            job.job_type = data['job_type']
            updated_fields.append('job_type')
            
        if data.get('experience_level') and not job.experience_level:
            job.experience_level = data['experience_level']
            updated_fields.append('experience_level')
        
        if updated_fields:
            job.save(update_fields=updated_fields)
            print(f"  ✓ Updated: {', '.join(updated_fields)}")
            enriched += 1
        else:
            print("  No new data extracted")
            failed += 1
            
        # Rate limit
        time.sleep(1)
    
    print(f"\n{'='*50}")
    print(f"RESULTS: Enriched {enriched}, Failed {failed}, Skipped {skipped}")
    print(f"{'='*50}")

if __name__ == '__main__':
    main()
