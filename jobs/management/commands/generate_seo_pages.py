"""
Programmatic SEO Page Generator

Analyzes job data and generates high-value SEO landing pages:
1. Role-based: /remote-{role}-jobs/ (e.g., /remote-software-engineer-jobs/)
2. Skill-based: /{skill}-jobs/ (e.g., /python-climate-jobs/)
3. Salary-based: /high-paying-{domain}-jobs/ (e.g., /high-paying-climate-jobs/)

Usage:
    python manage.py generate_seo_pages --analyze    # Show opportunities
    python manage.py generate_seo_pages --generate   # Create pages
"""

from collections import Counter
from django.core.management.base import BaseCommand
from django.db.models import Count, Avg, Q
from django.utils.text import slugify
from jobs.models import Job, Category, Organization


# Common role patterns to extract
ROLE_PATTERNS = [
    'software engineer', 'data analyst', 'data scientist', 'product manager',
    'program manager', 'project manager', 'policy analyst', 'policy director',
    'communications manager', 'marketing manager', 'operations manager',
    'research analyst', 'research scientist', 'ux designer', 'ui designer',
    'frontend developer', 'backend developer', 'full stack developer',
    'devops engineer', 'machine learning engineer', 'ai engineer',
    'content writer', 'copywriter', 'grant writer', 'technical writer',
    'business development', 'account manager', 'customer success',
    'hr manager', 'recruiter', 'people operations',
    'finance manager', 'accountant', 'financial analyst',
    'executive director', 'ceo', 'cto', 'coo', 'cfo',
    'intern', 'fellow', 'associate', 'coordinator', 'specialist',
]


