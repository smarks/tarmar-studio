"""A Game Master lifts a figure's magic bounds, as a ruling on the battle (#37).

Spencer's ruling of 2026-10-14: the Game Master records "Channel lifted for
<figure>", and likewise the push cap and the counter's commitment bounds,
each separately or together, as a recorded ruling on the battle, with who and
when. Who records it is the HTH-by-agreement ruling's answer (#867): the
fight's runner, while a Game Master (``runs_the_seats``). Only a bound the
loaded canon imposes is offered or may be lifted.

The tests pass under either canon: those that need every bound imposed, or one
missing, inject the canon's rules with that change (:func:`rules_imposing`);
the rest read whatever the loaded canon imposes (``offered_bounds``).
"""

from dataclasses import replace
from datetime import datetime

from django.urls import reverse
from django.utils.html import escape
from django.utils.text import capfirst
from tarmar_engine.magic import ChannelDerivation, ChannelRules, MagicRules

from battle import magic_bounds
from battle.access import Actor
from battle.admin import BattleAdmin
from battle.magic_rules import load_magic_rules, magic_rules
from battle.models import Battle, BattleEvent
from battle.services import (
    advance_turn,
    create_battle,
    restart_battle,
    set_magic_bounds_lifted,
)
from battle.views import MAGIC_BOUNDS_PANEL_ID
from battle.wording import game_master_name, words
from common.test_helpers import BaseTestCase
from reference.paths import DEFAULT_CANON_DIR

from .magic_fixtures import seated_rules
from .rulings_words import with_fixed_words
from .test_lobby import started_battle_with_player

CHANNEL = magic_bounds.CHANNEL
PUSH_CAP = magic_bounds.PUSH_CAP
COUNTER_BOUNDS = magic_bounds.COUNTER_BOUNDS
COMBATANT_FIELD = magic_bounds.COMBATANT_FIELD
BOUNDS_FIELD = magic_bounds.BOUNDS_FIELD
LIFTED_FIELD = magic_bounds.LIFTED_FIELD


def rules_imposing(*bounds: str) -> MagicRules:
    """The loaded canon's magic rules, changed to impose exactly ``bounds``."""
    rules = magic_rules()
    assert rules.push is not None, "both canons carry Push"
    assert rules.counterspell is not None, "both canons carry Counterspell"
    channel = rules.channel or ChannelRules(
        derivations={
            "test": ChannelDerivation(source_skill=None, multiplier=1, offset=0)
        },
        default_kind="test",
    )
    changed = replace(
        rules,
        channel=channel if CHANNEL in bounds else None,
        push=replace(rules.push, max_push_mana=3 if PUSH_CAP in bounds else None),
        counterspell=rules.counterspell if COUNTER_BOUNDS in bounds else None,
        # The engine refuses uncapped Borrowing with no Channel to bound it.
        borrowing=rules.borrowing if CHANNEL in bounds else None,
    )
    assert magic_bounds.bounds_imposed_by(changed) == bounds
    return changed


class CanonImposesTestCase(BaseTestCase):
    """A test case under a canon that imposes ``imposed`` while each test runs."""

    imposed: tuple[str, ...] = magic_bounds.LIFTABLE_BOUNDS

    def setUp(self):
        super().setUp()
        self.enterContext(seated_rules(rules_imposing(*self.imposed)))


