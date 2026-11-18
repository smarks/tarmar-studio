"""The Game Master's grant control on the battle page (tarmar-studio #208).

A granted working is a ruling on the battle (``test_working_grants``). This is
its control: the panel beside the magic bounds', the endpoint its forms post
to, and the record in the battle's JSON. The fight's Game Master grants and
withdraws; a player reads who granted what and when, as the battle log tells
them, and is given no form and refused by the endpoint. The grant form names a
working by its effect and its tier, and the key is the engine's for them; a
withdrawal names the key the record holds.

Every test runs under the default canon's magic rules, whichever canon is
loaded (its working Figment needs a grant, Sear and Knit need none), and under
:data:`~.rulings_words.FIXED_WORDS`: each sentence is its key followed by what
filled it, in the order of the names it may use.
"""

import json
from dataclasses import replace

from django.urls import reverse
from tarmar_engine.state import CraftState

from battle import magic_bounds, working_grants
from battle.models import Battle, BattleEvent
from battle.persistence import CLASSIC_PROFILE
from battle.refusals import BattleRefusal
from battle.services import create_battle, set_magic_bounds_lifted
from battle.views import (
    WORKING_GRANTS_CONTEXT,
    WORKING_GRANTS_PANEL_ID,
    working_grants_panel,
)
from common.helpers import SCRIPT_CALLER_HEADERS
from common.test_helpers import BaseTestCase, forms_of_class, inner_markup_of

from . import true_magic_play as play
from .magic_fixtures import DEFAULT_MAGIC_RULES, seated_rules
from .rulings_words import with_fixed_words
from .test_true_magic_stage import menu_and_stage
from .test_working_grants import (
    FIRST_TIER,
    GRANTED_EFFECT,
    NEEDS_NO_GRANT,
    NO_TRUE_MAGIC,
    PAST_THE_CAP,
    SECOND_TIER,
    DefaultCanonTestCase,
    TrueMageBattleTestCase,
    true_mage,
)

COMBATANT_FIELD = working_grants.COMBATANT_FIELD
WORKING_FIELD = working_grants.WORKING_FIELD
EFFECT_FIELD = working_grants.EFFECT_FIELD
TIER_FIELD = working_grants.TIER_FIELD
GRANTED_FIELD = working_grants.GRANTED_FIELD

#: A withdrawal's button, by the word on it: the word is in each button's
#: accessible name too, so the word alone does not count the buttons.
WITHDRAW_BUTTON = ">working_grants.withdraw_button</button>"

assert DEFAULT_MAGIC_RULES.true_magic is not None
#: The default canon with True Magic and no working that needs a grant: it
#: still builds Figment, and a True mage still crafts workings.
NO_EFFECT_NEEDS_A_GRANT = replace(
    DEFAULT_MAGIC_RULES,
    true_magic=replace(
        DEFAULT_MAGIC_RULES.true_magic,
        effects=tuple(
            replace(effect, needs_grant=False)
            for effect in DEFAULT_MAGIC_RULES.true_magic.effects
        ),
    ),
)


def json_block(page: str, block_id: str) -> dict:
    """One of a page's ``json_script`` blocks, parsed.

    Args:
        page: The page's HTML.
        block_id: The block's element id.
    """
    return json.loads(inner_markup_of(page, block_id))


class GrantControlTestCase(TrueMageBattleTestCase):
    """The battle of ``TrueMageBattleTestCase``, its pages and its endpoint."""

    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("battle:working-grants", args=[self.battle.pk])
        self.detail = reverse("battle:detail", args=[self.battle.pk])

    def granting(
        self, working_key: str = FIRST_TIER, *, but: dict[str, object] | None = None
    ) -> dict[str, object]:
        """The fields of a grant of ``working_key`` to the mage, by its key.

        Args:
            working_key: The working granted.
            but: Fields to send otherwise, or beside these.
        """
        return {
            COMBATANT_FIELD: self.mage.pk,
            WORKING_FIELD: working_key,
            GRANTED_FIELD: True,
            **(but or {}),
        }

    def page(self) -> str:
        """The signed-in viewer's battle page, as HTML."""
        return self.client.get(self.detail).content.decode()

    def panel(self) -> dict | None:
        """The panel as the signed-in viewer's battle page is given it."""
        return self.client.get(self.detail).context[WORKING_GRANTS_CONTEXT]

    def lift_channel_for_the_mage(self) -> None:
        set_magic_bounds_lifted(
            self.battle.pk,
            self.game_master(),
            combatant_id=self.mage.pk,
            bounds=[magic_bounds.CHANNEL],
            lifted=True,
        )


