"""Who is calling the API, which tier they get, and how much they may call.

Keys come as `Authorization: Bearer ri_...`, `X-Api-Key: ri_...` or `?api_key=`. Without a key the caller
gets the anonymous tier (low limits, by IP), so AI assistants and scripts can try things without signing up.
"""
from dataclasses import dataclass
from datetime import timedelta
import hashlib

from django.db.models import F, Q
from django.db import connection
from django.utils import timezone

TIERS = {
    # The free tiers are for evaluating the data and small non-commercial projects; the catalogue itself
    # (thousands of roles, enriched fields, bulk paging) is the commercial product.
    # per-minute, per-day, max page size, how many pages per query, MCP results per call, enriched fields
    "anonymous": {"per_minute": 10, "per_day": 100, "page_max": 10, "max_pages": 3, "mcp_max": 5, "enriched": False},
    "free": {"per_minute": 30, "per_day": 500, "page_max": 20, "max_pages": 5, "mcp_max": 10, "enriched": False},
    "commercial": {"per_minute": 600, "per_day": 100000, "page_max": 500, "max_pages": 10000, "mcp_max": 25, "enriched": True},
}


@dataclass
class Caller:
    tier: str
    key: object = None  # ApiKey or None
    scope: str = ""     # rate-limit scope

    @property
    def limits(self):
        return TIERS[self.tier]


class RateLimited(Exception):
    def __init__(self, retry_after: int, caller=None):
        self.retry_after = retry_after
        self.caller = caller


class TierLimit(Exception):
    """The request is valid but beyond what this tier may fetch."""


def _raw_key(request) -> str:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.META.get("HTTP_X_API_KEY") or request.GET.get("api_key") or "").strip()


def _client_ip(request) -> str:
    from jobs.security import client_ip
    return client_ip(request)


def identify(request) -> Caller:
    """Resolve the caller; raises RateLimited when over their limits."""
    from jobs.models import ApiKey

    raw = _raw_key(request)
    key = None
    if raw.startswith("ri_"):
        key = ApiKey.objects.filter(key_hash=ApiKey.hash_key(raw), is_active=True).first()
    if key is not None:
        caller = Caller(tier=key.tier, key=key, scope=f"key:{key.pk}")
    else:
        caller = Caller(tier="anonymous", scope=f"ip:{_client_ip(request)}")
    _count(caller)
    return caller


def _bump(cache_key: str, timeout: int) -> int:
    from jobs.models import ApiRateBucket

    # FileBasedCache.incr is a read/write pair and races between Gunicorn workers.
    # Shared SQL counters work with both SQLite and PostgreSQL, independently of Redis.
    key = hashlib.sha256(cache_key.encode()).hexdigest()
    now = timezone.now()
    if connection.vendor in ("postgresql", "sqlite") and connection.features.can_return_columns_from_insert:
        # One atomic upsert returns this call's increment, avoiding a read/insert,
        # expiry update, increment and second read on every API request.
        table = connection.ops.quote_name(ApiRateBucket._meta.db_table)
        field = ApiRateBucket._meta.get_field("expires_at")
        expires = field.get_db_prep_value(now + timedelta(seconds=timeout), connection)
        current = field.get_db_prep_value(now, connection)
        with connection.cursor() as cursor:
            cursor.execute(f'''INSERT INTO {table} ("key", "count", "expires_at") VALUES (%s, 1, %s)
                ON CONFLICT ("key") DO UPDATE SET
                "count" = CASE WHEN {table}."expires_at" <= %s THEN 1 ELSE {table}."count" + 1 END,
                "expires_at" = CASE WHEN {table}."expires_at" <= %s THEN excluded."expires_at" ELSE {table}."expires_at" END
                RETURNING "count"''', [key, expires, current, current])
            return cursor.fetchone()[0]
    ApiRateBucket.objects.get_or_create(key=key, defaults={"expires_at": now + timedelta(seconds=timeout)})
    ApiRateBucket.objects.filter(key=key, expires_at__lte=now).update(count=0, expires_at=now + timedelta(seconds=timeout))
    ApiRateBucket.objects.filter(key=key).update(count=F("count") + 1)
    return ApiRateBucket.objects.values_list("count", flat=True).get(key=key)


def _count(caller: Caller):
    now = timezone.now()
    limits = caller.limits
    minute = _bump(f"api:m:{caller.scope}:{now.strftime('%Y%m%d%H%M')}", 120)
    if minute > limits["per_minute"]:
        raise RateLimited(60 - now.second, caller)
    if caller.key is not None:
        today = now.date()
        from jobs.models import ApiKey

        # Compare and update in SQL: concurrent callers must not reset one another's daily usage
        # or make admission decisions from a stale ApiKey instance.
        ApiKey.objects.filter(pk=caller.key.pk).filter(
            Q(requests_date__lt=today) | Q(requests_date__isnull=True)
        ).update(requests_today=0, requests_date=today)
        admitted = ApiKey.objects.filter(pk=caller.key.pk, requests_date=today,
                                        requests_today__lt=limits["per_day"]).update(
            requests_today=F("requests_today") + 1, last_used_at=now)
        used = 0 if admitted else limits["per_day"] + 1
    else:
        used = _bump(f"api:d:{caller.scope}:{now.strftime('%Y%m%d')}", 90000)
    if used > limits["per_day"]:
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        raise RateLimited(int((tomorrow - now).total_seconds()), caller)
