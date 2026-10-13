"""Release tests also close connections owned by asgiref's ORM worker thread."""
import asyncio

from asgiref.sync import sync_to_async
from django.db import connections
from django.test.runner import DiscoverRunner


class ReleaseTestRunner(DiscoverRunner):
    def teardown_databases(self, old_config, **kwargs):
        async def close_worker_connections():
            await sync_to_async(connections.close_all, thread_sensitive=True)()

        asyncio.run(close_worker_connections())
        return super().teardown_databases(old_config, **kwargs)