@with_fixed_words
class GrantEndpointTest(GrantControlTestCase):
    """The control's endpoint: a Game Master's forms, and nobody else's."""

    def test_a_game_master_grants_by_effect_and_tier_and_withdraws_by_key(
        self,
    ) -> None:
        self.login_as(self.dm)
        self.assertContains(self.client.get(self.detail), "working_grants.none_granted")
        granted = self.client.post(
            self.url,
            {
                COMBATANT_FIELD: self.mage.pk,
                EFFECT_FIELD: GRANTED_EFFECT.key,
                TIER_FIELD: "2",
                GRANTED_FIELD: "true",
            },
        )
        self.assertRedirects(granted, self.detail)
        # The key recorded is the engine's for the effect and the tier.
        (entry,) = self.recorded()
        self.assertEqual(
            (entry["combatant_id"], entry["working"], entry["by"]),
            (self.mage.pk, SECOND_TIER, self.dm.username),
        )
        page = self.client.get(self.detail)
        # by, name, working: who granted what to whom, and then when.
        self.assertContains(
            page,
            "working_grants.grant_line common.game_master "
            f"{self.dm.username} Mage Figment (tier 2), ",
        )
        self.assertContains(page, WITHDRAW_BUTTON, count=1)
        self.assertContains(page, "working_grants.withdraw_note", count=1)
        self.assertNotContains(page, "working_grants.none_granted")
        self.assertEqual(
            BattleEvent.objects.filter(
                battle=self.battle, payload__granted=True
            ).count(),
            1,
        )
        withdrawn = self.client.post(
            self.url,
            {COMBATANT_FIELD: self.mage.pk, WORKING_FIELD: SECOND_TIER},
        )
        self.assertRedirects(withdrawn, self.detail)
        self.assertEqual(self.recorded(), [])
        page = self.client.get(self.detail)
        self.assertContains(page, "working_grants.none_granted")
        # With nothing to withdraw the note on withdrawing is not drawn.
        self.assertNotContains(page, "working_grants.withdraw_note")

    def test_the_form_posts_back_to_the_page_it_names(self) -> None:
        self.login_as(self.dm)
        response = self.client.post(
            self.url, {**self.granting(), GRANTED_FIELD: "true", "next": "/tarmar/gm/"}
        )
        self.assertRedirects(response, "/tarmar/gm/", fetch_redirect_response=False)

    def test_a_script_posting_the_form_gets_the_battle_payload(self) -> None:
        """The GM chat's Battle tab posts the panel's own form, fields and all."""
        self.login_as(self.dm)
        response = self.client.post(
            self.url,
            {
                COMBATANT_FIELD: self.mage.pk,
                EFFECT_FIELD: GRANTED_EFFECT.key,
                TIER_FIELD: "1",
                GRANTED_FIELD: "true",
                "next": "/tarmar/gm/",
            },
            headers=SCRIPT_CALLER_HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["rulings"]["workings_granted"], self.recorded())
        self.assertEqual(
            [entry["working"] for entry in body["rulings"]["workings_granted"]],
            [FIRST_TIER],
        )
        # The page's panel, rendered from the new record, for battle.js.
        self.assertIn(
            "working_grants.grant_line", body["rulings_html"][WORKING_GRANTS_PANEL_ID]
        )

    def test_the_json_caller_grants_by_key_and_reads_the_record_back(self) -> None:
        self.login_as(self.dm)
        response = self.post_json(self.url, self.granting())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("view", body)
        (entry,) = body["rulings"]["workings_granted"]
        self.assertEqual(
            (entry["combatant_id"], entry["working"]), (self.mage.pk, FIRST_TIER)
        )
        withdrawn = self.post_json(self.url, self.granting(but={GRANTED_FIELD: False}))
        self.assertEqual(withdrawn.json()["rulings"]["workings_granted"], [])

    def test_a_key_sent_is_the_working_whatever_effect_and_tier_go_with_it(
        self,
    ) -> None:
        self.login_as(self.dm)
        response = self.post_json(
            self.url,
            self.granting(SECOND_TIER, but={EFFECT_FIELD: "sear", TIER_FIELD: 1}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([entry["working"] for entry in self.recorded()], [SECOND_TIER])

    def test_the_poll_carries_the_record(self) -> None:
        self.grant()
        self.login_as(self.player)
        poll = self.client.get(reverse("battle:state", args=[self.battle.pk]))
        self.assertEqual(poll.json()["rulings"]["workings_granted"], self.recorded())
        self.assertEqual(len(self.recorded()), 1)

    def test_a_player_is_refused_and_reads_only_the_record(self) -> None:
        self.grant()
        before = self.recorded()
        self.battle.combatants.filter(pk=self.mage.pk).update(player=self.player)
        self.login_as(self.player)
        for label, sent in (
            ("a grant", self.granting(SECOND_TIER)),
            ("a withdrawal", self.granting(but={GRANTED_FIELD: False})),
        ):
            with self.subTest(label):
                response = self.post_json(self.url, sent)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    response.json()["error"], "working_grants.game_master_only"
                )
                form = self.client.post(self.url, {**sent, GRANTED_FIELD: "true"})
                self.assertEqual(form.status_code, 403)
                self.assertEqual(
                    form.context["error"], "working_grants.game_master_only"
                )
                self.assertEqual(self.recorded(), before)
        # The seated player reads the grant, who made it and when, and is
        # given neither form.
        page = self.client.get(self.detail)
        self.assertContains(page, f'id="{WORKING_GRANTS_PANEL_ID}"')
        self.assertContains(
            page,
            "working_grants.grant_line common.game_master "
            f"{self.dm.username} Mage Figment (tier 1), ",
        )
        for control in (
            "working_grants.grant_button",
            "working_grants.withdraw_button",
            "working_grants.withdraw_note",
            self.url,
        ):
            self.assertNotContains(page, control)
        assert (panel := self.panel()) is not None
        self.assertIs(panel["can_rule"], False)

    def test_a_bystander_game_master_is_refused(self) -> None:
        self.login_as(self.create_dm(username="bystander"))
        response = self.post_json(self.url, self.granting())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "working_grants.game_master_only")
        self.assertEqual(self.recorded(), [])
        self.assertNotContains(
            self.client.get(self.detail), "working_grants.grant_button"
        )

    def test_a_post_that_names_no_figure_or_no_working_is_refused(self) -> None:
        self.login_as(self.dm)
        for label, sent, refusal in (
            (
                "no figure",
                self.granting(but={COMBATANT_FIELD: "x"}),
                "working_grants.refuse_combatant",
            ),
            (
                "neither a key nor an effect",
                {COMBATANT_FIELD: self.mage.pk, GRANTED_FIELD: True},
                "working_grants.refuse_working",
            ),
            (
                "an effect the canon does not carry",
                {
                    COMBATANT_FIELD: self.mage.pk,
                    EFFECT_FIELD: "glamour",
                    TIER_FIELD: 1,
                    GRANTED_FIELD: True,
                },
                "working_grants.refuse_working",
            ),
            (
                "a tier that is no number",
                {
                    COMBATANT_FIELD: self.mage.pk,
                    EFFECT_FIELD: GRANTED_EFFECT.key,
                    TIER_FIELD: "two",
                    GRANTED_FIELD: True,
                },
                "working_grants.refuse_working",
            ),
            (
                "a tier below the first",
                {
                    COMBATANT_FIELD: self.mage.pk,
                    EFFECT_FIELD: GRANTED_EFFECT.key,
                    TIER_FIELD: 0,
                    GRANTED_FIELD: True,
                },
                "working_grants.refuse_working",
            ),
            (
                "a negative tier",
                {
                    COMBATANT_FIELD: self.mage.pk,
                    EFFECT_FIELD: GRANTED_EFFECT.key,
                    TIER_FIELD: -1,
                    GRANTED_FIELD: True,
                },
                "working_grants.refuse_working",
            ),
        ):
            with self.subTest(label):
                response = self.post_json(self.url, sent)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], refusal)
                self.assertEqual(self.recorded(), [])
        with self.subTest("a body that is not an object"):
            not_an_object = self.client.post(
                self.url, "[]", content_type="application/json"
            )
            self.assertEqual(not_an_object.status_code, 400)
            # The body is refused before any field of it is read.
            self.assertEqual(not_an_object.json(), {"error": "Invalid JSON"})

    def test_each_refusal_of_the_ruling_is_shown_to_the_game_master(self) -> None:
        """What ``set_working_granted`` refuses, the script reads as the
        error and the form's page carries standing on it."""
        self.login_as(self.dm)
        cases = (
            (
                "a key the rules do not build",
                self.granting("working:glamour:1"),
                "working_grants.refuse_working",
            ),
            (
                "a working that needs no grant",
                self.granting(NEEDS_NO_GRANT),
                # working.
                "working_grants.refuse_needs_no_grant Sear (tier 1)",
            ),
            (
                "a figure not in this battle",
                self.granting(but={COMBATANT_FIELD: 987654}),
                "working_grants.refuse_combatant",
            ),
            (
                "a figure with no level in the True skill",
                self.granting(but={COMBATANT_FIELD: self.fighter.pk}),
                # name, skill.
                f"working_grants.refuse_no_true_level {self.fighter.display_name} "
                "Arcane",
            ),
        )
        for label, sent, refusal in cases:
            with self.subTest(label):
                self.assert_refused(self.url, sent, refusal)
        with self.subTest("a canon with no True Magic"), seated_rules(NO_TRUE_MAGIC):
            self.assert_refused(
                self.url, self.granting(), "working_grants.refuse_no_true_magic"
            )
            self.assert_refused(
                self.url,
                {
                    COMBATANT_FIELD: self.mage.pk,
                    EFFECT_FIELD: GRANTED_EFFECT.key,
                    TIER_FIELD: 1,
                    GRANTED_FIELD: True,
                },
                "working_grants.refuse_no_true_magic",
            )
        with self.subTest("a classic battle"):
            classic = create_battle(
                self.dm, name="Melee", count=2, seed=3, rules_profile=CLASSIC_PROFILE
            )
            self.assert_refused(
                reverse("battle:working-grants", args=[classic.pk]),
                self.granting(but={COMBATANT_FIELD: classic.combatants.first().pk}),
                "working_grants.refuse_classic",
            )
        self.assertEqual(self.recorded(), [])

    def assert_refused(self, url: str, sent: dict, refusal: str) -> None:
        """A grant is refused in ``refusal``'s words, to a script and on the
        page a form post lands on."""
        script = self.post_json(url, sent)
        self.assertEqual(script.status_code, 400)
        self.assertEqual(script.json()["error"], refusal)
        form = self.client.post(url, {**sent, GRANTED_FIELD: "true"})
        self.assertEqual(form.status_code, 400)
        self.assertEqual(form.context["error"], refusal)
        self.assertContains(form, refusal, status_code=400)


