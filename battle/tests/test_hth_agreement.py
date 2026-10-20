"""HTH by agreement (#867), on tarmar-engine v0.9.10.

Spencer's ruling of 2026-10-09: "a Game Master marks a battle, or a pair, as
'HTH by agreement'; players alone still need the engine's entry condition."
Who marks it is the fight's runner alone, while a Game Master
(``runs_the_seats``): coordinator's ruling under the standing rule; Spencer
may overrule.
"""

from django.urls import reverse

from battle import agreement
from battle.access import Actor
from battle.admin import BattleAdmin
from battle.engine.state import BattleState
from battle.models import Battle, BattleEvent
from battle.policy import choose_option
from battle.services import advance_turn, create_battle, set_hth_agreement
from battle.wording import game_master_name, words
from common.test_helpers import BaseTestCase

from .rulings_words import with_fixed_words
from .test_lobby import started_battle_with_player
from .test_turn_menu import figure


@with_fixed_words
class AgreementRecordTest(BaseTestCase):
    """The ruling as the battle stores it, and as the engine is given it."""

    def test_a_marked_pair_lists_each_figure_for_the_other(self):
        state = BattleState(
            combatants=[
                figure(1, "Aldwin", (0, 0)),
                figure(2, "Brin", (1, 0)),
                figure(3, "Cael", (0, 1)),
            ]
        )
        agreement.fill(state, agreement.Agreement(pairs=frozenset({(1, 2)})))
        self.assertEqual(
            [figure.hth_agreed_with for figure in state.combatants], [[2], [1], []]
        )
        # Unmarked on the next turn: the record overwrites the state.
        agreement.fill(state, agreement.Agreement())
        self.assertEqual(
            [figure.hth_agreed_with for figure in state.combatants], [[], [], []]
        )

    def test_a_marked_battle_lists_every_enemy_and_no_teammate(self):
        state = BattleState(
            combatants=[
                figure(1, "Aldwin", (0, 0), team="Red"),
                figure(2, "Brin", (1, 0), team="Red"),
                figure(3, "Cael", (0, 1), team="Blue"),
            ]
        )
        agreement.fill(state, agreement.Agreement(whole_battle=True))
        self.assertEqual(
            [figure.hth_agreed_with for figure in state.combatants], [[3], [3], [1, 2]]
        )

    def test_the_stored_shape_round_trips(self):
        record = agreement.Agreement(whole_battle=False, pairs=frozenset({(2, 5)}))
        self.assertEqual(record.to_json(), {"whole_battle": False, "pairs": [[2, 5]]})
        self.assertEqual(agreement.Agreement().to_json(), {})
        self.assertEqual(agreement.pair_key(5, 2), (2, 5))

    def test_the_words_for_the_page_and_the_log(self):
        names = {1: "Aldwin", 2: "Brin"}
        self.assertEqual(agreement.describe(agreement.Agreement(), names), "")
        self.assertEqual(
            agreement.describe(agreement.Agreement(whole_battle=True), names),
            "hth_agreement.whole_battle_line",
        )
        self.assertEqual(
            agreement.describe(agreement.Agreement(pairs=frozenset({(1, 2)})), names),
            "hth_agreement.pairs_line hth_agreement.pair_names Aldwin Brin",
        )
        lifted = agreement.change_message(
            whole_battle=False, pair=None, agreed=False, names=names, by="GM"
        )
        self.assertEqual(lifted, "hth_agreement.log_battle_lifted GM")
        self.assertEqual(
            agreement.change_message(
                whole_battle=None, pair=(1, 2), agreed=True, names=names, by="GM"
            ),
            "hth_agreement.log_pair_marked GM hth_agreement.pair_names Aldwin Brin",
        )
        with self.assertRaisesMessage(ValueError, "whole battle or a pair"):
            agreement.change_message(
                whole_battle=None, pair=None, agreed=True, names=names, by="GM"
            )

    def test_the_menu_offers_a_way_in_only_to_a_marked_pair(self):
        """Players alone still need the engine's entry condition."""
        chooser = figure(1, "Aldwin", (0, 0))
        state = BattleState(combatants=[chooser, figure(2, "Brin", (1, 0))])
        letters = [c.letter for c in choose_option(state, chooser).candidates]
        self.assertNotIn("o", letters)
        agreement.fill(state, agreement.Agreement(pairs=frozenset({(1, 2)})))
        letters = [c.letter for c in choose_option(state, chooser).candidates]
        self.assertIn("o", letters)


