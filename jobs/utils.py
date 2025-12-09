from __future__ import annotations

import uuid
from typing import Type

from django.db import models
from django.utils.text import slugify


def unique_slug(model: Type[models.Model], value: str, slug_field: str = 'slug') -> str:
    """
    Generate a slug for ``value`` that is unique for ``model``.

    This is intentionally simple but ensures that importers can create
    deterministic slugs without relying on admin prepopulation hooks.
    """
    base_slug = slugify(value) or str(uuid.uuid4())
    slug = base_slug
    counter = 1

    lookup = {slug_field: slug}
    while model.objects.filter(**lookup).exists():
        slug = f'{base_slug}-{counter}'
        counter += 1
        lookup = {slug_field: slug}
    return slug
