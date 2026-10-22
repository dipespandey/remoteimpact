"""Approval-first native publishing. No AI requests, source fetching or outbound sending."""
import hashlib
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from blog.models import BlogPost
from .models import ArticleRevision


ALLOWED_TAGS = {"p", "h2", "h3", "h4", "ul", "ol", "li", "strong", "em", "a", "blockquote", "code", "pre", "br", "hr", "table", "thead", "tbody", "tr", "th", "td", "caption"}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "svg", "math", "template", "form", "input", "button", "textarea"}


def safe_url(value):
    if not isinstance(value, str) or not value or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if value.startswith("/") and not value.startswith("//"):
        return not parsed.scheme and not parsed.netloc
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def sanitize_html(html, *, sponsored=False):
    soup = BeautifulSoup(html, "html.parser")
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    for tag in list(soup.find_all(True)):
        if tag.name is None or tag.parent is None:  # Already decomposed with a parent.
            continue
        if tag.name in DROP_CONTENT:
            tag.decompose()
        elif tag.name not in ALLOWED_TAGS:
            tag.unwrap()
        else:
            href = tag.get("href") if tag.name == "a" else None
            tag.attrs = {}
            if href and safe_url(href):
                tag["href"] = href
                tag["rel"] = "sponsored nofollow noopener" if sponsored else "noopener"
    return str(soup)


def validate_destination(path):
    if not safe_url(path) or not path.startswith("/") or "?" in path or "#" in path:
        raise ValidationError("Use an existing internal destination path without query data.")
    from django.urls import Resolver404, resolve
    try:
        resolve(path)
    except Resolver404:
        raise ValidationError("The destination path is not routed by this application.") from None


def validate_evidence(evidence):
    if not isinstance(evidence, list) or not evidence:
        raise ValidationError("At least one dated source is required.")
    for row in evidence:
        if not isinstance(row, dict) or not safe_url(row.get("url")) or not row["url"].startswith("https://"):
            raise ValidationError("Each evidence row needs a safe HTTPS source URL.")
        if not isinstance(row.get("note"), str) or not row["note"].strip():
            raise ValidationError("Record what each source supports, not just a URL.")
        try:
            captured = parse_datetime(row.get("captured_at", ""))
        except (ValueError, TypeError):
            captured = None
        if not captured or timezone.is_naive(captured) or captured > timezone.now():
            raise ValidationError("Each source needs a valid past timezone-aware capture time.")


def revision_hash(revision):
    opportunity = revision.opportunity
    payload = {"post": str(revision.post_id), "slug": revision.post.slug, "title": revision.title,
               "excerpt": revision.excerpt, "body": revision.body, "sponsored": revision.sponsored,
               "opportunity": opportunity.pk, "evidence": opportunity.evidence,
               "coverage": opportunity.coverage_notes, "destination": opportunity.destination_path,
               "publish_at": revision.publish_at.isoformat() if revision.publish_at else None}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def require_editor(actor):
    if not actor or not actor.is_active or not actor.is_staff or not actor.has_perm("growth.change_articlerevision"):
        raise PermissionDenied


@transaction.atomic
def approve_revision(revision_id, *, actor):
    require_editor(actor)
    revision = ArticleRevision.objects.select_for_update().select_related("opportunity", "post").get(pk=revision_id)
    if revision.state not in (ArticleRevision.State.DRAFT, ArticleRevision.State.APPROVED):
        raise ValidationError("Only a current draft can be approved.")
    revision.full_clean()
    if revision.opportunity.decision != "accepted" or not revision.opportunity.review_note.strip():
        raise ValidationError("Accept the opportunity and record an evidence/overlap review before approval.")
    if not revision.publish_at or timezone.is_naive(revision.publish_at):
        raise ValidationError("Choose an explicit timezone-aware publication time.")
    revision.body = sanitize_html(revision.body, sponsored=revision.sponsored)
    revision.state = ArticleRevision.State.APPROVED
    revision.approved_by = actor
    revision.approved_at = timezone.now()
    revision.approval_hash = revision_hash(revision)
    revision.evidence_snapshot = {"sources": revision.opportunity.evidence,
                                  "coverage": revision.opportunity.coverage_notes,
                                  "destination": revision.opportunity.destination_path}
    revision.save()
    return revision


@transaction.atomic
def publish_revision(revision_id):
    revision = ArticleRevision.objects.select_for_update().select_related("opportunity", "post", "approved_by").get(pk=revision_id)
    # Lock the post too: two workers/revisions cannot race to overwrite its body.
    post = BlogPost.objects.select_for_update().get(pk=revision.post_id)
    if revision.state == ArticleRevision.State.PUBLISHED:
        return False
    if revision.state != ArticleRevision.State.APPROVED or not revision.publish_at or revision.publish_at > timezone.now():
        raise ValidationError("Only approved, due revisions can publish.")
    require_editor(revision.approved_by)
    revision.full_clean()
    if revision.opportunity.decision != "accepted":
        raise ValidationError("The opportunity is no longer accepted.")
    if revision.approval_hash != revision_hash(revision):
        raise ValidationError("Content, evidence, destination or schedule changed after approval; review again.")
    if ArticleRevision.objects.filter(post=post, published_at__gt=revision.approved_at).exists():
        raise ValidationError("A newer revision has published; review against the current article first.")
    # Fixed CTA destinations are application paths, not generated user preferences.
    from django.utils.html import format_html
    cta = format_html('<p><a href="{}">{}</a></p>', revision.opportunity.destination_path,
                      "Explore hiring options" if revision.opportunity.audience == "employer" else "Explore current opportunities")
    disclosure = "<p><strong>Sponsored employer content.</strong> Payment does not influence Scout matching.</p>" if revision.sponsored else ""
    post.title, post.excerpt = revision.title, revision.excerpt
    post.body = disclosure + sanitize_html(revision.body, sponsored=revision.sponsored) + str(cta)
    post.status = BlogPost.Status.PUBLISHED
    post.published_at = post.published_at or timezone.now()
    post.save()
    revision.state = ArticleRevision.State.PUBLISHED
    revision.published_at = timezone.now()
    revision.published_hash = revision_hash(revision)
    revision.save()
    return True
