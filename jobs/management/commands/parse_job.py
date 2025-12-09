import json
from django.core.management.base import BaseCommand, CommandError
from jobs.models import Job
from jobs.ai import JobParser


class Command(BaseCommand):
    help = "Parses job description using AI to extract structured data"

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=int, help="ID of the job to parse")
        parser.add_argument(
            "--save", action="store_true", help="Save the parsed data to the job record"
        )

    def handle(self, *args, **options):
        job_id = options["job_id"]
        save = options["save"]

        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            raise CommandError(f"Job with ID {job_id} does not exist")

        self.stdout.write(f'Parsing job "{job.title}" (ID: {job.id})...')

        # Combine relevant fields for context if the original description was dumped into one field
        # Often scrapers dump everything into 'description' or 'raw_data'
        # For the user's specific case, it seems the messy text might be in 'description'
        text_to_parse = job.description

        # If raw_data has content, maybe include that too?
        # But let's assume the user is fixing a job where the scraper put everything in description.

        parser = JobParser()
        data = parser.parse(text_to_parse)

        if not data:
            self.stdout.write(
                self.style.ERROR("Failed to parse job data or empty response from AI.")
            )
            return

        self.stdout.write(self.style.SUCCESS("Successfully parsed job data:"))
        self.stdout.write(json.dumps(data, indent=2))

        if save:
            self.stdout.write("Saving changes to database...")

            # Map extracted fields to Job model fields
            if data.get("title"):
                job.title = data["title"]
            if data.get("organization_description") and job.organization:
                job.organization.description = data["organization_description"]
                job.organization.save()

            # Core fields
            job.description = data.get("description", "")
            job.requirements = data.get("requirements", "")
            job.location = data.get("location", job.location)

            # Salary
            if data.get("salary_min"):
                job.salary_min = data["salary_min"]
            if data.get("salary_max"):
                job.salary_max = data["salary_max"]
            if data.get("salary_currency"):
                job.salary_currency = data["salary_currency"]

            # Application
            if data.get("application_url"):
                job.application_url = data["application_url"]
            if data.get("application_email"):
                job.application_email = data["application_email"]

            # New structured fields
            if data.get("impact"):
                job.impact = data["impact"]
            if data.get("benefits"):
                job.benefits = data["benefits"]
            if data.get("how_to_apply_text"):
                job.how_to_apply_text = data["how_to_apply_text"]
            if data.get("company_description"):
                job.company_description = data["company_description"]

            job.save()

            self.stdout.write(self.style.SUCCESS(f"Job {job.id} updated successfully."))
        else:
            self.stdout.write(
                self.style.WARNING("\nDry run completed. Use --save to apply changes.")
            )