class Command(BaseCommand):
    help = 'Generate programmatic SEO landing pages'

    def add_arguments(self, parser):
        parser.add_argument(
            '--analyze',
            action='store_true',
            help='Analyze job data and show SEO opportunities',
        )
        parser.add_argument(
            '--generate',
            action='store_true', 
            help='Generate SEO page configurations',
        )
        parser.add_argument(
            '--min-jobs',
            type=int,
            default=10,
            help='Minimum jobs required to create a page (default: 10)',
        )

    def handle(self, *args, **options):
        if options['analyze']:
            self.analyze_opportunities(options['min_jobs'])
        elif options['generate']:
            self.generate_pages(options['min_jobs'])
        else:
            self.stdout.write('Use --analyze or --generate')

    def analyze_opportunities(self, min_jobs):
        """Analyze job data to find SEO page opportunities."""
        self.stdout.write(self.style.SUCCESS('\n📊 SEO Opportunity Analysis\n'))
        
        jobs = Job.objects.filter(is_active=True)
        total = jobs.count()
        self.stdout.write(f'Total active jobs: {total}\n')

        # 1. Role-based opportunities
        self.stdout.write(self.style.SUCCESS('─' * 50))
        self.stdout.write(self.style.SUCCESS('🎯 ROLE-BASED PAGES'))
        self.stdout.write(self.style.SUCCESS('─' * 50))
        
        role_counts = Counter()
        for job in jobs.values_list('title', flat=True):
            title_lower = job.lower()
            for role in ROLE_PATTERNS:
                if role in title_lower:
                    role_counts[role] += 1
                    break

        for role, count in role_counts.most_common(30):
            if count >= min_jobs:
                slug = f"remote-{slugify(role)}-jobs"
                self.stdout.write(f'  /{slug}/ → {count} jobs')

        # 2. Skill-based opportunities
        self.stdout.write(self.style.SUCCESS('\n' + '─' * 50))
        self.stdout.write(self.style.SUCCESS('🛠️ SKILL-BASED PAGES'))
        self.stdout.write(self.style.SUCCESS('─' * 50))

        skill_counts = Counter()
        for job in jobs.values_list('skills_list', flat=True):
            if job:
                for skill in job:
                    skill_counts[skill.lower()] += 1

        for skill, count in skill_counts.most_common(30):
            if count >= min_jobs:
                slug = f"{slugify(skill)}-remote-jobs"
                self.stdout.write(f'  /{slug}/ → {count} jobs')

        # 3. Category + Salary pages
        self.stdout.write(self.style.SUCCESS('\n' + '─' * 50))
        self.stdout.write(self.style.SUCCESS('💰 HIGH-PAYING PAGES'))
        self.stdout.write(self.style.SUCCESS('─' * 50))

        for cat in Category.objects.all():
            high_paying = jobs.filter(
                category=cat,
                salary_min__gte=100000
            ).count()
            if high_paying >= 5:
                slug = f"high-paying-{cat.slug}-jobs"
                self.stdout.write(f'  /{slug}/ → {high_paying} jobs ($100k+)')

        # 4. Company careers pages
        self.stdout.write(self.style.SUCCESS('\n' + '─' * 50))
        self.stdout.write(self.style.SUCCESS('🏢 COMPANY CAREERS PAGES'))
        self.stdout.write(self.style.SUCCESS('─' * 50))

        top_orgs = Organization.objects.annotate(
            job_count=Count('jobs', filter=Q(jobs__is_active=True))
        ).filter(job_count__gte=3).order_by('-job_count')[:30]

        for org in top_orgs:
            slug = f"{org.slug}-careers"
            self.stdout.write(f'  /{slug}/ → {org.job_count} jobs')

        # 5. Location-based pages
        self.stdout.write(self.style.SUCCESS('\n' + '─' * 50))
        self.stdout.write(self.style.SUCCESS('🌍 LOCATION-BASED PAGES'))
        self.stdout.write(self.style.SUCCESS('─' * 50))

        location_patterns = {
            'remote-jobs-usa': ['united states', 'usa', 'us-based', 'us only'],
            'remote-jobs-europe': ['europe', 'eu', 'european', 'emea'],
            'remote-jobs-uk': ['united kingdom', 'uk', 'britain', 'england'],
            'remote-jobs-anywhere': ['worldwide', 'anywhere', 'global'],
            'remote-jobs-latam': ['latin america', 'latam', 'south america'],
            'remote-jobs-asia': ['asia', 'apac', 'asian'],
        }

        for slug, patterns in location_patterns.items():
            count = 0
            for job in jobs.values_list('location', flat=True):
                if job:
                    loc_lower = job.lower()
                    if any(p in loc_lower for p in patterns):
                        count += 1
            if count >= min_jobs:
                self.stdout.write(f'  /{slug}/ → {count} jobs')

        self.stdout.write(self.style.SUCCESS('\n✅ Analysis complete\n'))

    def generate_pages(self, min_jobs):
        """Generate SEO page data (outputs JSON config)."""
        import json
        
        jobs = Job.objects.filter(is_active=True)
        pages = []

        # Generate role pages
        role_counts = Counter()
        for job in jobs.values_list('title', flat=True):
            title_lower = job.lower()
            for role in ROLE_PATTERNS:
                if role in title_lower:
                    role_counts[role] += 1
                    break

        for role, count in role_counts.most_common():
            if count >= min_jobs:
                pages.append({
                    'type': 'role',
                    'slug': f"remote-{slugify(role)}-jobs",
                    'title': f"Remote {role.title()} Jobs",
                    'h1': f"Remote {role.title()} Jobs",
                    'meta_desc': f"Find {count}+ remote {role} jobs at impact organizations. Work from anywhere in climate, AI safety, global health & more.",
                    'query_pattern': role,
                    'job_count': count,
                })

        # Generate skill pages
        skill_counts = Counter()
        for job in jobs.values_list('skills_list', flat=True):
            if job:
                for skill in job:
                    skill_counts[skill.lower()] += 1

        for skill, count in skill_counts.most_common():
            if count >= min_jobs and len(skill) > 2:
                pages.append({
                    'type': 'skill',
                    'slug': f"{slugify(skill)}-remote-jobs",
                    'title': f"{skill.title()} Remote Jobs",
                    'h1': f"Remote Jobs Requiring {skill.title()}",
                    'meta_desc': f"Browse {count}+ remote jobs requiring {skill} skills. Find impact roles in climate, AI safety, global health & social good.",
                    'skill': skill,
                    'job_count': count,
                })

        self.stdout.write(json.dumps(pages, indent=2))
        self.stdout.write(self.style.SUCCESS(f'\n✅ Generated {len(pages)} page configs'))