class BoundsTheCanonImposesTest(BaseTestCase):
    """Which bounds there are to lift is the loaded canon's answer."""

    def test_each_bound_is_imposed_only_when_the_rules_carry_it(self):
        default_canon = load_magic_rules(DEFAULT_CANON_DIR / "magic-rules.json")
        # The default canon has no Channel, and caps Push.
        self.assertEqual(
            magic_bounds.bounds_imposed_by(default_canon), (PUSH_CAP, COUNTER_BOUNDS)
        )
        assert default_canon.push is not None
        uncapped = replace(
            default_canon, push=replace(default_canon.push, max_push_mana=None)
        )
        # The Tarmar canon's Push has no cap, so there is no push cap to lift.
        self.assertEqual(magic_bounds.bounds_imposed_by(uncapped), (COUNTER_BOUNDS,))
        self.assertEqual(
            magic_bounds.bounds_imposed_by(
                replace(uncapped, push=None, counterspell=None)
            ),
            (),
        )
        # A counter that must commit nothing and may commit the whole purse is
        # bounded by nothing; either end alone is a bound.
        assert default_canon.counterspell is not None
        for least, most, bounded in ((0, None, False), (1, None, True), (0, 5, True)):
            counters = replace(
                default_canon,
                counterspell=replace(
                    default_canon.counterspell,
                    defender_min_mana=least,
                    defender_max_mana=most,
                ),
            )
            with self.subTest(least=least, most=most):
                self.assertEqual(
                    COUNTER_BOUNDS in magic_bounds.bounds_imposed_by(counters), bounded
                )
        for bounds in ((CHANNEL,), (CHANNEL, PUSH_CAP, COUNTER_BOUNDS)):
            with self.subTest(bounds=bounds):
                self.assertEqual(
                    magic_bounds.bounds_imposed_by(rules_imposing(*bounds)), bounds
                )

    def test_the_loaded_canon_decides_what_is_offered(self):
        self.assertEqual(
            magic_bounds.offered_bounds(), magic_bounds.bounds_imposed_by(magic_rules())
        )


@with_fixed_words
class LiftedBoundsRecordTest(CanonImposesTestCase):
    """The ruling as the battle stores it, and the words it is shown in."""

    def test_the_stored_shape_round_trips_in_bound_order(self):
        record = (
            magic_bounds.LiftedBounds()
            .lifted(5, [PUSH_CAP], by="gm", by_id=1, at="2026-10-18T10:00:00+00:00")
            .lifted(2, [CHANNEL], by="gm", by_id=1, at="2026-10-18T10:01:00+00:00")
            .lifted(5, [CHANNEL], by="gm", by_id=1, at="2026-10-18T10:02:00+00:00")
        )
        stored = record.to_json()
        self.assertEqual(
            [(entry["combatant_id"], entry["bound"]) for entry in stored],
            [(2, CHANNEL), (5, CHANNEL), (5, PUSH_CAP)],
        )
        battle = Battle(magic_bounds_lifted_json=stored)
        self.assertEqual(magic_bounds.LiftedBounds.of(battle).to_json(), stored)
        self.assertEqual(
            magic_bounds.LiftedBounds.of(battle).for_figure(5),
            frozenset({CHANNEL, PUSH_CAP}),
        )
        self.assertEqual(magic_bounds.LiftedBounds().to_json(), [])

    def test_lifting_again_keeps_the_first_record_and_holding_removes_it(self):
        first = magic_bounds.LiftedBounds().lifted(
            3, [CHANNEL], by="first", by_id=1, at="2026-10-18T10:00:00+00:00"
        )
        again = first.lifted(
            3, [CHANNEL], by="second", by_id=2, at="2026-10-18T11:00:00+00:00"
        )
        self.assertEqual(again, first)
        held = again.restored(3, [CHANNEL, PUSH_CAP])
        self.assertEqual(held.for_figure(3), frozenset())

    def test_the_words_come_from_the_configuration(self):
        bounds_words = words().magic_bounds
        self.assertEqual(magic_bounds.bound_words([CHANNEL]), bounds_words.channel)
        self.assertEqual(
            magic_bounds.bound_words([COUNTER_BOUNDS, CHANNEL, PUSH_CAP]),
            "magic_bounds.channel, magic_bounds.push_cap common.and_word "
            "magic_bounds.counter_bounds",
        )
        self.assertEqual(
            magic_bounds.change_message(
                bounds=[PUSH_CAP, CHANNEL], lifted=True, name="Aldwin", by="GM"
            ),
            "magic_bounds.log_lifted magic_bounds.channel common.and_word "
            "magic_bounds.push_cap GM Aldwin",
        )
        self.assertEqual(
            magic_bounds.change_message(
                bounds=[CHANNEL], lifted=False, name="Aldwin", by="GM"
            ),
            "magic_bounds.log_held magic_bounds.channel GM Aldwin",
        )
        self.assertIn(
            magic_bounds.bound_words(magic_bounds.LIFTABLE_BOUNDS),
            magic_bounds.none_lifted_line(),
        )


