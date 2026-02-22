"""
RSS and XML feeds for job syndication.

Includes:
- Standard RSS feed
- Indeed XML feed format
- LinkedIn XML feed format
"""

from django.contrib.syndication.views import Feed
from django.utils.feedgenerator import Rss201rev2Feed, Atom1Feed
from django.urls import reverse
from django.conf import settings
from django.http import HttpResponse
from django.views import View
from django.utils import timezone
from xml.etree.ElementTree import Element, SubElement, tostring
from .models import Job, Category


class LatestJobsFeed(Feed):
    """RSS feed of latest jobs."""
    title = "Remote Impact Jobs"
    link = "/jobs/"
    description = "Latest remote jobs in climate, AI safety, global health & social impact"

    def items(self):
        return Job.objects.filter(is_active=True).order_by('-posted_at')[:50]

    def item_title(self, item):
        return f"{item.title} at {item.organization.name}"

    def item_description(self, item):
        return item.description[:500] if item.description else ""

    def item_link(self, item):
        return item.get_absolute_url()

    def item_pubdate(self, item):
        return item.posted_at


class CategoryJobsFeed(Feed):
    """RSS feed of jobs by category."""

    def get_object(self, request, slug):
        return Category.objects.get(slug=slug)

    def title(self, obj):
        return f"{obj.name} Jobs - Remote Impact"

    def link(self, obj):
        return reverse('jobs:category_landing', args=[obj.slug])

    def description(self, obj):
        return f"Latest remote {obj.name.lower()} jobs"

    def items(self, obj):
        return Job.objects.filter(
            is_active=True,
            category=obj
        ).order_by('-posted_at')[:50]

    def item_title(self, item):
        return f"{item.title} at {item.organization.name}"

    def item_description(self, item):
        return item.description[:500] if item.description else ""

    def item_link(self, item):
        return item.get_absolute_url()

    def item_pubdate(self, item):
        return item.posted_at


class IndeedXMLFeed(View):
    """
    Indeed XML feed format.
    
    Documentation: https://indeed.force.com/employerSupport1/s/article/XML-Feed-Specifications
    
    URL: /feed/indeed.xml
    """
    
    def get(self, request):
        jobs = Job.objects.filter(
            is_active=True,
        ).select_related('organization', 'category').order_by('-posted_at')[:500]
        
        site_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org')
        
        # Build XML
        root = Element('source')
        
        publisher = SubElement(root, 'publisher')
        publisher.text = 'Remote Impact'
        
        publisherurl = SubElement(root, 'publisherurl')
        publisherurl.text = site_url
        
        for job in jobs:
            job_el = SubElement(root, 'job')
            
            # Required fields
            title = SubElement(job_el, 'title')
            title.text = self._cdata(job.title)
            
            date = SubElement(job_el, 'date')
            date.text = job.posted_at.strftime('%Y-%m-%d')
            
            referencenumber = SubElement(job_el, 'referencenumber')
            referencenumber.text = str(job.id)
            
            url = SubElement(job_el, 'url')
            url.text = f"{site_url}{job.get_absolute_url()}"
            
            company = SubElement(job_el, 'company')
            company.text = self._cdata(job.organization.name)
            
            city = SubElement(job_el, 'city')
            city.text = 'Remote'
            
            state = SubElement(job_el, 'state')
            state.text = ''
            
            country = SubElement(job_el, 'country')
            country.text = 'REMOTE'
            
            description = SubElement(job_el, 'description')
            desc_text = job.description or ''
            if job.requirements:
                desc_text += f"\n\nRequirements:\n{job.requirements}"
            description.text = self._cdata(desc_text[:5000])
            
            # Optional fields
            if job.salary_min or job.salary_max:
                salary = SubElement(job_el, 'salary')
                if job.salary_min and job.salary_max:
                    salary.text = f"{job.salary_min}-{job.salary_max}"
                elif job.salary_min:
                    salary.text = str(job.salary_min)
                else:
                    salary.text = str(job.salary_max)
                    
            if job.category:
                category = SubElement(job_el, 'category')
                category.text = job.category.name
                
            jobtype = SubElement(job_el, 'jobtype')
            jobtype.text = job.get_job_type_display()
            
            remotetype = SubElement(job_el, 'remotetype')
            remotetype.text = 'Fully Remote'
            
            if job.expires_at:
                expirationdate = SubElement(job_el, 'expirationdate')
                expirationdate.text = job.expires_at.strftime('%Y-%m-%d')
        
        xml_content = tostring(root, encoding='unicode')
        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
        
        return HttpResponse(
            xml_declaration + xml_content,
            content_type='application/xml'
        )
    
    def _cdata(self, text):
        """Clean text for XML (CDATA would be better but keeping simple)."""
        if not text:
            return ''
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


class LinkedInXMLFeed(View):
    """
    LinkedIn Job Wrapping XML feed.
    
    URL: /feed/linkedin.xml
    """
    
    def get(self, request):
        jobs = Job.objects.filter(
            is_active=True,
        ).select_related('organization', 'category').order_by('-posted_at')[:500]
        
        site_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org')
        
        root = Element('jobs')
        
        for job in jobs:
            job_el = SubElement(root, 'job')
            
            company_name = SubElement(job_el, 'companyName')
            company_name.text = job.organization.name
            
            title = SubElement(job_el, 'title')
            title.text = job.title
            
            description = SubElement(job_el, 'description')
            description.text = job.description[:5000] if job.description else ''
            
            apply_url = SubElement(job_el, 'applyUrl')
            apply_url.text = f"{site_url}{job.get_absolute_url()}"
            
            location = SubElement(job_el, 'location')
            location.text = job.location or 'Remote'
            
            posted_at = SubElement(job_el, 'postedAt')
            posted_at.text = job.posted_at.isoformat()
            
            job_type = SubElement(job_el, 'jobType')
            job_type.text = job.get_job_type_display()
            
            if job.salary_min:
                salary_min = SubElement(job_el, 'salaryMin')
                salary_min.text = str(int(job.salary_min))
                
            if job.salary_max:
                salary_max = SubElement(job_el, 'salaryMax')
                salary_max.text = str(int(job.salary_max))
                
            salary_currency = SubElement(job_el, 'salaryCurrency')
            salary_currency.text = job.salary_currency or 'USD'
        
        xml_content = tostring(root, encoding='unicode')
        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
        
        return HttpResponse(
            xml_declaration + xml_content,
            content_type='application/xml'
        )
