"""The six core attributes are listed once and reused in order."""

from django.test import SimpleTestCase

from bestiary.generation import _VARIED_STATS
from bestiary.models import Beast
from character_export.export_utils import EXPORTABLE_FIELDS
from characters.constants import CHARACTER_ATTRIBUTE_FIELDS
from characters.models import Character
from characters.views import _CREATION_STAT_FIELDS

SHEET_ORDER = (
    "strength",
    "dexterity",
    "intelligence",
    "wisdom",
    "constitution",
    "charisma",
)


class CharacterAttributeFieldsTest(SimpleTestCase):
    def test_names_the_six_model_fields_in_sheet_order(self):
        self.assertEqual(CHARACTER_ATTRIBUTE_FIELDS, SHEET_ORDER)
        for field in CHARACTER_ATTRIBUTE_FIELDS:
            Character._meta.get_field(field)
            Beast._meta.get_field(field)

    def test_creation_export_and_variation_lists_keep_their_order(self):
        self.assertEqual(_CREATION_STAT_FIELDS[:6], SHEET_ORDER)
        self.assertEqual(EXPORTABLE_FIELDS[:7], ("name", *SHEET_ORDER))
        self.assertEqual(_VARIED_STATS, (*SHEET_ORDER, "movement", "stops"))