@with_fixed_words
class SetAgreementTest(BaseTestCase):
    """Who may set the ruling, and what each change leaves behind."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle = create_battle(self.dm, name="Pit", count=3, seed=5)
        self.ids = sorted(self.battle.combatants.values_list("pk", flat=True))

    def game_master(self):
        return Actor(user=self.dm, guest_key="")

    def test_a_game_master_marks_a_pair_and_the_log_says_so(self):
        first, second = self.ids[:2]
        battle, event = set_hth_agreement(
            self.battle.pk, self.game_master(), agreed=True, pair=(second, first)
        )
        self.assertEqual(
            battle.hth_agreement_json,
            {"whole_battle": False, "pairs": [[first, second]]},
        )
        self.assertIsNotNone(event)
        assert event is not None
        names = {row.pk: row.display_name for row in battle.combatants.all()}
        self.assertEqual(
            event.message,
            agreement.change_message(
                whole_battle=None,
                pair=(first, second),
                agreed=True,
                names=names,
                by=game_master_name(self.dm.username),
            ),
        )
        self.assertTrue(event.message.startswith("hth_agreement.log_pair_marked"))
        self.assertIn(f"common.game_master {self.dm.username}", event.message)
        self.assertEqual(event.payload["pair"], [first, second])
        # The same change again changes nothing and logs nothing.
        _battle, again = set_hth_agreement(
            self.battle.pk, self.game_master(), agreed=True, pair=(first, second)
        )
        self.assertIsNone(again)
        battle, lifted = set_hth_agreement(
            self.battle.pk, self.game_master(), agreed=False, pair=(first, second)
        )
        self.assertEqual(battle.hth_agreement_json, {})
        assert lifted is not None
        self.assertTrue(lifted.message.startswith("hth_agreement.log_pair_lifted"))

    def test_a_game_master_marks_the_whole_battle(self):
        battle, event = set_hth_agreement(
            self.battle.pk, self.game_master(), agreed=True, whole_battle=True
        )
        self.assertTrue(battle.hth_agreement_json["whole_battle"])
        assert event is not None
        self.assertEqual(
            event.message,
            f"hth_agreement.log_battle_marked common.game_master {self.dm.username}",
        )

    def test_only_a_game_master_sets_it(self):
        # The battle's own maker is not enough, and neither is a player.
        maker = self.create_player(username="maker")
        own = create_battle(maker, name="Mine", count=2, seed=3)
        for who, actor, battle in (
            ("maker", Actor(user=maker, guest_key=""), own),
            ("player", Actor(user=self.player, guest_key=""), self.battle),
            ("guest", Actor(user=None, guest_key="guest-key"), self.battle),
        ):
            with (
                self.subTest(who=who),
                self.assertRaisesMessage(
                    ValueError, words().hth_agreement.game_master_only
                ),
            ):
                set_hth_agreement(battle.pk, actor, agreed=True, whole_battle=True)
        self.assertEqual(Battle.objects.get(pk=own.pk).hth_agreement_json, {})

    def test_a_bystander_game_master_is_refused(self):
        """The fight's runner alone sets it (coordinator's ruling under the
        standing rule; Spencer may overrule): another Game Master is a
        spectator here, as at a seat hand-off."""
        bystander = self.create_dm(username="bystander")
        with self.assertRaisesMessage(
            ValueError, words().hth_agreement.game_master_only
        ):
            set_hth_agreement(
                self.battle.pk,
                Actor(user=bystander, guest_key=""),
                agreed=True,
                whole_battle=True,
            )
        self.assertEqual(Battle.objects.get(pk=self.battle.pk).hth_agreement_json, {})

    def test_a_players_practice_battle_cannot_be_marked(self):
        """A player's own battle has no Game Master running it: not the
        player, and not a Game Master looking on."""
        practice = create_battle(self.player, name="Practice", count=2, seed=3)
        for who, user in (("the player", self.player), ("a Game Master", self.dm)):
            with (
                self.subTest(who=who),
                self.assertRaisesMessage(
                    ValueError, words().hth_agreement.game_master_only
                ),
            ):
                set_hth_agreement(
                    practice.pk,
                    Actor(user=user, guest_key=""),
                    agreed=True,
                    whole_battle=True,
                )

    def test_a_pair_must_be_two_enemies_in_this_battle(self):
        first, second, third = self.ids
        for pair, message in (
            ((first, first), "hth_agreement.refuse_pair"),
            ((first, 999_999), "hth_agreement.refuse_pair"),
        ):
            with self.subTest(pair=pair), self.assertRaisesMessage(ValueError, message):
                set_hth_agreement(
                    self.battle.pk, self.game_master(), agreed=True, pair=pair
                )
        self.battle.combatants.filter(pk__in=[first, second]).update(team="Red")
        with self.assertRaisesMessage(ValueError, "hth_agreement.refuse_same_side"):
            set_hth_agreement(
                self.battle.pk, self.game_master(), agreed=True, pair=(first, second)
            )
        with self.assertRaisesMessage(ValueError, "hth_agreement.refuse_scope"):
            set_hth_agreement(
                self.battle.pk,
                self.game_master(),
                agreed=True,
                whole_battle=True,
                pair=(first, third),
            )

    def test_a_classic_battle_has_no_such_ruling(self):
        classic = create_battle(
            self.dm, name="Melee", count=2, seed=3, rules_profile="classic-melee"
        )
        with self.assertRaisesMessage(ValueError, words().hth_agreement.refuse_classic):
            set_hth_agreement(
                classic.pk, self.game_master(), agreed=True, whole_battle=True
            )

    def test_the_engine_is_given_the_ruling_every_turn(self):
        set_hth_agreement(
            self.battle.pk, self.game_master(), agreed=True, whole_battle=True
        )
        battle, _events = advance_turn(self.battle.pk)
        lists = {
            entry["combatant_id"]: entry.get("hth_agreed_with")
            for entry in battle.state_json["combatants"]
        }
        for combatant_id, partners in lists.items():
            self.assertEqual(
                partners, [other for other in self.ids if other != combatant_id]
            )

    def test_a_change_while_a_move_is_awaited_clears_the_pause(self):
        battle, _seat = started_battle_with_player(self)
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        battle, _event = set_hth_agreement(
            battle.pk, self.game_master(), agreed=True, whole_battle=True
        )
        self.assertEqual(battle.status, Battle.STATUS_PAUSED)
        self.assertEqual(battle.pending_input_json, {})

    def test_the_admin_cannot_change_it(self):
        self.assertIn("hth_agreement_json", BattleAdmin.readonly_fields)


@with_fixed_words
class AgreementEndpointTest(BaseTestCase):
    """The page's control: a Game Master's forms, everyone else the line."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(self.dm, name="Pit", count=2, seed=5)
        self.ids = sorted(self.battle.combatants.values_list("pk", flat=True))
        self.url = reverse("battle:hth-agreement", args=[self.battle.pk])

    def test_a_game_master_posts_the_form_and_the_page_shows_it(self):
        self.login_as(self.dm)
        response = self.client.post(
            self.url,
            {
                "scope": "pair",
                "first": self.ids[0],
                "second": self.ids[1],
                "agreed": "true",
            },
        )
        self.assertRedirects(response, reverse("battle:detail", args=[self.battle.pk]))
        page = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertContains(page, "hth_agreement.pairs_line")
        self.assertContains(page, words().hth_agreement.mark_whole_battle)
        self.assertEqual(
            BattleEvent.objects.filter(
                battle=self.battle, payload__scope="pair"
            ).count(),
            1,
        )

    def test_the_json_caller_gets_the_battle_payload(self):
        self.login_as(self.dm)
        response = self.post_json(self.url, {"scope": "battle", "agreed": True})
        self.assertEqual(response.status_code, 200)
        self.assertIn("view", response.json())
        # The caller reads back what it changed, and the page's panel comes
        # rendered from the new record (battle.js puts it in place).
        self.assertTrue(response.json()["rulings"]["hth_agreement"]["whole_battle"])
        self.assertIn(
            words().hth_agreement.whole_battle_line,
            response.json()["rulings_html"]["battle-hth-agreement"],
        )
        bad = self.post_json(self.url, {"scope": "everyone", "agreed": True})
        self.assertEqual(bad.status_code, 400)
        no_pair = self.post_json(self.url, {"scope": "pair", "first": "x"})
        self.assertEqual(no_pair.status_code, 400)
        not_an_object = self.client.post(
            self.url, "[]", content_type="application/json"
        )
        self.assertEqual(not_an_object.status_code, 400)
        # The service's own refusal comes back in the caller's shape.
        same_figure = self.post_json(
            self.url,
            {"scope": "pair", "first": self.ids[0], "second": self.ids[0]},
        )
        self.assertEqual(same_figure.status_code, 400)
        self.assertEqual(same_figure.json()["error"], words().hth_agreement.refuse_pair)

    def test_a_bystander_game_master_reads_only_the_line(self):
        bystander = self.create_dm(username="bystander")
        self.login_as(bystander)
        response = self.post_json(self.url, {"scope": "battle", "agreed": True})
        self.assertEqual(response.status_code, 403)
        page = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertContains(page, words().hth_agreement.none_marked)
        self.assertNotContains(page, words().hth_agreement.mark_whole_battle)

    def test_a_form_post_lands_back_where_it_came_from(self):
        self.login_as(self.dm)
        response = self.client.post(
            self.url,
            {"scope": "battle", "agreed": "true", "next": "/tarmar/gm/?session=7"},
        )
        self.assertRedirects(
            response, "/tarmar/gm/?session=7", fetch_redirect_response=False
        )
        elsewhere = self.client.post(
            self.url,
            {"scope": "battle", "agreed": "", "next": "https://example.com/"},
        )
        self.assertRedirects(elsewhere, reverse("battle:detail", args=[self.battle.pk]))

    def test_anyone_else_is_refused_and_reads_only_the_line(self):
        player = self.create_player()
        self.battle.combatants.filter(pk=self.ids[0]).update(player=player)
        self.login_as(player)
        response = self.post_json(self.url, {"scope": "battle", "agreed": True})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["error"], words().hth_agreement.game_master_only
        )
        page = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertContains(page, words().hth_agreement.none_marked)
        self.assertNotContains(page, words().hth_agreement.mark_whole_battle)
        self.assertEqual(Battle.objects.get(pk=self.battle.pk).hth_agreement_json, {})

    def test_a_classic_battle_shows_no_panel(self):
        classic = create_battle(
            self.dm, name="Melee", count=2, seed=3, rules_profile="classic-melee"
        )
        self.login_as(self.dm)
        page = self.client.get(reverse("battle:detail", args=[classic.pk]))
        self.assertNotContains(page, "battle-hth-agreement")


