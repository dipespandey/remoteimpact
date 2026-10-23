"""Redis URL helpers for keeping unrelated Django cache aliases isolated."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def redis_url_for_database(url, database):
    """Return a Redis URL using ``database`` while preserving auth and options.

    RedisCache.clear() issues FLUSHDB, so cache aliases sharing a Redis URL
    must not share its logical database. A ``db`` query option can override
    the URL path in redis-py, so remove it before setting the desired database.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in {'redis', 'rediss', 'unix'}:
        raise ValueError('REDIS_URL must use redis, rediss, or unix scheme')
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key.lower() != 'db']
    if parsed.scheme == 'unix':
        query.append(('db', str(int(database))))
        path = parsed.path
    else:
        path = f'/{int(database)}'
    return urlunsplit((parsed.scheme, parsed.netloc, path, urlencode(query), parsed.fragment))
