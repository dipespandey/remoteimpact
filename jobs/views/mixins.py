from django.db.models.query import QuerySet

from jobs.models import Job


class FastJobPaginationMixin:
    """Sort IDs first, then hydrate the small page without vectors or search documents."""

    def paginate_queryset(self, queryset, page_size):
        if not isinstance(queryset, QuerySet):
            return super().paginate_queryset(queryset, page_size)
        paginator, page, ids, is_paginated = super().paginate_queryset(
            queryset.values_list("pk", flat=True), page_size)
        ids = list(ids)
        by_id = {job.pk: job for job in Job.objects.filter(pk__in=ids)
                 .select_related("organization", "category").defer("embedding", "search_vector")}
        page.object_list = [by_id[pk] for pk in ids if pk in by_id]
        return paginator, page, page.object_list, is_paginated
