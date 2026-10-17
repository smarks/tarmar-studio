"""Tests for the GM session's character ledger (issues #356, #590).

The ledger is the snapshot the Characters pane and the model's state block
read. Its arithmetic is the real rules functions in ``characters.skills`` and
``characters.services`` -- the point being that the rules, not the model,
decide the numbers. No test here reaches Ollama.
"""

import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from characters import services
from characters.services import CharacterChangeError
from characters.skills import calculate_skill_points_from_xp, track_keys
from common.test_helpers import BaseTestCase
from gm.ledger import (
    ATTRIBUTE_FIELDS,
    DRAFT_FIELDS,
    DraftLedger,
    draft_character,
    draft_defaults,
    draft_values,
)
from gm.models import GMSession
from gm.testing import gm_replies, session_with_character
from gm.turn_engine.state import key_figures

# A complete set of attributes, so a character can take years of experience.
ATTRIBUTES = {
    "strength": 13,
    "dexterity": 12,
    "intelligence": 11,
    "wisdom": 10,
    "constitution": 14,
    "charisma": 9,
}


def survives_every_year():
    """Patch the prior-experience dice so a run cannot end in death.

    The rules being tested are the *skill-point* rules; a survival roll
    failing mid-run would make the XP total a coin flip. The dice are patched
    at the names ``characters.skills`` imports them under, so nothing about
    the arithmetic under test is stubbed.
    """
    return (
        patch("characters.skills.roll_survival_dice", return_value=(6, 6, 6, 18)),
        patch("characters.skills.roll_d20", return_value=1),
    )


def empty_draft() -> dict:
    return draft_defaults()


def built_draft(**overrides):
    """Values with the six attributes and a Fatigue roll settled."""
    return {
        **empty_draft(),
        **ATTRIBUTES,
        "fatigue_roll": 7,
        "name": "Kael",
        **overrides,
    }


def changed(values: dict, change) -> tuple[dict, str]:
    """Apply one rules function to ``values``; returns the new values and its note."""
    character = draft_character(values)
    note = change(character)
    if isinstance(note, tuple):
        note = note[0]
    return draft_values(character), note


class DraftStorageTests(TestCase):
    """A snapshot is Character field values, rehydrated as an unsaved Character."""

    def test_every_draft_field_is_a_real_character_field(self):
        from characters.models import Character

        model_fields = {field.name for field in Character._meta.get_fields()}
        self.assertTrue(set(DRAFT_FIELDS) <= model_fields)

    def test_rehydrating_gives_an_unsaved_character(self):
        character = draft_character(built_draft())
        self.assertIsNone(character.pk)
        self.assertEqual(character.strength, 13)

    def test_draft_values_round_trips(self):
        draft = built_draft()
        self.assertEqual(draft_values(draft_character(draft)), draft)

    def test_an_empty_draft_leaves_attributes_undecided(self):
        ledger = DraftLedger.from_draft(empty_draft())
        self.assertFalse(ledger.attributes_known)
        self.assertTrue(all(line.base is None for line in ledger.attributes))

    def test_pools_are_blank_until_the_fatigue_roll_is_in(self):
        ledger = DraftLedger.from_draft({**empty_draft(), **ATTRIBUTES})
        self.assertIsNone(ledger.fatigue)
        self.assertIsNone(ledger.body)

    def test_pools_come_from_the_character_model(self):
        draft = built_draft()
        ledger = DraftLedger.from_draft(draft)
        self.assertEqual(ledger.fatigue, draft_character(draft).fatigue)
        self.assertEqual(ledger.body, draft_character(draft).body)
        # And the figures themselves, by derived-pools.md, so a wrong formula
        # in the model cannot pass by being wrong on both sides (issue #799):
        # Fatigue = CON 14 + WIS 10 + INT 11 + max(DEX 12, STR 13) + roll 7,
        # Body = 2/3 of 55, rounded up.
        self.assertEqual(ledger.fatigue, 55)
        self.assertEqual(ledger.body, 37)