@with_fixed_words
class SetMagicBoundsTest(CanonImposesTestCase):
    """Who may record the ruling, and what each change leaves behind."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle = create_battle(self.dm, name="Pit", count=3, seed=5)
        self.ids = sorted(self.battle.combatants.values_list("pk", flat=True))

    def game_master(self) -> Actor:
        return Actor(user=self.dm, guest_key="")

    def lift(self, battle=None, actor=None, *, bounds=(CHANNEL,), lifted=True):
        return set_magic_bounds_lifted(
            (battle or self.battle).pk,
            actor or self.game_master(),
            combatant_id=self.ids[0],
            bounds=list(bounds),
            lifted=lifted,
        )

    def test_a_game_master_lifts_channel_with_who_and_when_and_the_log_says_so(self):
        before = datetime.now().astimezone()
        battle, event = self.lift()
        (entry,) = battle.magic_bounds_lifted_json
        self.assertEqual(entry["combatant_id"], self.ids[0])
        self.assertEqual(entry["bound"], CHANNEL)
        self.assertEqual(entry["by"], self.dm.username)
        self.assertEqual(entry["by_id"], self.dm.pk)
        self.assertGreaterEqual(datetime.fromisoformat(entry["at"]), before)
        assert event is not None
        name = battle.combatants.get(pk=self.ids[0]).display_name
        self.assertEqual(
            event.message,
            "magic_bounds.log_lifted magic_bounds.channel "
            f"common.game_master {self.dm.username} {name}",
        )
        self.assertEqual(event.payload["bounds"], [CHANNEL])
        self.assertTrue(event.payload["lifted"])
        # The same change again changes nothing and logs nothing.
        _battle, again = self.lift()
        self.assertIsNone(again)

    def test_bounds_are_lifted_together_and_held_again_singly(self):
        battle, event = self.lift(bounds=(PUSH_CAP, CHANNEL, COUNTER_BOUNDS))
        lifted = magic_bounds.LiftedBounds.of(battle).for_figure(self.ids[0])
        self.assertEqual(lifted, frozenset(magic_bounds.LIFTABLE_BOUNDS))
        assert event is not None
        self.assertIn(
            magic_bounds.bound_words(magic_bounds.LIFTABLE_BOUNDS), event.message
        )
        battle, held = self.lift(bounds=(PUSH_CAP,), lifted=False)
        self.assertEqual(
            magic_bounds.LiftedBounds.of(battle).for_figure(self.ids[0]),
            frozenset({CHANNEL, COUNTER_BOUNDS}),
        )
        assert held is not None
        self.assertTrue(held.message.startswith("magic_bounds.log_held"))
        # Other figures are untouched.
        record = magic_bounds.LiftedBounds.of(battle)
        self.assertEqual(record.for_figure(self.ids[1]), frozenset())

    def test_a_player_cannot_record_it(self):
        """Players are held to the rules: not a player, not a battle's maker
        who is no Game Master, not a guest."""
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
                    ValueError, words().magic_bounds.game_master_only
                ),
            ):
                set_magic_bounds_lifted(
                    battle.pk,
                    actor,
                    combatant_id=battle.combatants.first().pk,
                    bounds=[CHANNEL],
                    lifted=True,
                )
        for battle in (own, self.battle):
            self.assertEqual(
                Battle.objects.get(pk=battle.pk).magic_bounds_lifted_json, []
            )

    def test_a_bystander_game_master_and_a_players_practice_battle_are_refused(self):
        refusal = words().magic_bounds.game_master_only
        bystander = Actor(user=self.create_dm(username="bystander"), guest_key="")
        with self.assertRaisesMessage(ValueError, refusal):
            self.lift(actor=bystander)
        practice = create_battle(self.player, name="Practice", count=2, seed=3)
        with self.assertRaisesMessage(ValueError, refusal):
            set_magic_bounds_lifted(
                practice.pk,
                self.game_master(),
                combatant_id=practice.combatants.first().pk,
                bounds=[CHANNEL],
                lifted=True,
            )

    def test_nonsense_is_refused(self):
        refusal = words().magic_bounds.refuse_bounds.format(
            bounds=magic_bounds.bound_words(magic_bounds.LIFTABLE_BOUNDS)
        )
        for bounds in ((), ("mana",), (CHANNEL, "mana")):
            with (
                self.subTest(bounds=bounds),
                self.assertRaisesMessage(ValueError, refusal),
            ):
                self.lift(bounds=bounds)
        with self.assertRaisesMessage(
            ValueError, words().magic_bounds.refuse_combatant
        ):
            set_magic_bounds_lifted(
                self.battle.pk,
                self.game_master(),
                combatant_id=999_999,
                bounds=[CHANNEL],
                lifted=True,
            )
        classic = create_battle(
            self.dm, name="Melee", count=2, seed=3, rules_profile="classic-melee"
        )
        with self.assertRaisesMessage(ValueError, words().magic_bounds.refuse_classic):
            set_magic_bounds_lifted(
                classic.pk,
                self.game_master(),
                combatant_id=classic.combatants.first().pk,
                bounds=[CHANNEL],
                lifted=True,
            )

    def test_the_ruling_survives_save_and_load(self):
        """Turns, a reload from the database and a restart keep the record."""
        battle, _event = self.lift(bounds=(CHANNEL, COUNTER_BOUNDS))
        recorded = battle.magic_bounds_lifted_json
        battle, _events = advance_turn(battle.pk)
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(
            Battle.objects.get(pk=battle.pk).magic_bounds_lifted_json, recorded
        )
        restarted = restart_battle(battle.pk, is_game_master=True)
        self.assertEqual(
            Battle.objects.get(pk=restarted.pk).magic_bounds_lifted_json, recorded
        )
        self.assertEqual(
            magic_bounds.LiftedBounds.of(Battle.objects.get(pk=battle.pk)).for_figure(
                self.ids[0]
            ),
            frozenset({CHANNEL, COUNTER_BOUNDS}),
        )

    def test_a_change_while_a_move_is_awaited_clears_the_pause(self):
        battle, seat = started_battle_with_player(self)
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        battle, _event = set_magic_bounds_lifted(
            battle.pk,
            self.game_master(),
            combatant_id=seat.pk,
            bounds=[CHANNEL],
            lifted=True,
        )
        self.assertEqual(battle.status, Battle.STATUS_PAUSED)
        self.assertEqual(battle.pending_input_json, {})

    def test_the_admin_cannot_change_it(self):
        self.assertIn("magic_bounds_lifted_json", BattleAdmin.readonly_fields)


@with_fixed_words
class BoundTheCanonDoesNotImposeTest(CanonImposesTestCase):
    """The Tarmar canon's Push has no cap: there is no push cap to lift."""

    imposed = (CHANNEL, COUNTER_BOUNDS)

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(self.dm, name="Pit", count=2, seed=5)
        self.figure = self.battle.combatants.order_by("pk").first()

    def change(self, bounds, *, lifted):
        return set_magic_bounds_lifted(
            self.battle.pk,
            Actor(user=self.dm, guest_key=""),
            combatant_id=self.figure.pk,
            bounds=bounds,
            lifted=lifted,
        )

    def test_it_is_neither_offered_nor_liftable(self):
        refusal = words().magic_bounds.refuse_bounds.format(
            bounds=magic_bounds.bound_words(self.imposed)
        )
        with self.assertRaisesMessage(ValueError, refusal):
            self.change([PUSH_CAP], lifted=True)
        self.login_as(self.dm)
        page = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        offered = [bound["key"] for bound in page.context["magic_bounds"]["bounds"]]
        self.assertEqual(offered, [CHANNEL, COUNTER_BOUNDS])
        self.assertNotContains(page, f'value="{PUSH_CAP}"')

    def test_a_lifting_recorded_under_another_canon_may_still_be_held_again(self):
        self.battle.magic_bounds_lifted_json = (
            magic_bounds.LiftedBounds()
            .lifted(
                self.figure.pk,
                [PUSH_CAP],
                by=self.dm.username,
                by_id=self.dm.pk,
                at="2026-10-18T10:00:00+00:00",
            )
            .to_json()
        )
        self.battle.save()
        battle, event = self.change([PUSH_CAP], lifted=False)
        self.assertEqual(battle.magic_bounds_lifted_json, [])
        self.assertIsNotNone(event)

    def test_a_canon_imposing_none_shows_no_panel(self):
        with seated_rules(rules_imposing()):
            self.login_as(self.dm)
            page = self.client.get(reverse("battle:detail", args=[self.battle.pk]))
        self.assertIsNone(page.context["magic_bounds"])
        self.assertNotContains(page, MAGIC_BOUNDS_PANEL_ID)