@with_fixed_words
class WorkingAskedForTest(DefaultCanonTestCase):
    """The working a post names: its key, or its effect and its tier."""

    def test_an_effect_and_a_tier_are_the_engines_key_for_them(self) -> None:
        self.assertEqual(
            working_grants.working_asked_for(
                {EFFECT_FIELD: GRANTED_EFFECT.key, TIER_FIELD: "2"}
            ),
            SECOND_TIER,
        )

    def test_a_key_sent_is_returned_as_it_was_sent(self) -> None:
        # A recorded grant is withdrawn by the key it holds, built or not.
        self.assertEqual(
            working_grants.working_asked_for({WORKING_FIELD: "working:glamour:9"}),
            "working:glamour:9",
        )

    def test_what_names_no_working_is_refused_in_the_rulings_words(self) -> None:
        for label, fields in (
            ("nothing", {}),
            ("an empty key and no effect", {WORKING_FIELD: ""}),
            ("an unknown effect", {EFFECT_FIELD: "glamour", TIER_FIELD: 1}),
            ("no tier", {EFFECT_FIELD: GRANTED_EFFECT.key}),
            ("a tier of nought", {EFFECT_FIELD: GRANTED_EFFECT.key, TIER_FIELD: 0}),
            ("a negative tier", {EFFECT_FIELD: GRANTED_EFFECT.key, TIER_FIELD: -1}),
            ("a list for a tier", {EFFECT_FIELD: GRANTED_EFFECT.key, TIER_FIELD: [1]}),
        ):
            with self.subTest(label), self.assertRaises(BattleRefusal) as refused:
                working_grants.working_asked_for(fields)
            self.assertEqual(str(refused.exception), "working_grants.refuse_working")
        with seated_rules(NO_TRUE_MAGIC), self.assertRaises(BattleRefusal) as refused:
            working_grants.working_asked_for(
                {EFFECT_FIELD: GRANTED_EFFECT.key, TIER_FIELD: 1}
            )
        self.assertEqual(str(refused.exception), "working_grants.refuse_no_true_magic")

    def test_the_effects_to_grant_are_those_that_need_one(self) -> None:
        self.assertEqual(
            [effect.label for effect in working_grants.grantable_effects()],
            ["Figment"],
        )
        with seated_rules(NO_TRUE_MAGIC):
            self.assertEqual(working_grants.grantable_effects(), ())


