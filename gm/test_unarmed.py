"""A character with no weapon is named before and during a fight (issue #844).

The Battle tab names who would fight bare-handed, from state, and the sheet
offers the gear step creation lacked; both stay while they are true.
"""

from django.urls import reverse
from django.utils.html import escape

from battle.models import Battle
from battle.services import (
    bare_handed_figures,
    bare_handed_line,
    unarmed_attached_line,
    unarmed_line,
)
from battle.wording import words
from characters.models import carries_a_weapon
from common.test_helpers import BaseTestCase, create_character
from gm.testing import game_master_sets_the_foes_asked_for

from .fight import start_fight
from .models import GMSession
from .unarmed import unarmed_notice

SWORD = [{"item_id": "broadsword", "quantity": 1}]


class UnarmedNoticeTest(BaseTestCase):
    """The Battle tab's line."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user(username="alice")
        self.bare = create_character(self.player, name="Bare", equipment=[])
        self.armed = create_character(self.player, name="Armed", equipment=SWORD)
        self.session = GMSession.objects.create(user=self.player, name="Road")
        self.session.characters.add(self.bare, self.armed)

    def test_what_counts_as_a_weapon_is_what_the_board_seats(self) -> None:
        self.assertTrue(carries_a_weapon(SWORD))
        self.assertFalse(carries_a_weapon([]))
        self.assertFalse(carries_a_weapon(None))
        self.assertFalse(carries_a_weapon([{"item_id": "backpack", "quantity": 1}]))
        # "varies" damage: the board passes a club over (#846).
        self.assertFalse(carries_a_weapon([{"item_id": "club", "quantity": 1}]))

    def test_before_the_fight_the_attached_are_named(self) -> None:
        self.assertEqual(
            unarmed_notice(self.session),
            words().services.unarmed_attached.format(unarmed=unarmed_line(["Bare"])),
        )
        self.armed.equipment = []
        self.armed.save()
        self.assertEqual(
            unarmed_notice(self.session),
            words().services.unarmed_attached.format(
                unarmed=unarmed_line(["Bare", "Armed"])
            ),
        )

    def test_during_the_fight_the_bare_handed_figures_are_named(self) -> None:
        # A player's own session: the foes are the Game Master's to set (#841);
        # this test is not about who chooses them, so the request stands.
        with game_master_sets_the_foes_asked_for():
            battle = start_fight(self.session, foes_text="a gray wolf", seed=4).battle
        self.session.refresh_from_db()
        self.assertEqual(bare_handed_figures(battle), ["Bare"])
        # The board's responses carry the same sentence, to repaint it.
        self.login_as(self.player)
        state = self.client.get(f"/tarmar/battles/{battle.pk}/state/").json()
        self.assertEqual(
            state["bare_handed"],
            words().services.bare_handed_one.format(names="Bare"),
        )
        self.assertEqual(
            unarmed_notice(self.session),
            words().services.bare_handed_one.format(names="Bare"),
        )
        # Once it is over the board names nobody, and the tab's line goes
        # back to who is attached.
        Battle.objects.filter(pk=battle.pk).update(status=Battle.STATUS_FINISHED)
        self.session.refresh_from_db()
        assert self.session.battle is not None
        self.assertEqual(bare_handed_figures(self.session.battle), [])
        attached = words().services.unarmed_attached.format(
            unarmed=unarmed_line(["Bare"])
        )
        self.assertEqual(unarmed_notice(self.session), attached)
        # The board's payload carries the same line once the fight is over,
        # so the tab has the warning before the next Fight (second review).
        self.assertEqual(
            bare_handed_line(self.session.battle), unarmed_notice(self.session)
        )
        state = self.client.get(f"/tarmar/battles/{battle.pk}/state/").json()
        self.assertEqual(state["bare_handed"], attached)
        self.assertFalse(state["live"])

    def test_nobody_unarmed_and_no_session_say_nothing(self) -> None:
        self.session.characters.remove(self.bare)
        self.assertEqual(unarmed_notice(self.session), "")
        self.assertEqual(unarmed_notice(None), "")
        self.assertEqual(unarmed_attached_line(None), "")

    def test_a_classic_or_unstarted_battle_names_nobody(self) -> None:
        self.assertEqual(
            bare_handed_figures(Battle(name="x", seed=1, rules_profile="classic")), []
        )

    def test_the_battle_tab_renders_it(self) -> None:
        self.login_as(self.player)
        html = self.client.get(
            reverse("gm:chat") + f"?session={self.session.pk}"
        ).content.decode()
        self.assertIn('id="gm-panel-unarmed"', html)
        self.assertIn(
            escape(
                words().services.unarmed_attached.format(unarmed=unarmed_line(["Bare"]))
            ),
            html,
        )


class SheetGearStepTest(BaseTestCase):
    """The sheet's gear step, repainted with every change."""

    def setUp(self) -> None:
        super().setUp()
        self.player = self.create_user(username="alice")
        self.character = create_character(self.player, name="Bare", equipment=[])
        self.login_as(self.player)

    def sheet(self) -> str:
        return self.client.get(
            reverse("characters:edit", args=[self.character.pk])
        ).content.decode()

    def test_a_character_with_no_weapon_is_offered_the_gear_step(self) -> None:
        html = self.sheet()
        self.assertIn('id="gear-step"', html)
        self.assertIn(escape(unarmed_line(["Bare"])), html)
        self.assertIn('<a href="#equipment">Buy gear in Equipment</a>', html)

    def test_buying_a_weapon_repaints_it_away(self) -> None:
        self.character.starting_coin = 500
        self.character.wealth_recorded = True
        self.character.save()
        response = self.post_json(
            reverse("characters:equipment_add", args=[self.character.pk]),
            {"item_id": "dagger", "quantity": 1},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('id="gear-step"', response.json()["completion_html"])
        self.assertNotIn('id="gear-step"', self.sheet())

    def test_a_reader_who_cannot_edit_is_not_offered_it(self) -> None:
        self.character.is_public = True
        self.character.save()
        self.logout()
        self.login_as(self.create_user(username="reader"))
        self.assertNotIn('id="gear-step"', self.sheet())

    def test_the_dead_and_the_armed_are_not_offered_it(self) -> None:
        self.character.equipment = SWORD
        self.character.save()
        self.assertNotIn('id="gear-step"', self.sheet())
        self.character.equipment = []
        self.character.is_alive = False
        self.character.save()
        self.assertNotIn('id="gear-step"', self.sheet())
