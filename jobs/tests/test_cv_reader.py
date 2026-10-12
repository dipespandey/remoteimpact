import io
import json

from django.test import SimpleTestCase

from jobs.services import cv_reader


def tiny_pdf(text):
    """A minimal one-page PDF with a line of text (no PDF library needed to build it)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref))
    out.seek(0)
    return out


class FakeClient:
    def __init__(self, reply):
        self.reply = reply

    def generate(self, *args, **kwargs):
        return json.dumps(self.reply)


CV = ("Amina Diallo\nProgram Manager, Save the Children, 2019-2024\nLed maternal health programs across three "
      "regions and managed a team of 12.\nSkills: monitoring and evaluation, grant writing, Kobo Toolbox\n"
      "MSc Public Health, LSHTM\nLanguages: French (native), English (fluent)\n") * 2


class CvReaderTests(SimpleTestCase):
    def test_reads_pdf_text(self):
        self.assertIn("Program Manager at Save the Children", cv_reader.extract_text(tiny_pdf("Program Manager at Save the Children"), "cv.pdf"))

    def test_reads_docx_text(self):
        import docx
        document, buffer = docx.Document(), io.BytesIO()
        document.add_paragraph("Grant writing and monitoring and evaluation")
        document.save(buffer)
        buffer.seek(0)
        self.assertIn("monitoring and evaluation", cv_reader.extract_text(buffer, "cv.docx"))

    def test_unsupported_or_broken_files_give_no_text(self):
        self.assertEqual(cv_reader.extract_text(io.BytesIO(b"not a cv"), "cv.doc"), "")
        self.assertEqual(cv_reader.extract_text(io.BytesIO(b"%PDF-broken"), "cv.pdf"), "")

    def test_facts_without_a_real_quote_are_dropped(self):
        facts = cv_reader.read_facts(CV, FakeClient({
            "roles": [{"title": "Program Manager", "organization": "Save the Children", "years": "2019-2024",
                       "quote": "Program Manager, Save the Children, 2019-2024"},
                      {"title": "CEO", "organization": "Invented Corp", "years": "2010", "quote": "CEO of Invented Corp"}],
            "skills": [{"name": "Grant writing", "quote": "grant writing"}, {"name": "Python", "quote": "Python expert"}],
            "education": [], "languages": [{"language": "French", "level": "native", "quote": "French (native)"}],
            "total_years": 5}))
        self.assertEqual([r["organization"] for r in facts["roles"]], ["Save the Children"])
        self.assertEqual([s["name"] for s in facts["skills"]], ["Grant writing"])
        self.assertEqual(facts["total_years"], 5)
        self.assertIn("Program Manager at Save the Children 2019-2024", cv_reader.facts_summary(facts))


import tempfile

from django.test import TestCase, override_settings


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="ri-test-media-"))
class CvUploadViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from jobs.models import SeekerProfile, UserProfile
        self.user = get_user_model().objects.create_user("ana", "ana@example.com", "pw")
        UserProfile.objects.create(user=self.user, account_type="seeker")
        self.profile = SeekerProfile.objects.create(user=self.user, headline="M&E officer")
        self.client.force_login(self.user)

    def test_upload_rejects_wrong_types_and_accepts_pdf(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.post("/account/cv/", {"cv": SimpleUploadedFile("cv.exe", b"MZ")})
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.resume)
        self.client.post("/account/cv/", {"cv": SimpleUploadedFile("cv.pdf", tiny_pdf("Monitoring and evaluation officer").read())})
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.resume.name.endswith(".pdf"))

    def test_remove_clears_everything_read(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.post("/account/cv/", {"cv": SimpleUploadedFile("cv.pdf", tiny_pdf("x").read())})
        type(self.profile).objects.filter(pk=self.profile.pk).update(resume_text="text", cv_facts={"roles": [1]})
        self.client.post("/account/cv/remove/")
        self.profile.refresh_from_db()
        self.assertEqual((bool(self.profile.resume), self.profile.resume_text, self.profile.cv_facts), (False, "", {}))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="ri-test-media-"))
class PrivateCvAccessTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.core.files.base import ContentFile
        from django.utils import timezone
        from jobs.models import Application, Category, Job, Organization
        User = get_user_model()
        self.poster, self.colleague, self.applicant, self.stranger = (
            User.objects.create_user(n, f"{n}@x.example", "pw") for n in ("poster", "colleague", "applicant", "stranger"))
        org = Organization.objects.create(name="Org", slug="org")
        org.members.add(self.poster, self.colleague)
        job = Job.objects.create(title="Lead", slug="lead", organization=org, category=Category.objects.create(name="C", slug="c"),
                                 description="d", location="Remote", job_type="full-time", source=Job.Source.MANUAL,
                                 is_paid=True, is_active=True, posted_at=timezone.now(), poster=self.poster)
        self.app = Application.objects.create(job=job, applicant=self.applicant, full_name="Ana Diallo")
        self.app.resume.save("cv.pdf", ContentFile(tiny_pdf("Ana CV").read()))

    def status_for(self, user):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return self.client.get(f"/applications/{self.app.pk}/cv/").status_code

    def test_only_the_hiring_team_and_applicant_can_open_it(self):
        self.assertEqual(self.status_for(self.poster), 200)
        self.assertEqual(self.status_for(self.colleague), 200)
        self.assertEqual(self.status_for(self.applicant), 200)
        self.assertEqual(self.status_for(self.stranger), 404)
        self.assertEqual(self.status_for(None), 302)

    def test_file_is_not_cached_or_indexed(self):
        self.client.force_login(self.poster)
        response = self.client.get(f"/applications/{self.app.pk}/cv/")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn("noindex", response["X-Robots-Tag"])
