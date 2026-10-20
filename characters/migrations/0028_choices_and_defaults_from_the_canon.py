"""The background fields' options and defaults come from the canon (issue #66).

``provenance_class``, ``location`` and ``wealth_level`` lose their ``choices``,
which held the Tarmar canon's table labels, and the four background fields and
``starting_coin`` take callable defaults that read the loaded canon's
``defaults`` block (``characters.models.default_location`` and its siblings),
as ``age`` reads the starting age since 0027. Neither ``choices`` nor a
Python-side ``default`` is part of a column, so no column changes and every
row keeps the values it holds. PostgreSQL emits no statement for it; SQLite
rebuilds the table once per field, as it does for every ``AlterField`` and as
it did for 0027, copying the rows across unchanged.
"""

from django.db import migrations, models

import characters.models


class Migration(migrations.Migration):
    dependencies = [
        ("characters", "0027_character_rules_read_from_the_canon"),
    ]

    operations = [
        migrations.AlterField(
            model_name="character",
            name="location",
            field=models.CharField(
                default=characters.models.default_location, max_length=20
            ),
        ),
        migrations.AlterField(
            model_name="character",
            name="provenance_class",
            field=models.CharField(
                default=characters.models.default_social_class, max_length=20
            ),
        ),
        migrations.AlterField(
            model_name="character",
            name="provenance_detail",
            field=models.CharField(
                blank=True,
                default=characters.models.default_provenance_detail,
                max_length=50,
            ),
        ),
        migrations.AlterField(
            model_name="character",
            name="starting_coin",
            field=models.PositiveIntegerField(
                blank=True, default=characters.models.default_starting_coin, null=True
            ),
        ),
        migrations.AlterField(
            model_name="character",
            name="wealth_level",
            field=models.CharField(
                default=characters.models.default_wealth_level, max_length=20
            ),
        ),
    ]