@with_fixed_words
class WithdrawalLeavesACraftStandingTest(DefaultCanonTestCase):
    """What the control tells the Game Master of a withdrawal
    (``working_grants.withdraw_note``) is the engine's doing: a craft begun
    under a grant goes on to its end once the grant is withdrawn."""

    def test_a_craft_of_a_working_no_longer_granted_is_carried_on(self) -> None:
        # A turn into Figment, on a battle whose record grants it to nobody.
        state = play.board(
            mage={"crafting": CraftState(play.FIGMENT, 0, False, 1, 0, None)}
        )
        ungranted = Battle()
        menu, magic = menu_and_stage(state, battle=ungranted)
        assert magic is not None
        self.assertEqual(state.by_id(play.MAGE).granted_workings, [])
        self.assertIs(magic["craft"]["standing"]["offered"], True)
        self.assertTrue(
            [option for option in menu if option["spell_key"] == play.FIGMENT]
        )
        # No craft of it may be begun, by this figure or any.
        fresh, _magic = menu_and_stage(play.board(), battle=ungranted)
        self.assertEqual(
            [option for option in fresh if option["spell_key"] == play.FIGMENT], []
        )
        events = play.play(
            state,
            [{play.MAGE: play.craft(play.FIGMENT), play.FOE: play.idle}] * 2,
            battle=ungranted,
        )
        self.assertIsNone(state.by_id(play.MAGE).crafting)
        # The working went off: name, roll and the rest of its line.
        self.assertTrue(
            any(
                event["message"].startswith("true_magic_log.completed ")
                for event in events
            ),
            [event["message"] for event in events],
        )


