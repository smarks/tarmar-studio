"""Supplies become one row per thing carried, not one free-text line (#579).

The line is split on its commas and semicolons, a leading count becoming the
amount, so "3 torches, 40ft rope" arrives as two rows. Reversing joins the
rows back into a line of the same shape, so a rollback loses the itemisation
and nothing else.

Expand only: ``CampaignState.supplies`` keeps its column, now read by
nothing, so the colour still serving during the deploy can still select it
(issue #575). Issue #623 drops it a release later.
"""

import re

import django.db.models.deletion
import django.db.models.functions.text
import django.utils.timezone
from django.db import migrations, models

#: What a table writes between one thing and the next.
SEPARATORS = re.compile(r"[,;]")

#: A leading count: "3 torches", "40ft rope" keeps its measure in the name.
LEADING_COUNT = re.compile(r"^(?P<amount>\d+)\s+(?P<name>\S.*)$")

MAX_NAME_CHARS = 60
MAX_AMOUNT_CHARS = 40
MAX_LINE_CHARS = 400


def split_supplies(apps, schema_editor):
    """Turn each campaign's supplies line into rows, oldest campaign first."""
    CampaignState = apps.get_model("gm", "CampaignState")
    SupplyItem = apps.get_model("gm", "SupplyItem")
    for state in CampaignState.objects.exclude(supplies="").iterator():
        seen: set[str] = set()
        rows = []
        for fragment in SEPARATORS.split(state.supplies):
            said = fragment.strip()
            if not said:
                continue
            counted = LEADING_COUNT.match(said)
            amount = counted.group("amount")[:MAX_AMOUNT_CHARS] if counted else ""
            name = (counted.group("name") if counted else said)[:MAX_NAME_CHARS]
            if name.casefold() in seen:
                continue
            seen.add(name.casefold())
            rows.append(
                SupplyItem(
                    session_id=state.session_id,
                    name=name,
                    amount=amount,
                    created_at=state.updated_at,
                    updated_at=state.updated_at,
                )
            )
        SupplyItem.objects.bulk_create(rows)


def join_supplies(apps, schema_editor):
    """Write the rows back into the line the column held before."""
    CampaignState = apps.get_model("gm", "CampaignState")
    SupplyItem = apps.get_model("gm", "SupplyItem")
    said: dict[int, list[str]] = {}
    for item in SupplyItem.objects.order_by("session_id", "created_at", "pk"):
        written = f"{item.amount} {item.name}".strip() if item.amount else item.name
        if item.note:
            written = f"{written} ({item.note})"
        said.setdefault(item.session_id, []).append(written)
    for session_id, written in said.items():
        line = ", ".join(written)[:MAX_LINE_CHARS]
        state = CampaignState.objects.filter(session_id=session_id).first()
        if state is None:
            CampaignState.objects.create(session_id=session_id, supplies=line)
            continue
        state.supplies = line
        state.save(update_fields=["supplies"])


class Migration(migrations.Migration):
    dependencies = [
        ("gm", "0017_gmsession_summary_gmsession_summary_covers"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplyItem",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=60)),
                ("amount", models.CharField(blank=True, max_length=40)),
                ("note", models.CharField(blank=True, max_length=160)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="supplies",
                        to="gm.gmsession",
                    ),
                ),
            ],
            options={
                "ordering": ["session", "created_at", "pk"],
                "constraints": [
                    models.UniqueConstraint(
                        django.db.models.functions.text.Lower("name"),
                        models.F("session"),
                        name="one_row_per_named_supply_in_a_session",
                    )
                ],
            },
        ),
        migrations.RunPython(split_supplies, join_supplies),
    ]