class SkillPointArithmeticTests(TestCase):
    """The ledger's sums, through the rules functions every action runs."""

    def test_spending_a_point_moves_it_from_free_to_the_skill(self):
        draft, note = changed(
            built_draft(free_skill_points=3),
            lambda character: services.apply_skill_points(character, "broadsword", 2),
        )
        self.assertEqual(draft["free_skill_points"], 1)
        self.assertEqual(draft["skills"]["broadsword"], 2)
        self.assertIn("broadsword", note)

    def test_spending_more_than_are_free_is_refused_not_silently_capped(self):
        with self.assertRaisesMessage(CharacterChangeError, "Only 1 free skill point"):
            changed(
                built_draft(free_skill_points=1),
                lambda character: services.apply_skill_points(
                    character, "broadsword", 3
                ),
            )

    def test_skill_level_is_priced_by_the_triangular_ladder(self):
        draft, _ = changed(
            built_draft(free_skill_points=6),
            lambda character: services.apply_skill_points(character, "broadsword", 6),
        )
        (skill,) = DraftLedger.from_draft(draft).skills
        # 6 points is exactly level 3 on the triangular ladder (1+2+3).
        self.assertEqual(
            (skill.name, skill.level_display, skill.points), ("broadsword", "III", 6)
        )

    def test_arcane_is_priced_at_double_like_everywhere_else(self):
        draft, _ = changed(
            built_draft(free_skill_points=6),
            lambda character: services.apply_skill_points(character, "arcane", 6),
        )
        (skill,) = DraftLedger.from_draft(draft).skills
        self.assertEqual(skill.level_display, "II")

    def test_a_year_of_experience_goes_through_the_simulator(self):
        survival, skill_roll = survives_every_year()
        with survival, skill_roll:
            draft, note = changed(
                built_draft(),
                lambda character: services.apply_experience_years(
                    character, "laborer", 1
                ),
            )
        self.assertEqual(draft["total_xp"], 500)
        self.assertEqual(draft["age"], 17)
        self.assertIn("laborer", note)

    def test_experience_before_attributes_is_refused(self):
        with self.assertRaises(CharacterChangeError):
            changed(
                empty_draft(),
                lambda character: services.apply_experience_years(
                    character, "laborer", 1
                ),
            )

    def test_seven_years_one_point_spent_is_the_ledgers_number_not_the_models(self):
        """Spencer's case, priced by the rules rather than in prose (issue #356)."""
        survival, skill_roll = survives_every_year()
        with survival, skill_roll:
            draft, _ = changed(
                built_draft(),
                lambda character: services.apply_experience_years(
                    character, "campaigner", 7
                ),
            )
        self.assertEqual(draft["total_xp"], 8400)
        self.assertEqual(calculate_skill_points_from_xp(8400), 5)
        earned = draft["free_skill_points"]
        draft, _ = changed(
            draft,
            lambda character: services.apply_skill_points(character, "broadsword", 1),
        )
        ledger = DraftLedger.from_draft(draft)
        self.assertEqual(ledger.experience_years, 7)
        self.assertEqual(ledger.skill_points.spent, 1)
        self.assertEqual(ledger.skill_points.remaining, earned - 1)
        self.assertEqual(ledger.skill_points.earned, earned)
        account = ledger.skill_points
        self.assertEqual(
            account.earned, account.from_experience_xp + account.from_track_grants
        )
        self.assertEqual(account.from_experience_xp, 5)

    def test_equipment_stacks_rather_than_duplicating(self):
        draft, _ = changed(
            built_draft(),
            lambda character: services.apply_equipment(character, "Dagger", 1),
        )
        draft, note = changed(
            draft, lambda character: services.apply_equipment(character, "dagger", 2)
        )
        # Stored as the catalog item it names, so it weighs and fights like
        # one everywhere that resolves the catalog (issue #719).
        self.assertEqual(draft["equipment"], [{"item_id": "dagger", "quantity": 3}])
        self.assertIn("Dagger", note)

    def test_a_catalog_item_the_sheet_bought_is_stacked_onto(self):
        """The GM handing over a dagger adds to the one already carried (#719).

        A catalog entry stores no name, so comparing stored names against the
        incoming one was always false: the character ended up with two lines,
        the second carrying no item_id and so resolving no weight, damage or
        category anywhere.
        """
        draft = built_draft(equipment=[{"item_id": "dagger", "quantity": 1}])

        draft, note = changed(
            draft, lambda character: services.apply_equipment(character, "dagger", 2)
        )

        self.assertEqual(draft["equipment"], [{"item_id": "dagger", "quantity": 3}])
        self.assertIn("3 × Dagger", note)

    def test_something_the_catalog_does_not_sell_stays_a_custom_item(self):
        draft, note = changed(
            built_draft(),
            lambda character: services.apply_equipment(character, "Vane's key", 1),
        )

        # Stored as the sheet stores a custom item (#927).
        self.assertEqual(
            draft["equipment"],
            [
                {
                    "item_id": "custom",
                    "name": "Vane's key",
                    "category": "gear",
                    "weight": 0.0,
                    "cost": 0.0,
                    "quantity": 1,
                    "in_pack": False,
                    "notes": "",
                }
            ],
        )
        self.assertIn("Vane's key", note)

    def test_the_ledger_names_a_catalog_item_rather_than_calling_it_unknown(self):
        """What the pane shows and what the model is handed (issue #718)."""
        draft = built_draft(equipment=[{"item_id": "dagger", "quantity": 2}])

        ledger = DraftLedger.from_draft(draft)

        self.assertEqual(
            [(line.name, line.quantity) for line in ledger.equipment],
            [("Dagger", 2)],
        )

    def test_the_fatigue_roll_is_stored_and_opens_the_pools(self):
        draft, note = changed(
            {**empty_draft(), **ATTRIBUTES},
            lambda character: services.apply_fatigue_roll(character, 9),
        )
        self.assertEqual(draft["fatigue_roll"], 9)
        self.assertIn("Fatigue roll", note)
        self.assertIsNotNone(DraftLedger.from_draft(draft).fatigue)

    def test_a_dead_character_takes_no_more_years(self):
        with self.assertRaisesMessage(CharacterChangeError, "is dead"):
            changed(
                built_draft(is_alive=False, death_cause="Failed a survival check"),
                lambda character: services.apply_experience_years(
                    character, "laborer", 1
                ),
            )

    def test_an_unknown_track_or_a_run_past_the_maximum_is_refused(self):
        for track, years in (("wizard", 1), ("laborer", 999)):
            with self.subTest(track=track, years=years):
                with self.assertRaises(CharacterChangeError):
                    changed(
                        built_draft(),
                        lambda character,
                        track=track,
                        years=years: services.apply_experience_years(
                            character, track, years
                        ),
                    )

    def test_a_random_track_resolves_to_a_real_one(self):
        survival, skill_roll = survives_every_year()
        with survival, skill_roll:
            draft, _ = changed(
                built_draft(),
                lambda character: services.apply_experience_years(
                    character, "random", 1
                ),
            )
        (track,) = DraftLedger.from_draft(draft).experience_tracks
        self.assertIn(track, track_keys())

    def test_a_nonsense_quantity_or_point_count_is_refused(self):
        for change in (
            lambda character: services.apply_skill_points(character, "broadsword", 0),
            lambda character: services.apply_equipment(character, "Rope", 0),
        ):
            with self.assertRaises(CharacterChangeError):
                changed(built_draft(free_skill_points=3), change)

    def test_a_malformed_experience_log_entry_is_ignored_not_fatal(self):
        ledger = DraftLedger.from_draft(
            built_draft(experience_log=["not a dict", {"track": "laborer"}, {}])
        )
        self.assertEqual(ledger.experience_tracks, ("laborer",))
        self.assertEqual(ledger.skill_points.from_track_grants, 0)

    def test_setting_a_non_attribute_is_refused(self):
        with self.assertRaises(CharacterChangeError):
            changed(
                built_draft(),
                lambda character: services.apply_attribute(character, "luck", 12),
            )