@with_fixed_words
class GrantPanelTest(GrantControlTestCase):
    """What the panel draws, for the Game Master and for everyone else."""

    def test_the_form_offers_the_true_figures_and_the_effects_that_need_a_grant(
        self,
    ) -> None:
        self.login_as(self.dm)
        assert (panel := self.panel()) is not None
        self.assertIs(panel["can_rule"], True)
        # The mage, and not the fighter, who has no level in the True skill.
        self.assertEqual(panel["figures"], [{"id": self.mage.pk, "name": "Mage"}])
        # Figment, and neither Sear nor Knit, which any True mage crafts.
        self.assertEqual(panel["effects"], [{"key": "figment", "label": "Figment"}])
        self.assertEqual(
            panel["fields"],
            {
                "combatant": "combatant",
                "working": "working",
                "effect": "effect",
                "tier": "tier",
                "granted": "granted",
            },
        )
        (form,) = forms_of_class(self.page(), "working-grant-form")
        self.assertIn(f'<option value="{self.mage.pk}">Mage</option>', form)
        self.assertNotIn(f'<option value="{self.fighter.pk}">', form)
        self.assertIn('<option value="figment">Figment</option>', form)
        self.assertNotIn('value="sear"', form)
        # No floor of the engine's is restated: a tier it cannot build is
        # refused (``working_asked_for``).
        self.assertIn('name="tier" value="1" step="1" required>', form)
        self.assertIn('name="granted" value="true"', form)
        self.assertIn(f'action="{self.url}"', form)
        self.assertIn('name="csrfmiddlewaretoken"', form)
        self.assertIn(f'name="next" value="{self.detail}"', form)
        self.assertIn("working_grants.grant_button", form)

    def test_each_grant_has_its_withdrawal_naming_the_key_the_record_holds(
        self,
    ) -> None:
        self.grant(FIRST_TIER)
        self.grant(SECOND_TIER)
        self.login_as(self.dm)
        assert (panel := self.panel()) is not None
        self.assertEqual(
            [(grant["combatant_id"], grant["working"]) for grant in panel["grants"]],
            [(self.mage.pk, FIRST_TIER), (self.mage.pk, SECOND_TIER)],
        )
        withdrawals = forms_of_class(self.page(), "working-grant-withdraw-form")
        self.assertEqual(len(withdrawals), 2)
        for form, working_key, tier in zip(
            withdrawals, (FIRST_TIER, SECOND_TIER), (1, 2)
        ):
            self.assertIn(f'action="{self.url}"', form)
            self.assertIn('name="csrfmiddlewaretoken"', form)
            self.assertIn(f'name="next" value="{self.detail}"', form)
            self.assertIn(f'name="combatant" value="{self.mage.pk}"', form)
            self.assertIn(f'name="working" value="{working_key}"', form)
            self.assertIn('name="granted" value=""', form)
            # Both buttons read the one word; a screen reader is told each by
            # its own line: button, line (by, name, working).
            self.assertIn(
                'aria-label="common.undo_named working_grants.withdraw_button '
                "working_grants.grant_line common.game_master "
                f'{self.dm.username} Mage Figment (tier {tier})"{WITHDRAW_BUTTON}',
                form,
            )

    def test_a_grant_past_the_tier_cap_says_it_waits_on_channel(self) -> None:
        self.grant(FIRST_TIER)
        self.grant(PAST_THE_CAP)
        self.login_as(self.dm)
        assert (waiting := self.panel()) is not None
        # name, working: the stage's own sentence for the same grant.
        self.assertEqual(
            [grant["waits"] for grant in waiting["grants"]],
            ["", "true_magic.granted_waits Mage Figment (tier 3)"],
        )
        self.assertContains(
            self.client.get(self.detail),
            '<span class="working-grant-waits">'
            "true_magic.granted_waits Mage Figment (tier 3)</span>",
            count=1,
        )
        # Channel lifted for the mage, the same grants wait on nothing, and
        # the panel's record says it is to be drawn again.
        self.lift_channel_for_the_mage()
        assert (lifted := self.panel()) is not None
        self.assertEqual([grant["waits"] for grant in lifted["grants"]], ["", ""])
        self.assertNotContains(self.client.get(self.detail), "working-grant-waits")
        self.assertEqual(
            json.loads(waiting["record"])["workings_granted"],
            json.loads(lifted["record"])["workings_granted"],
        )
        self.assertNotEqual(waiting["record"], lifted["record"])

    def test_the_record_follows_a_figures_new_name(self) -> None:
        self.grant()
        self.login_as(self.dm)
        assert (before := self.panel()) is not None
        self.rename_the_mage()
        assert (after := self.panel()) is not None
        self.assertNotEqual(before["record"], after["record"])
        self.assertEqual(after["figures"], [{"id": self.mage.pk, "name": "Magus"}])
        self.assertIn("Magus", after["grants"][0]["line"])

    def rename_the_mage(self) -> None:
        self.battle.combatants.filter(pk=self.mage.pk).update(display_name="Magus")

    def test_the_record_follows_a_new_name_with_no_grant_on_it(self) -> None:
        """The grant form names the figure, so the panel is drawn again."""
        self.login_as(self.dm)
        assert (before := self.panel()) is not None
        self.assertEqual(before["grants"], [])
        self.rename_the_mage()
        assert (after := self.panel()) is not None
        self.assertNotEqual(before["record"], after["record"])
        self.assertEqual(
            json.loads(after["record"]),
            {
                "workings_granted": [],
                "lines": [],
                "figures": [{"id": self.mage.pk, "name": "Magus"}],
            },
        )

    def test_the_record_follows_a_new_name_where_no_form_is_offered(self) -> None:
        """The grant's line names the figure, so the panel is drawn again."""
        self.grant()
        self.login_as(self.dm)
        with seated_rules(NO_EFFECT_NEEDS_A_GRANT):
            assert (before := self.panel()) is not None
            self.rename_the_mage()
            assert (after := self.panel()) is not None
        self.assertEqual((before["figures"], after["figures"]), ([], []))
        self.assertNotEqual(before["record"], after["record"])
        self.assertEqual(
            json.loads(after["record"])["lines"],
            [
                [
                    # by, name, working; and the grant waits on nothing.
                    "working_grants.grant_line common.game_master "
                    f"{self.dm.username} Magus Figment (tier 1)",
                    "",
                ]
            ],
        )

    def test_a_battle_with_nothing_to_grant_shows_no_panel(self) -> None:
        self.login_as(self.dm)
        with self.subTest("a classic battle"):
            classic = create_battle(
                self.dm, name="Melee", count=2, seed=3, rules_profile=CLASSIC_PROFILE
            )
            page = self.client.get(reverse("battle:detail", args=[classic.pk]))
            self.assertIsNone(page.context[WORKING_GRANTS_CONTEXT])
            self.assertNotContains(page, WORKING_GRANTS_PANEL_ID)
        with self.subTest("no figure who crafts workings"):
            fighters = create_battle(self.dm, name="Pit", count=2, seed=3)
            page = self.client.get(reverse("battle:detail", args=[fighters.pk]))
            self.assertIsNone(page.context[WORKING_GRANTS_CONTEXT])
            self.assertNotContains(page, WORKING_GRANTS_PANEL_ID)
        with self.subTest("a canon with no True Magic"), seated_rules(NO_TRUE_MAGIC):
            self.assertIsNone(self.panel())
        with (
            self.subTest("a canon none of whose workings needs a grant"),
            seated_rules(NO_EFFECT_NEEDS_A_GRANT),
        ):
            self.assertIsNone(self.panel())

    def test_a_grant_the_canon_no_longer_builds_is_shown_and_may_be_withdrawn(
        self,
    ) -> None:
        self.grant()
        self.login_as(self.dm)
        with seated_rules(NO_TRUE_MAGIC):
            assert (panel := self.panel()) is not None
            (grant,) = panel["grants"]
            # by, name, working; the working by the words for one unbuilt.
            self.assertEqual(
                grant["line"],
                "working_grants.grant_line common.game_master "
                f"{self.dm.username} Mage working_grants.unbuilt_working {FIRST_TIER}",
            )
            self.assertEqual(grant["waits"], "")
            # Nobody crafts a working under this canon: no grant form.
            self.assertEqual(panel["figures"], [])
            page = self.client.get(self.detail)
            self.assertNotContains(page, "working_grants.grant_button")
            self.assertContains(page, WITHDRAW_BUTTON, count=1)
            withdrawn = self.client.post(
                self.url, {COMBATANT_FIELD: self.mage.pk, WORKING_FIELD: FIRST_TIER}
            )
            self.assertRedirects(withdrawn, self.detail)
        self.assertEqual(self.recorded(), [])

    def test_a_grant_of_what_no_longer_needs_one_is_shown_with_no_grant_form(
        self,
    ) -> None:
        """Under a canon with True Magic and nothing left to grant, the mage
        still crafts workings and is offered for none."""
        self.grant()
        self.login_as(self.dm)
        with seated_rules(NO_EFFECT_NEEDS_A_GRANT):
            assert (panel := self.panel()) is not None
            self.assertEqual((panel["figures"], panel["effects"]), ([], []))
            (grant,) = panel["grants"]
            # by, name, working: the canon still builds the working.
            self.assertEqual(
                grant["line"],
                "working_grants.grant_line common.game_master "
                f"{self.dm.username} Mage Figment (tier 1)",
            )
            page = self.page()
            self.assertEqual(forms_of_class(page, "working-grant-form"), [])
            self.assertNotIn("working_grants.grant_button", page)
            (withdrawal,) = forms_of_class(page, "working-grant-withdraw-form")
            self.assertIn(f'name="working" value="{FIRST_TIER}"', withdrawal)
            withdrawn = self.client.post(
                self.url, {COMBATANT_FIELD: self.mage.pk, WORKING_FIELD: FIRST_TIER}
            )
            self.assertRedirects(withdrawn, self.detail)
        self.assertEqual(self.recorded(), [])