@with_fixed_words
class MagicBoundsEndpointTest(CanonImposesTestCase):
    """The battle's GM control: a Game Master's forms, everyone else the record."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(self.dm, name="Pit", count=2, seed=5)
        self.ids = sorted(self.battle.combatants.values_list("pk", flat=True))
        self.url = reverse("battle:magic-bounds", args=[self.battle.pk])
        self.detail = reverse("battle:detail", args=[self.battle.pk])

    def test_a_game_master_posts_the_form_and_the_page_shows_who_and_when(self):
        self.login_as(self.dm)
        page = self.client.get(self.detail)
        self.assertContains(page, escape(magic_bounds.none_lifted_line()))
        response = self.client.post(
            self.url,
            {
                COMBATANT_FIELD: self.ids[0],
                BOUNDS_FIELD: [CHANNEL, PUSH_CAP],
                LIFTED_FIELD: "true",
            },
        )
        self.assertRedirects(response, self.detail)
        name = self.battle.combatants.get(pk=self.ids[0]).display_name
        by = game_master_name(self.dm.username)
        page = self.client.get(self.detail)
        for bound in (CHANNEL, PUSH_CAP):
            self.assertContains(
                page,
                words().magic_bounds.lifting_line.format(
                    bound=capfirst(magic_bounds.bound_label(bound)), by=by, name=name
                ),
            )
        self.assertContains(page, words().magic_bounds.hold_again, count=2)
        self.assertEqual(
            BattleEvent.objects.filter(
                battle=self.battle, payload__lifted=True
            ).count(),
            1,
        )
        response = self.client.post(
            self.url,
            {COMBATANT_FIELD: self.ids[0], BOUNDS_FIELD: CHANNEL, LIFTED_FIELD: ""},
        )
        self.assertRedirects(response, self.detail)
        self.assertEqual(
            [
                entry["bound"]
                for entry in Battle.objects.get(
                    pk=self.battle.pk
                ).magic_bounds_lifted_json
            ],
            [PUSH_CAP],
        )

    def test_a_script_posting_the_form_gets_the_battle_payload(self):
        """The GM chat's Battle tab posts the panel's own form, fields and all (#89)."""
        self.login_as(self.dm)
        response = self.client.post(
            self.url,
            {
                COMBATANT_FIELD: self.ids[0],
                BOUNDS_FIELD: [CHANNEL, PUSH_CAP],
                LIFTED_FIELD: "true",
                "next": "/tarmar/gm/",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(
                entry["bound"]
                for entry in response.json()["rulings"]["magic_bounds_lifted"]
            ),
            sorted([CHANNEL, PUSH_CAP]),
        )

    def test_the_json_caller_reads_back_the_ruling_and_nonsense_is_refused(self):
        self.login_as(self.dm)
        response = self.post_json(
            self.url,
            {
                COMBATANT_FIELD: self.ids[1],
                BOUNDS_FIELD: [COUNTER_BOUNDS],
                LIFTED_FIELD: True,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("view", body)
        (entry,) = body["rulings"]["magic_bounds_lifted"]
        self.assertEqual(
            (entry["combatant_id"], entry["bound"]), (self.ids[1], COUNTER_BOUNDS)
        )
        # The page's panel, rendered from the new record, for battle.js.
        self.assertIn(
            "magic_bounds.lifting_line", body["rulings_html"][MAGIC_BOUNDS_PANEL_ID]
        )
        for label, sent in (
            (
                "bounds not a list",
                {COMBATANT_FIELD: self.ids[1], BOUNDS_FIELD: CHANNEL},
            ),
            ("no combatant", {COMBATANT_FIELD: "x", BOUNDS_FIELD: [CHANNEL]}),
            ("unknown bound", {COMBATANT_FIELD: self.ids[1], BOUNDS_FIELD: ["mana"]}),
        ):
            with self.subTest(label):
                self.assertEqual(self.post_json(self.url, sent).status_code, 400)
        with self.subTest("a body that is not an object"):
            not_an_object = self.client.post(
                self.url, "[]", content_type="application/json"
            )
            self.assertEqual(not_an_object.status_code, 400)

    def test_a_player_is_refused_and_reads_only_the_record(self):
        player = self.create_player()
        self.battle.combatants.filter(pk=self.ids[0]).update(player=player)
        self.login_as(player)
        sent = {
            COMBATANT_FIELD: self.ids[0],
            BOUNDS_FIELD: [CHANNEL],
            LIFTED_FIELD: True,
        }
        response = self.post_json(self.url, sent)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["error"], words().magic_bounds.game_master_only
        )
        form = self.client.post(self.url, {**sent, LIFTED_FIELD: "true"})
        self.assertEqual(form.status_code, 403)
        self.assertEqual(
            Battle.objects.get(pk=self.battle.pk).magic_bounds_lifted_json, []
        )
        page = self.client.get(self.detail)
        self.assertContains(page, f'id="{MAGIC_BOUNDS_PANEL_ID}"')
        self.assertContains(page, escape(magic_bounds.none_lifted_line()))
        self.assertNotContains(page, words().magic_bounds.lift_button)

    def test_a_bystander_game_master_is_refused(self):
        self.login_as(self.create_dm(username="bystander"))
        response = self.post_json(
            self.url,
            {COMBATANT_FIELD: self.ids[0], BOUNDS_FIELD: [CHANNEL], LIFTED_FIELD: True},
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(
            self.client.get(self.detail), words().magic_bounds.lift_button
        )

    def test_a_classic_battle_shows_no_panel(self):
        classic = create_battle(
            self.dm, name="Melee", count=2, seed=3, rules_profile="classic-melee"
        )
        self.login_as(self.dm)
        page = self.client.get(reverse("battle:detail", args=[classic.pk]))
        self.assertNotContains(page, MAGIC_BOUNDS_PANEL_ID)


@with_fixed_words
class GmChatBattleTabTest(BaseTestCase):
    """The GM chat's Battle tab carries the ruling's panel: session fights are
    where a Game Master rules, and a form post lands back in the chat. Under
    the loaded canon, whichever it is: the first bound it imposes."""

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
        bound = magic_bounds.offered_bounds()[0]
        page = self.client.get(self.chat)
        self.assertTrue(page.context["magic_bounds"]["can_rule"])
        self.assertContains(page, f'id="{MAGIC_BOUNDS_PANEL_ID}"')
        aldwin = self.battle.combatants.get(display_name="Aldwin")
        response = self.client.post(
            reverse("battle:magic-bounds", args=[self.battle.pk]),
            {
                COMBATANT_FIELD: aldwin.pk,
                BOUNDS_FIELD: [bound],
                LIFTED_FIELD: "true",
                "next": self.chat,
            },
        )
        self.assertRedirects(response, self.chat, fetch_redirect_response=False)
        page = self.client.get(self.chat)
        self.assertContains(
            page,
            words().magic_bounds.lifting_line.format(
                bound=capfirst(magic_bounds.bound_label(bound)),
                by=game_master_name("session-gm"),
                name="Aldwin",
            ),
        )