@with_fixed_words
class GmChatBattleTabTest(BaseTestCase):
    """The GM chat's Battle tab carries the ruling's panel (#867): session
    fights are where it is wanted. Its context is arena_context's, and a
    form post lands back in the chat, re-rendered from the record."""

    def setUp(self):
        super().setUp()
        from common.test_helpers import create_character
        from gm.fight import start_fight
        from gm.models import GMSession

        self.game_master = self.create_dm(username="session-gm")
        self.login_as(self.game_master)
        self.session = GMSession.objects.create(name="Road", user=self.game_master)
        self.session.characters.add(create_character(self.game_master, name="Aldwin"))
        self.battle = start_fight(self.session, foes_text="gray wolf", seed=11).battle
        self.chat = f"/tarmar/gm/?session={self.session.pk}"

    def test_the_tab_shows_the_panel_and_a_post_returns_to_the_chat(self):
        page = self.client.get(self.chat)
        self.assertIsNotNone(page.context["hth_agreement"])
        self.assertTrue(page.context["hth_agreement"]["can_rule"])
        self.assertContains(page, 'id="battle-hth-agreement"')
        self.assertContains(page, f'name="next" value="{self.chat}"')
        response = self.client.post(
            reverse("battle:hth-agreement", args=[self.battle.pk]),
            {"scope": "battle", "agreed": "true", "next": self.chat},
        )
        self.assertRedirects(response, self.chat, fetch_redirect_response=False)
        page = self.client.get(self.chat)
        self.assertContains(page, words().hth_agreement.whole_battle_line)
