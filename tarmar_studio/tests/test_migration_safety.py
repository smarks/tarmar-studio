"""Every new migration must leave the previous release working (issue #575).

A deploy runs ``migrate`` on the shared database while the old colour is still
serving, and ``deploy.sh rollback`` points nginx back at old code running
against the new schema. So a migration may only *expand* the schema in a way
old code tolerates. This guard reads every migration added after the baseline
in ``tarmar_studio/migration_baseline.json`` and fails on:

- ``AddField`` of a non-null column without ``db_default``: Django fills
  existing rows and then drops the default, so old code's inserts are refused.
- ``RemoveField``, ``RenameField``, ``AlterModelTable``: old code still selects
  the old column or table.
- ``RunPython`` without ``reverse_code``: the migration cannot be unapplied.

A deliberate contract step (dropping a column one release after code stopped
reading it) goes in the file's ``allowed`` map, keyed ``app.migration``, with
the reason as the value.
"""

import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
from django.db import migrations, models
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations.base import Operation

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_FILE = REPO_ROOT / "tarmar_studio" / "migration_baseline.json"

UNSAFE_OPERATIONS = (
    migrations.RemoveField,
    migrations.RenameField,
    migrations.AlterModelTable,
)

Node = tuple[str, str]


def flatten(operations: Iterable[Operation]) -> Iterator[Operation]:
    """Operations as the database sees them, unwrapping SeparateDatabaseAndState."""
    for operation in operations:
        if isinstance(operation, migrations.SeparateDatabaseAndState):
            yield from flatten(operation.database_operations)
        else:
            yield operation


def created_models(migration: migrations.Migration) -> set[str]:
    """Lower-case names of the models a migration creates."""
    return {
        operation.name_lower
        for operation in flatten(migration.operations)
        if isinstance(operation, migrations.CreateModel)
    }


def problems(
    migration: migrations.Migration, new_tables: set[tuple[str, str]]
) -> list[str]:
    """Why ``migration`` would break the previous release, if it would.

    ``new_tables`` holds (app label, model name) for models created by new
    migrations: no released code reads them, so adding a column to one is safe.
    """
    found = []
    for operation in flatten(migration.operations):
        label = type(operation).__name__
        if isinstance(operation, migrations.AddField):
            field = operation.field
            if (migration.app_label, operation.model_name_lower) in new_tables:
                continue
            if field.many_to_many or field.null:
                continue
            if field.db_default is not models.NOT_PROVIDED:
                continue
            found.append(
                f"{label} {operation.model_name}.{operation.name} is NOT NULL "
                "with no db_default; use null=True or db_default="
            )
        elif isinstance(operation, UNSAFE_OPERATIONS):
            found.append(
                f"{label} {operation.describe()}: ship it one release after "
                "code stops reading the old name"
            )
        elif isinstance(operation, migrations.RunPython):
            if operation.reverse_code is None:
                found.append(
                    f"{label} has no reverse_code; pass one, or "
                    "migrations.RunPython.noop"
                )
    return found


def load_baseline() -> dict:
    return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))


def local_nodes(loader: MigrationLoader) -> set[Node]:
    """Migrations that live in this repository rather than in site-packages."""
    nodes = set()
    for node, migration in loader.disk_migrations.items():
        module_file = Path(str(sys.modules[type(migration).__module__].__file__))
        if REPO_ROOT in module_file.resolve().parents:
            nodes.add(node)
    return nodes


def new_migrations(loader: MigrationLoader, baseline: dict[str, str]) -> set[Node]:
    known: set[Node] = set()
    for app_label, name in baseline.items():
        known.update(loader.graph.forwards_plan((app_label, name)))
    return local_nodes(loader) - known


@pytest.fixture(scope="module")
def loader() -> MigrationLoader:
    return MigrationLoader(None, ignore_no_migrations=True)


def test_new_migrations_keep_the_previous_release_working(loader) -> None:
    config = load_baseline()
    allowed = config["allowed"]
    new_nodes = new_migrations(loader, config["baseline"])
    new_tables = {
        (node[0], model)
        for node in new_nodes
        for model in created_models(loader.graph.nodes[node])
    }
    failures = []
    for node in sorted(new_nodes):
        key = f"{node[0]}.{node[1]}"
        if key in allowed:
            continue
        for problem in problems(loader.graph.nodes[node], new_tables):
            failures.append(f"{key}: {problem}")
    assert not failures, (
        "These migrations would break the colour still serving during a deploy, "
        "or a rollback (issue #575). Make them expand-only, or list them in "
        f"{BASELINE_FILE.name} 'allowed' with the reason:\n" + "\n".join(failures)
    )


def test_baseline_names_real_migrations(loader) -> None:
    for app_label, name in load_baseline()["baseline"].items():
        assert (app_label, name) in loader.disk_migrations, (app_label, name)


def test_every_local_app_with_migrations_has_a_baseline(loader) -> None:
    baseline = load_baseline()["baseline"]
    apps_on_disk = {app_label for app_label, _ in local_nodes(loader)}
    # An app created after the baseline is checked from its first migration,
    # so only apps that existed then need an entry; none may be forgotten.
    assert set(baseline) <= apps_on_disk


def test_allowed_entries_are_real_new_migrations_with_reasons(loader) -> None:
    config = load_baseline()
    new_nodes = new_migrations(loader, config["baseline"])
    for key, reason in config["allowed"].items():
        app_label, _, name = key.partition(".")
        assert (app_label, name) in new_nodes, f"{key} is not a new migration"
        assert isinstance(reason, str) and reason.strip(), f"{key} needs a reason"


def make_migration(*operations: Operation) -> migrations.Migration:
    migration = migrations.Migration("0099_example", "characters")
    migration.operations = list(operations)
    return migration


def test_guard_refuses_a_not_null_column_without_db_default() -> None:
    migration = make_migration(
        migrations.AddField("character", "title", models.TextField(blank=True))
    )
    assert "NOT NULL" in problems(migration, set())[0]


@pytest.mark.parametrize(
    "field",
    [
        models.TextField(null=True),
        models.TextField(db_default=""),
        models.ManyToManyField("characters.character"),
    ],
)
def test_guard_accepts_columns_old_code_can_ignore(field: models.Field) -> None:
    assert (
        problems(make_migration(migrations.AddField("character", "x", field)), set())
        == []
    )


def test_guard_accepts_a_not_null_column_on_a_new_table() -> None:
    migration = make_migration(
        migrations.AddField("ledger", "title", models.TextField())
    )
    assert problems(migration, {("characters", "ledger")}) == []


@pytest.mark.parametrize(
    "operation",
    [
        migrations.RemoveField("character", "title"),
        migrations.RenameField("character", "title", "name"),
        migrations.AlterModelTable("character", "characters_hero"),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RemoveField("character", "title")]
        ),
    ],
)
def test_guard_refuses_removals_and_renames(operation: Operation) -> None:
    assert "one release after" in problems(make_migration(operation), set())[0]


def test_guard_requires_a_reverse_for_run_python() -> None:
    def forwards(apps, schema_editor) -> None:  # pragma: no cover
        return None

    refused = make_migration(migrations.RunPython(forwards))
    assert "reverse_code" in problems(refused, set())[0]
    reversible = make_migration(
        migrations.RunPython(forwards, migrations.RunPython.noop)
    )
    assert problems(reversible, set()) == []


def test_created_models_reads_through_separate_database_and_state() -> None:
    migration = make_migration(
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.CreateModel(
                    "Ledger", [("id", models.AutoField(primary_key=True))]
                )
            ]
        )
    )
    assert created_models(migration) == {"ledger"}
