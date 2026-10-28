"""Keep editorial category corrections tied to the source evidence reviewed."""
import hashlib
import json


def category_review_input_hash(payload):
    fields = ('source', 'external_id', 'title', 'organization_name',
              'application_url', 'description', 'requirements')
    values = {key: str(payload.get(key) or '') for key in fields}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def retained_category_review(job, payload):
    review = (job.raw_data or {}).get('category_source_review')
    if not isinstance(review, dict) or review.get('version') != 1 or review.get('needs_review'):
        return None
    if not job.category_id or review.get('category_slug') != job.category.slug:
        return None
    if review.get('source_input_hash') != category_review_input_hash(payload):
        return None
    return review
