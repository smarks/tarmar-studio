"""The browser tests' pinned dice, through a real request (issue #959).

``e2e/dice.js`` names a seed in the ``X-E2E-Dice-Seed`` header and asserts the
faces it gives. These tests hold the server side of that bargain: the header
reaches the dice ``characters.skills.roll_survival_dice`` rolls, a request
without it is untouched, and the middleware refuses to exist outside a DEBUG
process that turned the switch on. ``settings.py``'s own refusal is tested in
``tarmar_studio/tests/test_settings.py``.
"""

import random
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpRequest, HttpResponse
from django.test import SimpleTestCase, override_settings

from characters.models import Character
from common.middleware import PINNED_DICE_HEADER, PinnedDiceMiddleware
from common.test_helpers import BaseTestCase

PINNED_DICE_MIDDLEWARE = "common.middleware.PinnedDiceMiddleware"


class LaborerYearRequestCase(BaseTestCase):
    """A player's character and a way to roll it one Laborer year."""

    def setUp(self) -> None:
        super().setUp()
        self.user = self.create_user()
        self.character = Character.objects.create(
            user=self.user,
            name="Pinned",
            strength=14,
            dexterity=12,
            intelligence=12,
            wisdom=12,
            constitution=12,
            charisma=12,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
        )
        self.login_as(self.user)
        self.add_years_url = f"/tarmar/characters/{self.character.pk}/add-years/"

    def roll_one_laborer_year(self, headers: dict[str, str] | None = None):
        return self.client.post(
            self.add_years_url,
            '{"track": "laborer"}',
            content_type="application/json",
            headers=headers or {},
        )


@override_settings(
    DEBUG=True,
    E2E_DICE_SEEDING=True,
    MIDDLEWARE=[*settings.MIDDLEWARE, PINNED_DICE_MIDDLEWARE],
)
class PinnedDiceRequestTest(LaborerYearRequestCase):
    """A Laborer year rolled through the add-years view, seeded or not."""

    def test_the_header_seeds_the_years_d20_and_survival_dice(self) -> None:
        # A year rolls its d20 for the skill, then the 3d6 survival check
        # (characters/skills.py); the e2e helper's expected line relies on it.
        seeded = random.Random(3)
        expected_skill_roll = seeded.randint(1, 20)
        expected_survival_dice = [seeded.randint(1, 6) for _ in range(3)]

        response = self.roll_one_laborer_year({PINNED_DICE_HEADER: "3"})

        self.assertEqual(response.status_code, 200, response.content)
        year = response.json()["run_result"]["years"][0]
        self.assertEqual(year["roll"], expected_skill_roll)
        self.assertEqual(year["survival_check"]["dice"], expected_survival_dice)

    def test_a_request_without_the_header_rolls_the_usual_dice(self) -> None:
        with patch.object(random, "randint", side_effect=[10, 6, 6, 6]):
            response = self.roll_one_laborer_year()

        self.assertEqual(response.status_code, 200, response.content)
        year = response.json()["run_result"]["years"][0]
        self.assertEqual(year["roll"], 10)
        self.assertEqual(year["survival_check"]["dice"], [6, 6, 6])

    def test_a_seed_that_is_not_a_whole_number_is_refused(self) -> None:
        response = self.roll_one_laborer_year({PINNED_DICE_HEADER: "three"})

        self.assertEqual(response.status_code, 400)
        self.assertIn(PINNED_DICE_HEADER, response.content.decode())
        self.assertEqual(response["Content-Type"], "text/plain")
        self.character.refresh_from_db()
        self.assertEqual(self.character.experience_years, 0)


class DefaultStackIgnoresTheHeaderTest(LaborerYearRequestCase):
    """With the switch off -- the stack every real process runs -- the header
    is just a header: the dice are the usual ones and nothing is refused."""

    def test_the_middleware_is_not_in_the_stack(self) -> None:
        self.assertNotIn(PINNED_DICE_MIDDLEWARE, settings.MIDDLEWARE)

    def test_a_seed_header_changes_nothing(self) -> None:
        for named_seed in ("3", "three"):
            with self.subTest(named_seed=named_seed):
                with patch.object(random, "randint", side_effect=[10, 6, 6, 6]):
                    response = self.roll_one_laborer_year(
                        {PINNED_DICE_HEADER: named_seed}
                    )

                self.assertEqual(response.status_code, 200, response.content)
                year = response.json()["run_result"]["years"][0]
                self.assertEqual(year["roll"], 10)
                self.assertEqual(year["survival_check"]["dice"], [6, 6, 6])


def answer(request: HttpRequest) -> HttpResponse:
    return HttpResponse("ok")


class PinnedDiceMiddlewareInstallTest(SimpleTestCase):
    """The middleware removes itself unless DEBUG and the switch are both on."""

    def test_it_refuses_to_run_without_debug_or_without_the_switch(self) -> None:
        for debug, seeding in ((False, True), (True, False), (False, False)):
            with self.subTest(debug=debug, seeding=seeding):
                with override_settings(DEBUG=debug, E2E_DICE_SEEDING=seeding):
                    with self.assertRaises(MiddlewareNotUsed):
                        PinnedDiceMiddleware(answer)

    @override_settings(DEBUG=True, E2E_DICE_SEEDING=True)
    def test_it_runs_with_both(self) -> None:
        middleware = PinnedDiceMiddleware(answer)
        self.assertEqual(middleware(HttpRequest()).content, b"ok")