class LedgerPanelTests(BaseTestCase):
    """The Characters pane shows a character's live totals."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="panel")
        self.login_as(self.user)

    def _chat(self, session):
        return self.client.get(f"{reverse('gm:chat')}?session={session.pk}")

    def test_a_session_with_no_character_shows_no_ledger(self):
        session = GMSession.objects.create(user=self.user, name="S")
        self.assertNotContains(self._chat(session), "gm-draft-ledger")

    def test_the_pane_and_the_state_block_read_the_same_record(self):
        survival, skill_roll = survives_every_year()
        with survival, skill_roll:
            draft, _ = changed(
                built_draft(),
                lambda character: services.apply_experience_years(
                    character, "campaigner", 7
                ),
            )
        draft, _ = changed(
            draft,
            lambda character: services.apply_skill_points(character, "broadsword", 1),
        )
        session = session_with_character(self.user, draft, name="S")
        remaining = DraftLedger.from_draft(draft).skill_points.remaining

        response = self._chat(session)

        self.assertContains(response, "gm-draft-ledger")
        self.assertContains(response, f"{remaining} free")
        self.assertContains(response, "Kael")
        character = session.attached_characters().get()
        self.assertIn(
            f"{character.free_skill_points} free skill points", key_figures(character)
        )
        self.assertEqual(character.free_skill_points, remaining)


class PartialDraftTests(TestCase):
    """Some attributes but not all -- the ordinary creation state (issue #374).

    A character with one score written down used to 500 every later turn in
    its session. Nothing here mocks anything: the numbers are the rules'.
    """

    def test_every_prefix_of_a_creation_run_reads(self):
        labels = {field: label for label, field in ATTRIBUTE_FIELDS}
        draft = empty_draft()
        for field, value in ATTRIBUTES.items():
            draft, _note = changed(
                draft,
                lambda character, field=field, value=value: services.apply_attribute(
                    character, field, value
                ),
            )
            ledger = DraftLedger.from_draft(draft)
            line = next(
                line for line in ledger.attributes if line.label == labels[field]
            )
            self.assertEqual(line.base, value)
            self.assertIn(
                f"{labels[field][:3].upper()} {value}",
                key_figures(draft_character(draft)),
            )

    def test_a_rolled_attribute_offers_no_modifier_until_all_are_in(self):
        ledger = DraftLedger.from_draft({**empty_draft(), "strength": 13})
        strength = ledger.attributes[0]
        self.assertEqual(strength.base, 13)
        self.assertIsNone(strength.modifier)
        complete = DraftLedger.from_draft(built_draft()).attributes[0]
        self.assertIsNotNone(complete.modifier)

    def test_pools_pending_names_what_is_actually_missing(self):
        self.assertEqual(
            DraftLedger.from_draft({**empty_draft(), "fatigue_roll": 7}).pools_pending,
            "all six attributes",
        )
        self.assertEqual(
            DraftLedger.from_draft({**empty_draft(), **ATTRIBUTES}).pools_pending,
            "the 2d6 Fatigue roll",
        )
        self.assertEqual(
            DraftLedger.from_draft(empty_draft()).pools_pending,
            "all six attributes and the 2d6 Fatigue roll",
        )
        self.assertEqual(DraftLedger.from_draft(built_draft()).pools_pending, "")


class PartialDraftPanelTests(BaseTestCase):
    """What the Characters pane shows while a character is half-decided."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="partial")
        self.login_as(self.user)

    def test_the_pane_shows_a_rolled_score_while_the_rest_are_open(self):
        session = session_with_character(
            self.user, {**empty_draft(), "strength": 13}, name="S"
        )
        response = self.client.get(f"{reverse('gm:chat')}?session={session.pk}")
        self.assertContains(response, "gm-draft-ledger")
        self.assertContains(response, ">13</span>")
        self.assertContains(response, "Not rolled yet")

    def test_the_pane_says_what_the_pools_are_waiting_for(self):
        session = session_with_character(
            self.user, {**empty_draft(), **ATTRIBUTES}, name="S"
        )
        response = self.client.get(f"{reverse('gm:chat')}?session={session.pk}")
        self.assertContains(response, "Needs the 2d6 Fatigue roll first")


class PartialDraftStreamTests(BaseTestCase):
    """The session keeps working with a half-decided character (issue #374)."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="draft500")
        self.login_as(self.user)

    def test_the_turn_after_one_attribute_is_written_down_answers_with_it(self):
        session = session_with_character(
            self.user, {**empty_draft(), "strength": 22, "name": "Kthal"}, name="Kthal"
        )
        stream = gm_replies("Kthal has STR 22.")
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat", side_effect=stream
        ):
            response = self.client.post(
                "/tarmar/gm/stream/",
                json.dumps(
                    {"session_id": session.pk, "message": "What is Kthal's STR?"}
                ),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            body = b"".join(response.streaming_content).decode()
        self.assertIn("Kthal has STR 22.", body)
        self.assertNotIn("notices", body)
        self.assertIn("STR 22, DEX not recorded", stream.calls[-1][-1]["content"])
