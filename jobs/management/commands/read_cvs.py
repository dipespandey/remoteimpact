"""Read CVs that were uploaded before CV reading existed (or whose file changed). Re-embeds each profile."""
from django.core.management.base import BaseCommand
from django.db.models import F

from jobs.models import SeekerProfile
from jobs.services import embedding_service
from jobs.services.cv_reader import read_profile_cv


class Command(BaseCommand):
    help = "Read unread or replaced seeker CVs into text and grounded facts."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        from jobs.signals import _seeker_embedding_hash

        embedding_service.load_for_batch()
        pending = (SeekerProfile.objects.exclude(resume="").exclude(resume=None)
                   .exclude(cv_source=F("resume"))[: options["limit"]])
        read = 0
        for profile in pending:
            if read_profile_cv(profile):
                read += 1
            updates = {"cv_source": profile.resume.name}
            if profile.wizard_completed:
                updates.update(embedding=embedding_service.embed_seeker(profile),
                               embedding_content_hash=_seeker_embedding_hash(profile))
            SeekerProfile.objects.filter(pk=profile.pk).update(**updates)
        self.stdout.write(f"Read {read} CV(s).")