@with_fixed_words
class GrantOfAFigureTheEngineDoesNotReadTest(GrantControlTestCase):
    """A record that names a figure the engine no longer reads: the panel
    draws the grant, and asks the engine nothing of nobody.

    A seat given back to the lobby loses its grants
    (``battle.services.release_seat``), so only a record that outlived its
    figure some other way holds one; the page must still be drawn."""

    def test_the_grant_is_drawn_and_waits_on_nothing(self) -> None:
        # Past the mage's tier cap: while the board holds the mage, it waits.
        self.grant(PAST_THE_CAP)
        self.login_as(self.dm)
        assert (on_the_board := self.panel()) is not None
        self.assertEqual(
            [grant["waits"] for grant in on_the_board["grants"]],
            ["true_magic.granted_waits Mage Figment (tier 3)"],
        )
        # The mage's figure gone from the board, the record still naming it.
        battle = Battle.objects.get(pk=self.battle.pk)
        battle.state_json["combatants"] = [
            figure
            for figure in battle.state_json["combatants"]
            if figure["combatant_id"] != self.mage.pk
        ]
        battle.save(update_fields=["state_json"])
        self.assertNotIn(self.mage.pk, working_grants.engine_figures(battle))
        assert (panel := working_grants_panel(battle, self.game_master())) is not None
        (grant,) = panel["grants"]
        self.assertEqual(
            (grant["combatant_id"], grant["working"], grant["waits"]),
            (self.mage.pk, PAST_THE_CAP, ""),
        )
        # Nobody the engine reads crafts workings: no figure is offered.
        self.assertEqual(panel["figures"], [])
        # The page is drawn, and the poll carries the same panel.
        poll = self.client.get(reverse("battle:state", args=[self.battle.pk]))
        for drawn in (
            self.page(),
            poll.json()["rulings_html"][WORKING_GRANTS_PANEL_ID],
        ):
            self.assertIn(
                "working_grants.grant_line common.game_master "
                f"{self.dm.username} Mage Figment (tier 3), ",
                drawn,
            )
            self.assertNotIn("true_magic.granted_waits", drawn)


