"""Create the table backing ``CACHES["default"]`` (DatabaseCache).

The alternative — running ``manage.py createcachetable`` by hand — would add a
step to deploy.sh and to every fresh developer database, and would be forgotten
exactly once. A migration rides along with the ``manage.py migrate`` that
deploy.sh already runs.

The table is created through the ``createcachetable`` management command rather
than a hand-written ``CREATE TABLE``, so the column types stay whatever Django
itself would build for this backend (SQLite in development, PostgreSQL in
production) instead of a second, drifting definition of the same table.
"""

from django.conf import settings
from django.core.management import call_command
from django.db import migrations

CACHE_TABLE_NAME = settings.CACHE_TABLE_NAME


def create_cache_table(apps, schema_editor):
    """Build the cache table, unless a previous run already did."""
    connection = schema_editor.connection
    if CACHE_TABLE_NAME in connection.introspection.table_names():
        return
    call_command(
        "createcachetable",
        CACHE_TABLE_NAME,
        database=connection.alias,
        verbosity=0,
    )


def drop_cache_table(apps, schema_editor):
    """Reverse: drop the cache table. Cached values are disposable by design."""
    connection = schema_editor.connection
    if CACHE_TABLE_NAME not in connection.introspection.table_names():
        return
    quoted = connection.ops.quote_name(CACHE_TABLE_NAME)
    schema_editor.execute(f"DROP TABLE {quoted}")


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
