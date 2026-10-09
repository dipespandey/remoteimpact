from django.contrib.postgres.indexes import GinIndex
from pgvector.django import HnswIndex


class PostgresOnlyGinIndex(GinIndex):
    """Do not emit PostgreSQL index SQL during SQLite table rebuilds."""

    def create_sql(self, model, schema_editor, using="", **kwargs):
        if schema_editor.connection.vendor != "postgresql":
            # SQLite's table-rebuild path executes deferred SQL verbatim. A
            # valid no-op keeps that path portable without creating an index.
            return "SELECT 1"
        return super().create_sql(model, schema_editor, using=using, **kwargs)

    def deconstruct(self):
        _, args, kwargs = super().deconstruct()
        return "django.contrib.postgres.indexes.GinIndex", args, kwargs


class PostgresOnlyHnswIndex(HnswIndex):
    """Do not emit pgvector index SQL during SQLite table rebuilds."""

    def create_sql(self, model, schema_editor, using="", **kwargs):
        if schema_editor.connection.vendor != "postgresql":
            return "SELECT 1"
        return super().create_sql(model, schema_editor, using=using, **kwargs)

    def deconstruct(self):
        _, args, kwargs = super().deconstruct()
        return "pgvector.django.indexes.HnswIndex", args, kwargs