@with_fixed_words
class GmChatBattleTabTest(BaseTestCase):
    """The GM chat's Battle tab carries the grant control, and a form posted
    from it lands back in the chat."""

    def setUp(self) -> None:
        super().setUp()
        from gm.fight import start_fight
        from gm.models import GMSession

        self.enterContext(seated_rules(DEFAULT_MAGIC_RULES))
        self.game_master = self.create_dm(username="session-gm")
        self.login_as(self.game_master)
        self.session = GMSession.objects.create(name="Road", user=self.game_master)
        self.session.characters.add(true_mage(self.game_master))
        self.battle = start_fight(self.session, foes_text="gray wolf", seed=11).battle
        self.chat = f"/tarmar/gm/?session={self.session.pk}"

    def test_the_tab_shows_the_control_and_a_post_returns_to_the_chat(self) -> None:
        page = self.client.get(self.chat)
        self.assertTrue(page.context[WORKING_GRANTS_CONTEXT]["can_rule"])
        self.assertContains(page, f'id="{WORKING_GRANTS_PANEL_ID}"')
        mage = self.battle.combatants.get(display_name="Mage")
        response = self.client.post(
            reverse("battle:working-grants", args=[self.battle.pk]),
            {
                COMBATANT_FIELD: mage.pk,
                EFFECT_FIELD: GRANTED_EFFECT.key,
                TIER_FIELD: "1",
                GRANTED_FIELD: "true",
                "next": self.chat,
            },
        )
        self.assertRedirects(response, self.chat, fetch_redirect_response=False)
        self.assertContains(
            self.client.get(self.chat),
            "working_grants.grant_line common.game_master session-gm Mage "
            "Figment (tier 1), ",
        )


