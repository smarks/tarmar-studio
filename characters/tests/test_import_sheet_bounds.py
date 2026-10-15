"""Issue #940: a Game Master's import is held to the bounds the sheet holds him to.

Spencer's ruling: refuse what the sheet would refuse, with the importer's
error; a Game Master edits on the sheet afterwards, where it is recorded.
The checks are the sheet's own: the pools setting for the wounds and the
spent mana, and ``Character.clean_field_value`` for the derived counts. The
aging penalties are set from the age on save. The purse is held to its
column only, since a Game Master's purchase before the wealth roll leaves a
stored purse below nothing (#851) and the sheet keeps it.
"""

import json
import random
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile

from character_export.export_utils import generate_character_json
from characters import magic, purse, services
from characters.import_service import (
    CharacterImportError,
    held_to_player,
    save_imported_character,
    validate_import_payload,
)
from characters.models import (
    CHARACTER_INT_FIELD_MAX,
    CHARACTER_INT_FIELD_MIN,
    MAX_INT_FIELD_VALUE,
    MAX_SMALLINT_FIELD_VALUE,
    Character,
)
from characters.skills import ALL_TRACKS
from common.test_helpers import BaseTestCase, create_character
from gm.character_schema import extraction_to_import_payload

from .test_import import _build_payload

#: The counts the rules derive, each with its column's ceiling.
DERIVED_COUNTS: dict[str, int] = {
    "age": MAX_SMALLINT_FIELD_VALUE,
    "experience_terms": MAX_SMALLINT_FIELD_VALUE,
    "free_skill_points": MAX_SMALLINT_FIELD_VALUE,
    "free_spell_points": MAX_SMALLINT_FIELD_VALUE,
    "skill_points_owed": MAX_INT_FIELD_VALUE,
    "money_earned": MAX_INT_FIELD_VALUE,
}


