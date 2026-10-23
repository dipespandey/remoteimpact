"""Release tests close asynchronous workers and isolated test database sessions."""
import asyncio

from asgiref.sync import sync_to_async
from django.db import connections
from django.test.runner import DiscoverRunner


class ReleaseTestRunner(DiscoverRunner):
    def teardown_databases(self, old_config, **kwargs):
        async def close_worker_connections():
            await sync_to_async(connections.close_all, thread_sensitive=True)()

        asyncio.run(close_worker_connections())
        if not self.keepdb:
            for connection, original_name, destroy in old_config:
                # Browser/async worker threads can retain an idle connection after
                # the suite. Restrict cleanup to Django's isolated test database.
                test_name = connection.settings_dict['NAME']
                if (destroy and connection.vendor == 'postgresql'
                        and test_name != original_name
                        and str(test_name).startswith('test_')):
                    connection.close()
                    with connection._nodb_cursor() as cursor:
                        cursor.execute(
                            'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                            'WHERE datname = %s AND pid <> pg_backend_pid()',
                            [test_name],
                        )
        return super().teardown_databases(old_config, **kwargs)