@with_fixed_words
class ThePageCarriesTheCraftTest(BaseTestCase):
    """The battle page hands battle.js a True mage's stage and a standing
    craft: the blocks the arena partial renders, read back off the page."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(DEFAULT_MAGIC_RULES))
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle, self.seat = play.awaiting_true_mage(self.dm, self.player)
        self.detail = reverse("battle:detail", args=[self.battle.pk])
        self.login_as(self.player)

    def page(self) -> str:
        """The signed-in viewer's battle page, as HTML."""
        return self.client.get(self.detail).content.decode()

    def test_the_page_draws_from_the_true_casts_and_the_workings(self) -> None:
        pending = json_block(self.page(), "battle-pending")
        magic = pending["magic"]
        self.assertTrue(magic["true_casts"])
        self.assertTrue(all(row["true_cast"] for row in magic["true_casts"]))
        craft = magic["craft"]
        self.assertEqual((craft["skill"], craft["tier_cap"]), ("Arcane", 4))
        self.assertIsNone(craft["standing"])
        self.assertEqual(
            sorted({row["effect_label"] for row in craft["workings"]}),
            ["Knit", "Sear"],
        )
        # The words the page fills these with are on it once.
        panel_words = json_block(self.page(), "battle-turn-panel-words")
        self.assertEqual(panel_words["craft_tier"], "turn_panel.craft_tier {tier}")
        self.assertEqual(
            panel_words["craft_withheld"], "turn_panel.craft_withheld {line}"
        )

    def test_a_craft_begun_through_the_page_stands_on_the_next_page(self) -> None:
        pending = json_block(self.page(), "battle-pending")
        begun = next(
            option
            for option in pending["candidates"]
            if option["spell_key"] == play.SEAR_TWO and not option.get("push_mana")
        )
        acted = self.post_json(
            reverse("battle:act", args=[self.battle.pk]),
            {
                "combatant_id": pending["combatant_id"],
                "turn": pending["turn"],
                "letter": begun["letter"],
                "target_id": begun["target_id"],
                "spell_key": begun["spell_key"],
            },
        )
        self.assertEqual(acted.status_code, 200)
        self.login_as(self.dm)
        self.client.post(reverse("battle:advance", args=[self.battle.pk]))
        self.assertEqual(
            Battle.objects.get(pk=self.battle.pk).status, Battle.STATUS_AWAITING
        )
        self.login_as(self.player)
        page = self.page()
        standing = json_block(page, "battle-pending")["magic"]["craft"]["standing"]
        self.assertEqual(
            (
                standing["key"],
                standing["turn"],
                standing["turns"],
                standing["target_id"],
                standing["offered"],
            ),
            (play.SEAR_TWO, 2, 6, begun["target_id"], True),
        )
        # turn, turns, working.
        self.assertEqual(
            standing["line"], "true_magic.craft_continue 2 6 Sear (tier 2)"
        )
        (card,) = [
            figure["crafting"]
            for figure in json_block(page, "battle-view")["combatants"]
            if figure["combatant_id"] == self.seat.pk
        ]
        self.assertEqual(card["spell_key"], play.SEAR_TWO)
        self.assertTrue(card["line"].startswith("true_magic.card_naming 1 "))
