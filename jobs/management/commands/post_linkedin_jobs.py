"""
Post a daily LinkedIn roundup of the latest complete Remote Impact jobs.

Required environment variables for live posting:
    LINKEDIN_ACCESS_TOKEN
    LINKEDIN_AUTHOR_URN

LINKEDIN_AUTHOR_URN should look like one of:
    urn:li:organization:123456
    urn:li:person:abcdef

Usage:
    python manage.py post_linkedin_jobs --dry-run
    python manage.py post_linkedin_jobs
"""

import io
import os
import textwrap
from decimal import Decimal

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.db.models.functions import Length
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from jobs.models import Job


class Command(BaseCommand):
    help = "Post the 10 latest complete Remote Impact jobs to LinkedIn."

    WIDTH = 1200
    HEIGHT = 627
    BG = (15, 23, 42)
    PANEL = (24, 35, 58)
    GREEN = (138, 212, 37)
    WHITE = (255, 255, 255)
    MUTED = (203, 213, 225)

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print copy and write image without posting.")
        parser.add_argument("--count", type=int, default=10, help="Number of jobs to include.")
        parser.add_argument(
            "--min-description-length",
            type=int,
            default=300,
            help="Minimum description length required for a job to be eligible.",
        )
        parser.add_argument(
            "--output",
            default="/tmp/remoteimpact-linkedin-jobs.png",
            help="Where to write the generated image.",
        )

    def handle(self, *args, **options):
        count = options["count"]
        jobs = list(self.get_complete_jobs(count, options["min_description_length"]))

        if len(jobs) < count:
            raise CommandError(f"Only found {len(jobs)} complete jobs; need {count}. Not posting.")

        copy = self.build_post_copy(jobs)
        image_bytes = self.build_image(jobs)

        with open(options["output"], "wb") as f:
            f.write(image_bytes)

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS(f"Dry run image written to {options['output']}"))
            self.stdout.write("")
            self.stdout.write(copy)
            return

        token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
        author_urn = os.environ.get("LINKEDIN_AUTHOR_URN")
        if not token or not author_urn:
            raise CommandError(
                "Missing LINKEDIN_ACCESS_TOKEN or LINKEDIN_AUTHOR_URN. "
                "Run with --dry-run or add LinkedIn credentials."
            )

        asset_urn = self.upload_image(token, author_urn, image_bytes)
        post_id = self.create_linkedin_post(token, author_urn, copy, asset_urn)
        self.stdout.write(self.style.SUCCESS(f"Posted LinkedIn roundup: {post_id}"))

    def get_complete_jobs(self, count, min_description_length):
        now = timezone.now()
        return (
            Job.objects.filter(is_active=True)
            .annotate(description_length=Length("description"))
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=now))
            .exclude(title="")
            .exclude(organization__name="")
            .exclude(description="")
            .exclude(requirements="")
            .exclude(location="")
            .filter(description__isnull=False, requirements__isnull=False)
            .filter(description_length__gte=min_description_length)
            .filter(Q(application_url__gt="") | Q(application_email__gt="") | Q(source=Job.Source.MANUAL))
            .select_related("organization", "category")
            .order_by("-posted_at")[:count]
        )

    def build_post_copy(self, jobs):
        site_url = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
        lines = [
            "10 fresh remote impact roles worth a look today.",
            "",
            "Curated from Remote Impact for people who want their work to matter across climate, global health, AI safety, policy, nonprofit operations, and more.",
            "",
        ]

        for index, job in enumerate(jobs, 1):
            category = f" | {job.category.name}" if job.category else ""
            salary = self.salary_label(job)
            salary = f" | {salary}" if salary else ""
            lines.append(f"{index}. {job.title} at {job.organization.name}{category}{salary}")

        lines.extend(
            [
                "",
                f"Browse and apply: {site_url}/jobs/",
                "",
                "#RemoteJobs #ImpactJobs #SocialImpact #Hiring #RemoteWork",
            ]
        )
        return "\n".join(lines)

    def build_image(self, jobs):
        image = Image.new("RGB", (self.WIDTH, self.HEIGHT), self.BG)
        draw = ImageDraw.Draw(image)

        font_brand = self.font(34, bold=True)
        font_title = self.font(54, bold=True)
        font_sub = self.font(26)
        font_job = self.font(25, bold=True)
        font_meta = self.font(20)
        font_footer = self.font(20)

        draw.rectangle([0, 0, self.WIDTH, 120], fill=(22, 128, 60))
        draw.text((58, 42), "Remote Impact", fill=self.WHITE, font=font_brand)
        draw.rounded_rectangle([900, 36, 1138, 82], radius=23, fill=self.GREEN)
        draw.text((933, 48), "Latest remote jobs", fill=(15, 23, 42), font=font_footer)

        draw.text((58, 155), "10 remote impact jobs", fill=self.WHITE, font=font_title)
        draw.text((60, 222), timezone.localdate().strftime("%B %-d, %Y"), fill=self.MUTED, font=font_sub)

        y = 282
        for index, job in enumerate(jobs[:10], 1):
            row_y = y + (index - 1) * 30
            title = self.truncate(f"{job.title} at {job.organization.name}", 74)
            draw.text((62, row_y), f"{index}.", fill=self.GREEN, font=font_job)
            draw.text((104, row_y), title, fill=self.WHITE, font=font_job)

        draw.rounded_rectangle([58, 575, 1142, 610], radius=17, fill=self.PANEL)
        draw.text((84, 582), "Browse all roles at remoteimpact.org/jobs", fill=self.MUTED, font=font_footer)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def upload_image(self, token, author_urn, image_bytes):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }
        register_payload = {
            "registerUploadRequest": {
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "owner": author_urn,
                "serviceRelationships": [
                    {
                        "relationshipType": "OWNER",
                        "identifier": "urn:li:userGeneratedContent",
                    }
                ],
            }
        }
        response = requests.post(
            "https://api.linkedin.com/v2/assets?action=registerUpload",
            headers=headers,
            json=register_payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()["value"]
        upload_url = data["uploadMechanism"]["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]["uploadUrl"]
        asset_urn = data["asset"]

        upload_response = requests.put(
            upload_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "image/png"},
            data=image_bytes,
            timeout=60,
        )
        upload_response.raise_for_status()
        return asset_urn

    def create_linkedin_post(self, token, author_urn, copy, asset_urn):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": copy},
                    "shareMediaCategory": "IMAGE",
                    "media": [
                        {
                            "status": "READY",
                            "description": {"text": "Latest remote impact jobs from Remote Impact."},
                            "media": asset_urn,
                            "title": {"text": "10 latest remote impact jobs"},
                        }
                    ],
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        response = requests.post(
            "https://api.linkedin.com/v2/ugcPosts",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.headers.get("x-restli-id", "created")

    def salary_label(self, job):
        if not job.salary_min and not job.salary_max:
            return ""

        symbol = {"USD": "$", "EUR": "EUR ", "GBP": "GBP "}.get(job.salary_currency, f"{job.salary_currency} ")
        if job.salary_min and job.salary_max:
            return f"{symbol}{self.compact_money(job.salary_min)}-{self.compact_money(job.salary_max)}"
        if job.salary_min:
            return f"{symbol}{self.compact_money(job.salary_min)}+"
        return f"up to {symbol}{self.compact_money(job.salary_max)}"

    def compact_money(self, value):
        value = int(Decimal(value))
        if value >= 1000:
            return f"{round(value / 1000)}k"
        return str(value)

    def truncate(self, text, limit):
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def font(self, size, bold=False):
        names = (
            "DejaVuSans-Bold.ttf",
            "LiberationSans-Bold.ttf",
        ) if bold else (
            "DejaVuSans.ttf",
            "LiberationSans-Regular.ttf",
        )
        for name in names:
            for base in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation"):
                path = os.path.join(base, name)
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
        return ImageFont.load_default()