class GameMasterImportHeldToTheSheetTest(BaseTestCase):
    """Each value only a Game Master's import keeps, at and past its bound."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm("bounds-dm")

    def _import(self, **overrides: object) -> Character:
        imported = held_to_player(
            validate_import_payload(_build_payload(overrides=overrides)),
            is_game_master=True,
            importer=self.game_master,
        )
        return save_imported_character(self.game_master, imported)

    def _refused(self, **overrides: object) -> str:
        with self.assertRaises(CharacterImportError) as refused:
            self._import(**overrides)
        self.assertFalse(Character.objects.filter(user=self.game_master).exists())
        return str(refused.exception)

    def test_mana_spent_above_the_pool_is_refused(self):
        self.assertEqual(
            self._refused(free_spell_points=5, mana_spent=6),
            "Import failed: mana_spent: Mana -1 is below 0",
        )

    def test_mana_spent_at_the_pool_is_kept(self):
        character = self._import(free_spell_points=5, mana_spent=5)
        self.assertEqual((character.free_spell_points, character.mana_spent), (5, 5))

    def test_a_wound_on_a_character_without_pools_is_refused(self):
        refusal = self._refused(fatigue_roll=None, fatigue_damage=3)
        self.assertTrue(
            refusal.startswith("Import failed: fatigue_damage: Fatigue and Body"),
            refusal,
        )

    def test_a_wound_within_the_pool_is_kept(self):
        character = self._import(fatigue_damage=3, body_damage=2)
        self.assertEqual((character.fatigue_damage, character.body_damage), (3, 2))

    def test_the_aging_penalties_are_the_ages_not_the_files(self):
        """``Character.save`` sets them from the age (#680), so none is trusted."""
        young = self._import(age=20, aging_str_penalty=5, aging_con_penalty=-3)
        self.assertEqual((young.aging_str_penalty, young.aging_con_penalty), (0, 0))
        expected = Character(age=70)
        expected.reconcile_aging_penalties()
        old = self._import(name="Elder", age=70, aging_str_penalty=0)
        for column, _attribute in Character._AGING_PENALTY_FIELDS:
            with self.subTest(column):
                self.assertEqual(getattr(old, column), getattr(expected, column))
        self.assertNotEqual(old.aging_str_penalty, 0)

    def test_a_derived_count_past_its_column_is_refused(self):
        for field_name, ceiling in DERIVED_COUNTS.items():
            with self.subTest(field_name):
                self.assertEqual(
                    self._refused(**{field_name: ceiling + 1}),
                    f"Import failed: {field_name}: Value must be {ceiling} or fewer",
                )
                # The age's floor is the starting age (#958); test_age_floor
                # holds it by name, so a raised floor on any other count
                # still gets its below-floor check here.
                if field_name != "age":
                    self.assertEqual(CHARACTER_INT_FIELD_MIN[field_name], 0)
                    self.assertEqual(
                        self._refused(**{field_name: -1}),
                        f"Import failed: {field_name}: Value must be positive",
                    )

    def test_a_derived_count_at_its_column_is_accepted(self):
        for field_name, ceiling in DERIVED_COUNTS.items():
            with self.subTest(field_name):
                imported = validate_import_payload(
                    _build_payload(overrides={field_name: ceiling})
                )
                self.assertEqual(imported.fields[field_name], ceiling)

    def test_the_mana_pool_cap_is_the_columns(self):
        """A Game Master's grant and the import stop at the same number."""
        self.assertEqual(
            magic.MAX_MANA_POOL, CHARACTER_INT_FIELD_MAX["free_spell_points"]
        )

    def test_the_chats_extraction_is_held_to_the_same_bounds(self):
        for field_name in ("age", "free_skill_points", "money_earned"):
            with self.subTest(field_name):
                ceiling = DERIVED_COUNTS[field_name]
                payload = extraction_to_import_payload(
                    {"name": "Extracted", field_name: ceiling}
                )
                validate_import_payload(payload)
                payload["data"][field_name] = ceiling + 1
                with self.assertRaises(CharacterImportError) as refused:
                    validate_import_payload(payload)
                self.assertEqual(
                    str(refused.exception),
                    f"Import failed: {field_name}: Value must be {ceiling} or fewer",
                )

    def test_a_players_import_still_drops_them(self):
        """What a player never keeps is reset, not refused (#744)."""
        player = self.create_player("bounds-player")
        imported = held_to_player(
            validate_import_payload(
                _build_payload(
                    overrides={
                        "free_spell_points": 5,
                        "mana_spent": 6,
                        "coin_adjustment": "-20",
                    }
                )
            ),
            is_game_master=False,
            importer=player,
        )
        character = save_imported_character(player, imported)
        self.assertEqual(
            (
                character.free_spell_points,
                character.mana_spent,
                character.coin_adjustment,
            ),
            (0, 0, Decimal(0)),
        )


def _exported(character: Character) -> dict:
    return json.loads(generate_character_json(character))["data"]


class SheetBuiltGameMasterCharacterRoundTripTest(BaseTestCase):
    """A Game Master's character built on the sheet comes back unchanged."""

    #: A seed under which the run below survives, so the pools are set.
    SURVIVING_SEED = 940

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm("round-trip-bounds-dm")
        self.login_as(self.game_master)

    def _round_trip(self, built: Character) -> None:
        upload = SimpleUploadedFile(
            "character.json",
            generate_character_json(built).encode("utf-8"),
            content_type="application/json",
        )
        response = self.client.post("/tarmar/characters/import/", {"file": upload})
        self.assertEqual(response.status_code, 200, response.content.decode()[:400])
        imported = Character.objects.get(pk=response.json()["character_id"])
        self.assertEqual(_exported(imported), _exported(built))

    def test_round_trip(self):
        built = create_character(self.game_master, name="Veteran")
        # The run is dice; seeded, so it survives every time and the pools
        # below are always set and carried.
        state = random.getstate()
        random.seed(self.SURVIVING_SEED)
        try:
            services.add_experience_years(
                built.pk,
                ALL_TRACKS[0],
                8,
                actor=self.game_master,
                is_game_master=True,
            )
        finally:
            random.setstate(state)
        services.update_field(
            built.pk, "total_xp", 9000, actor=self.game_master, is_game_master=True
        )
        built.refresh_from_db()
        self.assertTrue(built.is_alive, built.death_cause)
        magic.grant_mana(built, 6, is_game_master=True)
        purse.move_coin(built, -built.purse, reason="everything")
        built.save()
        services.set_current_pools(
            built.pk,
            fatigue=(built.fatigue or 0) - 4,
            body=(built.body or 0) - 1,
            mana=2,
            actor=self.game_master,
            is_game_master=True,
        )
        built.refresh_from_db()
        self.assertEqual(
            (built.fatigue_damage, built.body_damage, built.mana_spent), (4, 1, 4)
        )
        self.assertEqual(built.purse, Decimal(0))
        self.assertGreater(built.age, 16)
        self._round_trip(built)

    def test_a_pool_lowered_to_the_mana_spent_round_trips(self):
        """The lowest Pool ``grant_mana`` now leaves is at the mana spent."""
        built = create_character(
            self.game_master, name="Drained", free_spell_points=6, mana_spent=4
        )
        magic.grant_mana(built, -2, is_game_master=True)
        built.save()
        self.assertEqual((built.free_spell_points, built.mana_spent), (4, 4))
        self._round_trip(built)

    def test_a_purse_below_nothing_after_a_purchase_before_wealth_round_trips(self):
        """#851: a Game Master's purchase before the roll stays paid for."""
        built = create_character(
            self.game_master,
            name="Indebted",
            wealth_recorded=False,
            starting_coin=100,
        )
        purse.move_coin(built, Decimal(-80), reason="a sword")
        built.save()
        services.update_field(
            built.pk,
            "wealth_level",
            "Subsistence",
            actor=self.game_master,
            is_game_master=True,
        )
        built.refresh_from_db()
        self.assertTrue(built.wealth_recorded)
        self.assertLess(built.purse, 0)
        self._round_trip(built)
