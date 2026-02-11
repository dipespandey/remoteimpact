"""
Management command to re-crawl specific jobs from their source.
"""
from django.core.management.base import BaseCommand
from jobs.models import Job
from jobs.services.crawlers import crawl_greenhouse_job, crawl_lever_job, crawl_ashby_job


class Command(BaseCommand):
    help = "Re-crawl a specific job from its source"

    def add_arguments(self, parser):
        parser.add_argument("job_ids", nargs="+", type=int, help="Job IDs to re-crawl")

    def handle(self, *args, **options):
        job_ids = options["job_ids"]
        
        crawlers = {
            "greenhouse": crawl_greenhouse_job,
            "lever": crawl_lever_job,
            "ashby": crawl_ashby_job,
        }

        for job_id in job_ids:
            try:
                job = Job.objects.get(id=job_id)
                self.stdout.write(f"\nCrawling job {job_id}: {job.title}")
                self.stdout.write(f"  Source: {job.source}")
                self.stdout.write(f"  URL: {job.application_url}")
                self.stdout.write(f"  Current desc length: {len(job.description or '')}")

                crawler = crawlers.get(job.source)
                if not crawler:
                    self.stdout.write(self.style.WARNING(f"  No crawler for source: {job.source}"))
                    continue

                # Mark for crawling
                job.raw_data = job.raw_data or {}
                job.raw_data["needs_crawling"] = True
                job.save()

                # Crawl
                result = crawler(job)
                if result:
                    result.save()
                    self.stdout.write(self.style.SUCCESS(f"  ✓ Updated! New desc length: {len(result.description or '')}"))
                else:
                    self.stdout.write(self.style.ERROR(f"  ✗ Crawl returned None"))

            except Job.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Job {job_id} not found"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error crawling job {job_id}: {e}"))
