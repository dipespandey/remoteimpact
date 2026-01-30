import io
import textwrap

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View
from PIL import Image, ImageDraw, ImageFont

from jobs.models import Job


def _load_font(size):
    """Try to load a bold font, fall back to default."""
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _load_font_regular(size):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


class JobOGImageView(View):
    """Generate a dynamic Open Graph image for a job posting."""

    WIDTH = 1200
    HEIGHT = 630
    GREEN = (22, 128, 60)        # #16803c
    DARK_BG = (15, 23, 42)      # Dark slate
    WHITE = (255, 255, 255)
    LIGHT_GRAY = (148, 163, 184)
    ACCENT = (34, 197, 94)       # Lighter green accent

    def get(self, request, slug):
        job = get_object_or_404(Job, slug=slug)

        img = Image.new("RGB", (self.WIDTH, self.HEIGHT), self.DARK_BG)
        draw = ImageDraw.Draw(img)

        # -- Green header bar --
        draw.rectangle([0, 0, self.WIDTH, 160], fill=self.GREEN)

        # -- Branding in header --
        font_brand = _load_font(36)
        draw.text((60, 55), "Remote Impact Jobs", fill=self.WHITE, font=font_brand)

        # -- Accent line under header --
        draw.rectangle([0, 160, self.WIDTH, 166], fill=self.ACCENT)

        # -- Job title (wrapped) --
        font_title = _load_font(44)
        title_lines = textwrap.wrap(job.title, width=38)[:3]
        y = 200
        for line in title_lines:
            draw.text((60, y), line, fill=self.WHITE, font=font_title)
            y += 56

        # -- Organization name --
        font_org = _load_font_regular(30)
        org_text = job.organization.name if job.organization else ""
        draw.text((60, y + 16), org_text, fill=self.ACCENT, font=font_org)

        # -- Location / Remote info --
        font_info = _load_font_regular(26)
        location = job.location or "Remote"
        job_type = job.get_job_type_display() if job.job_type else ""
        info_text = f"{location}  \u2022  {job_type}" if job_type else location
        draw.text((60, y + 62), info_text, fill=self.LIGHT_GRAY, font=font_info)

        # -- Bottom bar --
        draw.rectangle([0, self.HEIGHT - 60, self.WIDTH, self.HEIGHT], fill=self.GREEN)
        font_footer = _load_font_regular(22)
        draw.text(
            (60, self.HEIGHT - 45),
            "remoteimpact.org",
            fill=self.WHITE,
            font=font_footer,
        )

        # -- Encode --
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)

        response = HttpResponse(buf.getvalue(), content_type="image/png")
        response["Cache-Control"] = "public, max-age=86400, s-maxage=604800"
        return response
