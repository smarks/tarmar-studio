"""A Game Master grants a figure a working, as a ruling on the battle (#208).

A canon's True Magic may give a working an effect that needs a grant: only a
figure a Game Master has named the working for may craft it. The grant is
recorded on the battle with who and when, as a lifted bound is
(``test_magic_bounds``), by the fight's runner while a Game Master
(``runs_the_seats``), and no player records one. The Tarmar profile writes the
record onto each figure's ``granted_workings`` every turn (tarmar-engine
v0.9.22), and the engine's menu then offers that figure the craft.

Every test runs under the default canon's magic rules, read from its own file
(``magic_fixtures.DEFAULT_MAGIC_RULES``), whichever canon is loaded: its True
Magic has a narrated working that needs a grant, and two that need none.
"""

from dataclasses import replace
from datetime import datetime

from django.test import SimpleTestCase
from tarmar_engine.actions import CRAFT_OPTION
from tarmar_engine.magic import WorkingEffect
from tarmar_engine.policy import Candidate, choose_option

from accounts.models import User
from battle import magic_bounds, profiles, working_grants
from battle.access import Actor
from battle.admin import BattleAdmin
from battle.engine.state import BattleState
from battle.models import Battle, BattleCombatant, BattleEvent
from battle.persistence import dump_state, load_state
from battle.services import (
    advance_turn,
    claim_seat,
    create_battle,
    release_seat,
    restart_battle,
    set_magic_bounds_lifted,
    set_seat_ready,
    set_working_granted,
    start_battle,
)
from battle.wording import words
from characters.models import Character
from characters.skills import ARCANE_SKILL_NAME, points_for_level
from common.rolling import Roller
from common.test_helpers import BaseTestCase, create_character

from .magic_fixtures import DEFAULT_MAGIC_RULES, seated_rules
from .rulings_words import with_fixed_words
from .test_lobby import open_battle
from .test_state import make_combatant

assert DEFAULT_MAGIC_RULES.true_magic is not None, "the default canon has True Magic"
TRUE_MAGIC = DEFAULT_MAGIC_RULES.true_magic


def effect_that(*, needs_grant: bool) -> WorkingEffect:
    """The default canon's first working effect that does, or does not, need
    a Game Master's grant."""
    return next(
        effect for effect in TRUE_MAGIC.effects if effect.needs_grant == needs_grant
    )


#: The working that needs a grant, at its first tiers, and one that needs
#: none. A True mage at level 1 has a tier cap of :data:`TIER_CAP_AT_LEVEL_ONE`,
#: so the first two tiers are within it and :data:`PAST_THE_CAP` is past it.
GRANTED_EFFECT = effect_that(needs_grant=True)
TIER_CAP_AT_LEVEL_ONE = TRUE_MAGIC.tier_cap(1)
FIRST_TIER = TRUE_MAGIC.working_key(GRANTED_EFFECT.key, 1)
SECOND_TIER = TRUE_MAGIC.working_key(GRANTED_EFFECT.key, 2)
PAST_THE_CAP = TRUE_MAGIC.working_key(GRANTED_EFFECT.key, TIER_CAP_AT_LEVEL_ONE + 1)
NEEDS_NO_GRANT = TRUE_MAGIC.working_key(effect_that(needs_grant=False).key, 1)

#: The default canon's magic rules with no True Magic at all.
NO_TRUE_MAGIC = replace(DEFAULT_MAGIC_RULES, true_magic=None)

#: When the recorded grants below were made.
FIRST_AT = "2026-11-18T10:00:00+00:00"
LATER_AT = "2026-11-18T10:05:00+00:00"


def record_granting(
    combatant_id: int, *working_keys: str
) -> working_grants.WorkingGrants:
    """A ruling granting ``working_keys`` to one figure."""
    return working_grants.WorkingGrants().with_ruled(
        combatant_id, working_keys, by="gm", by_id=1, at=FIRST_AT
    )


def true_mage_board(*, true_level: int = 1) -> BattleState:
    """A True mage (combatant 1) at ``true_level`` with mana to craft, and a
    fighter (combatant 2) with no level in the True skill."""
    return BattleState(
        arena_radius=6,
        combatants=[
            make_combatant(
                1,
                name="Mage",
                mana=30,
                max_mana=30,
                skill_levels={ARCANE_SKILL_NAME: true_level},
            ),
            make_combatant(2, name="Fighter", q=3, r=0),
        ],
    )


def true_mage(owner: User, *, true_level: int = 1) -> Character:
    """A saved character with ``true_level`` levels of Arcane."""
    return create_character(
        owner,
        name="Mage",
        skills={ARCANE_SKILL_NAME: points_for_level(true_level, ARCANE_SKILL_NAME)},
    )


def lobby_with_a_true_mage_seated(
    game_master: User, player: User, *, seed: int = 11
) -> tuple[Battle, BattleCombatant]:
    """A lobby a Game Master opened, one generated fighter in it, and its one
    seat claimed by a player with a saved True mage. Needs the default
    canon's rules seated, for the seat to carry the mage's level."""
    battle = open_battle(game_master, seats=1, count=1, seed=seed)
    open_seat = battle.combatants.get(is_open_seat=True)
    seat = claim_seat(
        battle.pk, open_seat.pk, player, character_id=true_mage(player).pk
    )
    return Battle.objects.get(pk=battle.pk), seat


def start_with_the_seat_ready(
    battle: Battle, seat: BattleCombatant, player: User
) -> Battle:
    """Close the lobby: the player readies the seat and the fight starts."""
    set_seat_ready(battle.pk, seat.pk, player, ready=True)
    return start_battle(battle.pk)


def crafts_offered(state: BattleState, combatant_id: int = 1) -> set[str]:
    """The workings the engine's menu offers one figure to begin crafting, on
    the seated rules."""
    menu: list[Candidate] = choose_option(
        state, state.by_id(combatant_id), magic=profiles.tarmar_engine_profile().magic
    ).candidates
    return {
        candidate.spell_key for candidate in menu if candidate.letter == CRAFT_OPTION
    }


class DefaultCanonTestCase(SimpleTestCase):
    """A test case under the default canon's magic rules."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(DEFAULT_MAGIC_RULES))


class TheDefaultCanonsWorkingsTest(SimpleTestCase):
    """What the tests below rest on, read from the default canon's file."""

    def test_one_working_needs_a_grant_and_a_level_one_mage_has_a_cap(self) -> None:
        self.assertEqual(
            (GRANTED_EFFECT.label, FIRST_TIER), ("Figment", "working:figment:1")
        )
        self.assertEqual(NEEDS_NO_GRANT, "working:sear:1")
        self.assertEqual(TIER_CAP_AT_LEVEL_ONE, 2)
        self.assertEqual(PAST_THE_CAP, "working:figment:3")


@with_fixed_words
class WorkingGrantsRecordTest(DefaultCanonTestCase):
    """The ruling as the battle stores it, and the words it is shown in."""

    def test_the_stored_shape_round_trips_in_the_order_granted(self) -> None:
        record = (
            working_grants.WorkingGrants()
            .with_ruled(5, [SECOND_TIER], by="gm", by_id=1, at=FIRST_AT)
            .with_ruled(2, [FIRST_TIER], by="other", by_id=7, at=LATER_AT)
            .with_ruled(5, [FIRST_TIER], by="gm", by_id=1, at=LATER_AT)
        )
        stored = record.to_json()
        self.assertEqual(
            stored,
            [
                {
                    "combatant_id": 2,
                    "working": FIRST_TIER,
                    "by": "other",
                    "by_id": 7,
                    "at": LATER_AT,
                },
                {
                    "combatant_id": 5,
                    "working": SECOND_TIER,
                    "by": "gm",
                    "by_id": 1,
                    "at": FIRST_AT,
                },
                {
                    "combatant_id": 5,
                    "working": FIRST_TIER,
                    "by": "gm",
                    "by_id": 1,
                    "at": LATER_AT,
                },
            ],
        )
        battle = Battle(workings_granted_json=stored)
        read_back = working_grants.WorkingGrants.of(battle)
        self.assertEqual(read_back.to_json(), stored)
        self.assertEqual(read_back.for_figure(5), frozenset({FIRST_TIER, SECOND_TIER}))
        self.assertEqual(read_back.for_figure(3), frozenset())
        self.assertEqual(working_grants.WorkingGrants().to_json(), [])
        self.assertEqual(working_grants.WorkingGrants.of(Battle()).to_json(), [])

    def test_a_grant_again_keeps_its_first_record_and_a_withdrawal_takes_one(
        self,
    ) -> None:
        first = record_granting(3, FIRST_TIER)
        again = first.with_ruled(
            3, [FIRST_TIER, SECOND_TIER], by="other", by_id=9, at=LATER_AT
        )
        self.assertEqual(
            [(entry.subject, entry.by, entry.at) for entry in again.in_order()],
            [(FIRST_TIER, "gm", FIRST_AT), (SECOND_TIER, "other", LATER_AT)],
        )
        withdrawn = again.with_ruled(
            4, [FIRST_TIER], by="gm", by_id=1, at=LATER_AT
        ).without(3, [FIRST_TIER])
        self.assertEqual(withdrawn.for_figure(3), frozenset({SECOND_TIER}))
        # Another figure's grant of the same working stands.
        self.assertEqual(withdrawn.for_figure(4), frozenset({FIRST_TIER}))

    def test_each_ruling_keeps_its_own_record_on_the_battle(self) -> None:
        # One record machinery, two rulings: neither reads the other's field
        # or stores its subject under the other's name.
        grants = record_granting(1, FIRST_TIER)
        liftings = magic_bounds.LiftedBounds().lifted(
            1, [magic_bounds.CHANNEL], by="gm", by_id=1, at=FIRST_AT
        )
        battle = Battle(
            workings_granted_json=grants.to_json(),
            magic_bounds_lifted_json=liftings.to_json(),
        )
        self.assertEqual(
            working_grants.WorkingGrants.of(battle).for_figure(1),
            frozenset({FIRST_TIER}),
        )
        self.assertEqual(
            magic_bounds.LiftedBounds.of(battle).for_figure(1),
            frozenset({magic_bounds.CHANNEL}),
        )
        self.assertEqual(
            (set(grants.to_json()[0]), set(liftings.to_json()[0])),
            (
                {"combatant_id", "working", "by", "by_id", "at"},
                {"combatant_id", "bound", "by", "by_id", "at"},
            ),
        )
        self.assertNotEqual(grants, magic_bounds.LiftedBounds(grants.entries))

    def test_the_log_names_the_working_as_the_board_does(self) -> None:
        self.assertEqual(working_grants.working_label(SECOND_TIER), "Figment (tier 2)")
        self.assertEqual(
            working_grants.change_message(
                working_key=SECOND_TIER, granted=True, name="Aldwin", by="GM"
            ),
            "working_grants.log_granted GM Aldwin Figment (tier 2)",
        )
        self.assertEqual(
            working_grants.change_message(
                working_key=FIRST_TIER, granted=False, name="Aldwin", by="GM"
            ),
            "working_grants.log_withdrawn GM Aldwin Figment (tier 1)",
        )

    def test_a_working_the_canon_no_longer_builds_is_named_by_its_key(self) -> None:
        with seated_rules(NO_TRUE_MAGIC):
            self.assertEqual(
                working_grants.working_label(FIRST_TIER),
                f"working_grants.unbuilt_working {FIRST_TIER}",
            )
        self.assertEqual(
            working_grants.working_label("working:no_such_effect:1"),
            "working_grants.unbuilt_working working:no_such_effect:1",
        )


class BuildsTest(DefaultCanonTestCase):
    """Which keys the loaded canon builds a working for."""

    def test_only_a_working_of_an_effect_and_a_tier_the_rules_carry(self) -> None:
        for working_key, built in (
            (FIRST_TIER, True),
            (PAST_THE_CAP, True),
            (NEEDS_NO_GRANT, True),
            ("working:no_such_effect:1", False),
            (TRUE_MAGIC.working_key(GRANTED_EFFECT.key, 1)[:-1] + "0", False),
            ("fire_ball", False),
            ("", False),
        ):
            with self.subTest(working_key=working_key):
                self.assertEqual(
                    working_grants.builds(working_key, DEFAULT_MAGIC_RULES), built
                )
        self.assertFalse(working_grants.builds(FIRST_TIER, NO_TRUE_MAGIC))


class FillTest(DefaultCanonTestCase):
    """The ruling written onto the engine's snapshot, and read back."""

    def test_each_figure_carries_its_own_grants_and_a_refill_clears_them(self) -> None:
        state = true_mage_board()
        working_grants.fill(state, record_granting(1, SECOND_TIER, FIRST_TIER))
        self.assertEqual(state.by_id(1).granted_workings, [SECOND_TIER, FIRST_TIER])
        self.assertEqual(state.by_id(2).granted_workings, [])
        working_grants.fill(state, working_grants.WorkingGrants())
        self.assertEqual(state.by_id(1).granted_workings, [])

    def test_the_field_survives_dump_and_load(self) -> None:
        state = true_mage_board()
        working_grants.fill(state, record_granting(1, FIRST_TIER))
        reloaded = load_state(dump_state(state), "tarmar")
        self.assertEqual(reloaded.by_id(1).granted_workings, [FIRST_TIER])

    def test_a_grant_the_canon_no_longer_builds_stays_off_the_figure(self) -> None:
        # The engine refuses a figure that lists a working its rules do not
        # build, so the record keeps such a grant and the board never sees it.
        record = record_granting(1, "working:no_such_effect:1", FIRST_TIER)
        state = true_mage_board()
        working_grants.fill(state, record)
        self.assertEqual(state.by_id(1).granted_workings, [FIRST_TIER])
        self.assertIn(FIRST_TIER, crafts_offered(state))
        with seated_rules(NO_TRUE_MAGIC):
            working_grants.fill(state, record)
            self.assertEqual(state.by_id(1).granted_workings, [])
            self.assertEqual(crafts_offered(state), set())

    def test_the_profile_fills_it_every_turn_and_for_the_cast_stage(self) -> None:
        battle = Battle(workings_granted_json=record_granting(1, FIRST_TIER).to_json())
        profile = profiles.TarmarProfile(battle)
        state = true_mage_board()
        profile.run_turn(state, Roller(seed=3), profiles.decider(battle))
        self.assertEqual(state.by_id(1).granted_workings, [FIRST_TIER])
        # The cast stage reads the ruling as it stands, not as the state last
        # had it.
        stale = true_mage_board()
        profile.caster_magic(stale, 1)
        self.assertEqual(stale.by_id(1).granted_workings, [FIRST_TIER])


class GrantedWorkingOnTheMenuTest(DefaultCanonTestCase):
    """What the grant changes: the engine's menu offers the figure the craft."""

    def test_ungranted_the_working_is_withheld_and_the_others_are_offered(
        self,
    ) -> None:
        offered = crafts_offered(true_mage_board())
        self.assertNotIn(FIRST_TIER, offered)
        self.assertIn(NEEDS_NO_GRANT, offered)

    def test_granted_the_menu_offers_that_working_and_no_other_tier(self) -> None:
        state = true_mage_board()
        ungranted = crafts_offered(state)
        working_grants.fill(state, record_granting(1, FIRST_TIER))
        self.assertEqual(crafts_offered(state), ungranted | {FIRST_TIER})
        # The fighter's menu is unchanged: it crafts nothing, granted or not.
        working_grants.fill(state, record_granting(2, FIRST_TIER))
        self.assertEqual(crafts_offered(state, 2), set())

    def test_withdrawn_the_working_is_withheld_again(self) -> None:
        state = true_mage_board()
        ungranted = crafts_offered(state)
        granted = record_granting(1, FIRST_TIER, SECOND_TIER)
        working_grants.fill(state, granted)
        self.assertEqual(crafts_offered(state), ungranted | {FIRST_TIER, SECOND_TIER})
        working_grants.fill(state, granted.without(1, [FIRST_TIER]))
        self.assertEqual(crafts_offered(state), ungranted | {SECOND_TIER})

    def test_a_grant_past_the_tier_cap_waits_on_channel_being_lifted(self) -> None:
        # Whether a grant waits is asked of the engine, so it says what the
        # engine's menu does: waiting, the craft is withheld; not, offered.
        state = true_mage_board()
        mage = state.by_id(1)
        working_grants.fill(state, record_granting(1, PAST_THE_CAP, SECOND_TIER))
        self.assertTrue(working_grants.waits_on_channel(mage, PAST_THE_CAP))
        self.assertFalse(working_grants.waits_on_channel(mage, SECOND_TIER))
        self.assertEqual(
            crafts_offered(state) & {PAST_THE_CAP, SECOND_TIER}, {SECOND_TIER}
        )
        # Another bound lifted lifts no tier cap.
        magic_bounds.fill(
            state,
            magic_bounds.LiftedBounds().lifted(
                1, [magic_bounds.PUSH_CAP], by="gm", by_id=1, at=FIRST_AT
            ),
        )
        self.assertTrue(working_grants.waits_on_channel(mage, PAST_THE_CAP))
        self.assertNotIn(PAST_THE_CAP, crafts_offered(state))
        magic_bounds.fill(
            state,
            magic_bounds.LiftedBounds().lifted(
                1, [magic_bounds.CHANNEL], by="gm", by_id=1, at=FIRST_AT
            ),
        )
        self.assertFalse(working_grants.waits_on_channel(mage, PAST_THE_CAP))
        self.assertIn(PAST_THE_CAP, crafts_offered(state))
        # Lifted or not, the working is past the figure's own cap.
        self.assertTrue(working_grants.past_tier_cap(mage, PAST_THE_CAP))

    def test_past_the_tier_cap_is_the_engines_answer_for_the_figure(self) -> None:
        for working_key, true_level, past in (
            (SECOND_TIER, 1, False),
            (PAST_THE_CAP, 1, True),
            (PAST_THE_CAP, 2, False),
            (FIRST_TIER, 0, True),
        ):
            with self.subTest(working_key=working_key, true_level=true_level):
                figure = true_mage_board(true_level=true_level).by_id(1)
                self.assertEqual(
                    working_grants.past_tier_cap(figure, working_key), past
                )
        mage = true_mage_board().by_id(1)
        for asked in (working_grants.past_tier_cap, working_grants.waits_on_channel):
            with self.subTest(asked=asked.__name__):
                with self.assertRaises(KeyError):
                    asked(mage, "working:no_such_effect:1")
                with seated_rules(NO_TRUE_MAGIC), self.assertRaises(KeyError):
                    asked(mage, FIRST_TIER)


class TrueMageBattleTestCase(BaseTestCase):
    """A battle a Game Master runs, with a saved True mage and a generated
    fighter, under the default canon's magic rules."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(DEFAULT_MAGIC_RULES))
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.mage_character = true_mage(self.dm)
        self.battle = create_battle(
            self.dm,
            name="Pit",
            count=1,
            seed=5,
            character_ids=(self.mage_character.pk,),
        )
        self.mage = self.battle.combatants.get(character=self.mage_character)
        self.fighter = self.battle.combatants.exclude(pk=self.mage.pk).get()

    def game_master(self) -> Actor:
        return Actor(user=self.dm, guest_key="")

    def grant(
        self,
        working_key: str = FIRST_TIER,
        *,
        granted: bool = True,
        battle: Battle | None = None,
        actor: Actor | None = None,
        figure: BattleCombatant | None = None,
    ) -> tuple[Battle, BattleEvent | None]:
        return set_working_granted(
            (battle or self.battle).pk,
            actor or self.game_master(),
            combatant_id=(figure or self.mage).pk,
            working_key=working_key,
            granted=granted,
        )

    def recorded(self, battle: Battle | None = None) -> list[dict]:
        """The grants as the database holds them."""
        return Battle.objects.get(pk=(battle or self.battle).pk).workings_granted_json


@with_fixed_words
class SetWorkingGrantedTest(TrueMageBattleTestCase):
    """Who may record the ruling, and what each change leaves behind."""

    def test_a_game_master_grants_it_with_who_and_when_and_the_log_says_so(
        self,
    ) -> None:
        before = datetime.now().astimezone()
        battle, event = self.grant()
        (entry,) = battle.workings_granted_json
        self.assertEqual(
            (entry["combatant_id"], entry["working"], entry["by"], entry["by_id"]),
            (self.mage.pk, FIRST_TIER, self.dm.username, self.dm.pk),
        )
        self.assertGreaterEqual(datetime.fromisoformat(entry["at"]), before)
        self.assertEqual(self.recorded(), [entry])
        assert event is not None
        self.assertEqual(
            event.message,
            f"working_grants.log_granted common.game_master {self.dm.username} "
            "Mage Figment (tier 1)",
        )
        self.assertEqual(
            event.payload,
            {
                "workings_granted": [entry],
                "combatant_id": self.mage.pk,
                "working": FIRST_TIER,
                "granted": True,
                "past_tier_cap": False,
            },
        )
        # The same grant again changes nothing and logs nothing.
        battle, again = self.grant()
        self.assertIsNone(again)
        self.assertEqual(battle.workings_granted_json, [entry])

    def test_a_grant_is_of_one_tier_and_is_withdrawn_singly(self) -> None:
        self.grant(FIRST_TIER)
        battle, _event = self.grant(SECOND_TIER)
        self.assertEqual(
            working_grants.WorkingGrants.of(battle).for_figure(self.mage.pk),
            frozenset({FIRST_TIER, SECOND_TIER}),
        )
        battle, event = self.grant(FIRST_TIER, granted=False)
        self.assertEqual(
            working_grants.WorkingGrants.of(battle).for_figure(self.mage.pk),
            frozenset({SECOND_TIER}),
        )
        assert event is not None
        self.assertEqual(
            event.message,
            f"working_grants.log_withdrawn common.game_master {self.dm.username} "
            "Mage Figment (tier 1)",
        )
        self.assertEqual(
            event.payload,
            {
                "workings_granted": battle.workings_granted_json,
                "combatant_id": self.mage.pk,
                "working": FIRST_TIER,
                "granted": False,
            },
        )
        # Withdrawing what is not granted changes nothing and logs nothing.
        battle, again = self.grant(FIRST_TIER, granted=False)
        self.assertIsNone(again)
        self.assertEqual(len(battle.workings_granted_json), 1)

    def test_a_player_cannot_record_it(self) -> None:
        """Players are held to the rules: not a player, not a battle's maker
        who is no Game Master, not a guest."""
        maker = self.create_player(username="maker")
        makers_character = true_mage(maker)
        own = create_battle(
            maker, name="Mine", count=1, seed=3, character_ids=(makers_character.pk,)
        )
        own_mage = own.combatants.get(character=makers_character)
        for who, actor, battle, figure in (
            ("maker", Actor(user=maker, guest_key=""), own, own_mage),
            ("player", Actor(user=self.player, guest_key=""), self.battle, self.mage),
            ("guest", Actor(user=None, guest_key="guest-key"), self.battle, self.mage),
        ):
            with (
                self.subTest(who=who),
                self.assertRaisesMessage(
                    ValueError, words().working_grants.game_master_only
                ),
            ):
                self.grant(battle=battle, actor=actor, figure=figure)
            with (
                self.subTest(who=who, granted=False),
                self.assertRaisesMessage(
                    ValueError, words().working_grants.game_master_only
                ),
            ):
                self.grant(battle=battle, actor=actor, figure=figure, granted=False)
        for battle in (own, self.battle):
            self.assertEqual(self.recorded(battle), [])

    def test_a_bystander_game_master_and_a_players_practice_battle_are_refused(
        self,
    ) -> None:
        refusal = words().working_grants.game_master_only
        bystander = Actor(user=self.create_dm(username="bystander"), guest_key="")
        with self.assertRaisesMessage(ValueError, refusal):
            self.grant(actor=bystander)
        practice = create_battle(self.player, name="Practice", count=2, seed=3)
        with self.assertRaisesMessage(ValueError, refusal):
            self.grant(battle=practice, figure=practice.combatants.first())
        self.assertEqual(self.recorded(), [])

    def test_a_player_is_told_only_that_it_is_the_game_masters(self) -> None:
        # Whatever else is wrong with what was asked: the canon's answers are
        # for the Game Master.
        player = Actor(user=self.player, guest_key="")
        for working_key in ("working:no_such_effect:1", NEEDS_NO_GRANT):
            with (
                self.subTest(working_key=working_key),
                self.assertRaisesMessage(ValueError, "working_grants.game_master_only"),
            ):
                self.grant(working_key, actor=player, figure=self.fighter)

    def test_a_classic_battle_is_refused(self) -> None:
        classic = create_battle(
            self.dm, name="Melee", count=2, seed=3, rules_profile="classic-melee"
        )
        for granted in (True, False):
            with (
                self.subTest(granted=granted),
                self.assertRaisesMessage(ValueError, "working_grants.refuse_classic"),
            ):
                self.grant(
                    battle=classic, figure=classic.combatants.first(), granted=granted
                )

    def test_a_canon_with_no_true_magic_has_no_working_to_grant(self) -> None:
        with (
            seated_rules(NO_TRUE_MAGIC),
            self.assertRaisesMessage(ValueError, "working_grants.refuse_no_true_magic"),
        ):
            self.grant()
        self.assertEqual(self.recorded(), [])

    def test_a_key_the_rules_do_not_build_is_refused(self) -> None:
        for working_key in (
            "working:no_such_effect:1",
            f"working:{GRANTED_EFFECT.key}:0",
            f"working:{GRANTED_EFFECT.key}",
            GRANTED_EFFECT.key,
            "fire_ball",
            "",
        ):
            with (
                self.subTest(working_key=working_key),
                self.assertRaisesMessage(ValueError, "working_grants.refuse_working"),
            ):
                self.grant(working_key)
        self.assertEqual(self.recorded(), [])

    def test_a_working_that_needs_no_grant_is_refused(self) -> None:
        with self.assertRaisesMessage(
            ValueError, "working_grants.refuse_needs_no_grant Sear (tier 1)"
        ):
            self.grant(NEEDS_NO_GRANT)
        self.assertEqual(self.recorded(), [])

    def test_a_working_that_needs_no_grant_is_refused_before_the_figures_level(
        self,
    ) -> None:
        # Two faults at once: the working needs no grant, and the fighter has
        # no level in the True skill. The Game Master is told of the working,
        # which no figure could be granted.
        with self.assertRaisesMessage(
            ValueError, "working_grants.refuse_needs_no_grant Sear (tier 1)"
        ):
            self.grant(NEEDS_NO_GRANT, figure=self.fighter)
        self.assertEqual(self.recorded(), [])

    def test_a_key_the_rules_do_not_build_is_refused_before_the_figures_level(
        self,
    ) -> None:
        # Two faults at once: no working by that key, and no level in the
        # True skill.
        with self.assertRaisesMessage(ValueError, "working_grants.refuse_working"):
            self.grant("working:no_such_effect:1", figure=self.fighter)
        self.assertEqual(self.recorded(), [])

    def test_a_figure_not_in_this_battle_is_refused(self) -> None:
        elsewhere = create_battle(self.dm, name="Elsewhere", count=2, seed=9)
        for granted in (True, False):
            with (
                self.subTest(granted=granted),
                self.assertRaisesMessage(ValueError, "working_grants.refuse_combatant"),
            ):
                self.grant(figure=elsewhere.combatants.first(), granted=granted)
        self.assertEqual(self.recorded(), [])

    def test_a_figure_with_no_level_in_the_true_skill_is_refused(self) -> None:
        with self.assertRaisesMessage(
            ValueError,
            f"working_grants.refuse_no_true_level {self.fighter.display_name} Arcane",
        ):
            self.grant(figure=self.fighter)
        self.assertEqual(self.recorded(), [])
        self.assertEqual(
            {
                combatant_id: DEFAULT_MAGIC_RULES.true_level(figure)
                for combatant_id, figure in working_grants.engine_figures(
                    self.battle
                ).items()
            },
            {self.mage.pk: 1, self.fighter.pk: 0},
        )

    def test_a_grant_past_the_tier_cap_is_recorded_and_says_it_is_past(self) -> None:
        battle, event = self.grant(PAST_THE_CAP)
        self.assertEqual(
            working_grants.WorkingGrants.of(battle).for_figure(self.mage.pk),
            frozenset({PAST_THE_CAP}),
        )
        assert event is not None
        self.assertIs(event.payload["past_tier_cap"], True)
        # It takes effect only with Channel lifted for the figure.
        self.assertEqual(self.waiting_on_channel(battle), {PAST_THE_CAP: True})
        # Channel lifted for another figure lifts nothing for this one.
        battle = self.lift_channel(self.fighter)
        self.assertEqual(self.waiting_on_channel(battle), {PAST_THE_CAP: True})
        # Lifted for the mage, it is read off the battle's record at once:
        # no turn has yet written the ruling onto the board.
        battle = self.lift_channel(self.mage)
        self.assertEqual(self.waiting_on_channel(battle), {PAST_THE_CAP: False})
        self.assertEqual(
            battle.state_json["combatants"][0].get("lifted_magic_bounds", []), []
        )
        # Held to Channel again, it waits again.
        battle = self.lift_channel(self.mage, lifted=False)
        self.assertEqual(self.waiting_on_channel(battle), {PAST_THE_CAP: True})

    def lift_channel(self, figure: BattleCombatant, *, lifted: bool = True) -> Battle:
        """The battle with Channel lifted, or held again, for one figure."""
        battle, _event = set_magic_bounds_lifted(
            self.battle.pk,
            self.game_master(),
            combatant_id=figure.pk,
            bounds=[magic_bounds.CHANNEL],
            lifted=lifted,
        )
        return battle

    def waiting_on_channel(self, battle: Battle) -> dict[str, bool]:
        """Each working granted the mage, and whether its grant waits on
        Channel being lifted for the mage."""
        mage = working_grants.engine_figures(battle)[self.mage.pk]
        return {
            working_key: working_grants.waits_on_channel(mage, working_key)
            for working_key in working_grants.WorkingGrants.of(battle).for_figure(
                self.mage.pk
            )
        }

    def test_a_grant_made_under_another_canon_may_still_be_withdrawn(self) -> None:
        self.grant()
        with seated_rules(NO_TRUE_MAGIC):
            battle, event = self.grant(granted=False)
        self.assertEqual(battle.workings_granted_json, [])
        assert event is not None
        self.assertEqual(
            event.message,
            f"working_grants.log_withdrawn common.game_master {self.dm.username} "
            f"Mage working_grants.unbuilt_working {FIRST_TIER}",
        )

    def test_the_ruling_survives_save_and_load(self) -> None:
        """Turns, a reload from the database and a restart keep the record."""
        battle, _event = self.grant()
        recorded = battle.workings_granted_json
        battle, _events = advance_turn(battle.pk)
        battle, _events = advance_turn(battle.pk)
        self.assertEqual(self.recorded(), recorded)
        restarted = restart_battle(battle.pk, is_game_master=True)
        self.assertEqual(self.recorded(restarted), recorded)

    def test_the_admin_cannot_change_it(self) -> None:
        self.assertIn("workings_granted_json", BattleAdmin.readonly_fields)


@with_fixed_words
class GrantOnASeatedPlayersMenuTest(BaseTestCase):
    """The whole path: a Game Master's grant, recorded by the service, reaches
    a seated player's menu on the next advance, and its withdrawal takes it
    off again."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(DEFAULT_MAGIC_RULES))
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.battle, self.seat = lobby_with_a_true_mage_seated(self.dm, self.player)

    def grant(self, *, granted: bool) -> tuple[Battle, BattleEvent | None]:
        return set_working_granted(
            self.battle.pk,
            Actor(user=self.dm, guest_key=""),
            combatant_id=self.seat.pk,
            working_key=FIRST_TIER,
            granted=granted,
        )

    def start(self) -> None:
        start_with_the_seat_ready(self.battle, self.seat, self.player)

    def crafts_awaited(self) -> set[str]:
        """The workings the seated mage's awaited menu offers to craft."""
        battle, _events = advance_turn(self.battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(battle.pending_input_json["combatant_id"], self.seat.pk)
        return {
            candidate["spell_key"]
            for candidate in battle.pending_input_json["candidates"]
            if candidate["letter"] == CRAFT_OPTION
        }

    def test_a_grant_in_the_lobby_reads_the_seats_snapshot_and_has_no_log(
        self,
    ) -> None:
        # A lobby has no board yet: the level is read off the seat's own
        # snapshot, an open seat has none, and there is no log to write to.
        open_seat_battle = open_battle(self.dm, seats=1, count=1, seed=4)
        open_seat = open_seat_battle.combatants.get(is_open_seat=True)
        self.assertNotIn(open_seat.pk, working_grants.engine_figures(open_seat_battle))
        with self.assertRaisesMessage(
            ValueError,
            f"working_grants.refuse_no_true_level {open_seat.display_name} Arcane",
        ):
            set_working_granted(
                open_seat_battle.pk,
                Actor(user=self.dm, guest_key=""),
                combatant_id=open_seat.pk,
                working_key=FIRST_TIER,
                granted=True,
            )
        seated = working_grants.engine_figures(self.battle)[self.seat.pk]
        self.assertEqual(DEFAULT_MAGIC_RULES.true_level(seated), 1)
        battle, event = self.grant(granted=True)
        self.assertIsNone(event)
        self.assertEqual(
            working_grants.WorkingGrants.of(battle).for_figure(self.seat.pk),
            frozenset({FIRST_TIER}),
        )
        self.start()
        self.assertIn(FIRST_TIER, self.crafts_awaited())

    def test_granted_the_awaited_menu_offers_the_craft_and_withdrawn_it_does_not(
        self,
    ) -> None:
        self.start()
        ungranted = self.crafts_awaited()
        self.assertNotIn(FIRST_TIER, ungranted)
        self.assertTrue(ungranted)
        # The grant clears the pause, and the next advance asks again with
        # the craft on the menu.
        battle, event = self.grant(granted=True)
        self.assertIsNotNone(event)
        self.assertEqual(battle.status, Battle.STATUS_PAUSED)
        self.assertEqual(self.crafts_awaited(), ungranted | {FIRST_TIER})
        battle, event = self.grant(granted=False)
        self.assertIsNotNone(event)
        self.assertEqual(self.crafts_awaited(), ungranted)

    def test_a_released_seats_grants_go_with_the_figure_that_held_it(self) -> None:
        # A grant names a figure. The next character to claim the seat is
        # another figure under the same id, and is granted nothing.
        self.grant(granted=True)
        release_seat(self.battle.pk, self.seat.pk, self.player)
        self.assertEqual(
            Battle.objects.get(pk=self.battle.pk).workings_granted_json, []
        )
        newcomer = self.create_player(username="newcomer")
        self.seat = claim_seat(
            self.battle.pk,
            self.seat.pk,
            newcomer,
            character_id=true_mage(newcomer).pk,
        )
        start_with_the_seat_ready(self.battle, self.seat, newcomer)
        offered = self.crafts_awaited()
        self.assertNotIn(FIRST_TIER, offered)
        # The newcomer is a True mage, offered every craft that needs no grant.
        self.assertIn(NEEDS_NO_GRANT, offered)

    def test_releasing_a_seat_leaves_every_other_seats_grants(self) -> None:
        battle = open_battle(self.dm, seats=2, count=0, seed=6)
        game_master = Actor(user=self.dm, guest_key="")
        seats = []
        for number, open_seat in enumerate(battle.combatants.order_by("pk")):
            player = self.create_player(username=f"mage-{number}")
            seat = claim_seat(
                battle.pk, open_seat.pk, player, character_id=true_mage(player).pk
            )
            for working_key in (FIRST_TIER, SECOND_TIER):
                set_working_granted(
                    battle.pk,
                    game_master,
                    combatant_id=seat.pk,
                    working_key=working_key,
                    granted=True,
                )
            seats.append((seat, player))
        (leaving, leaver), (staying, _stayer) = seats
        before = Battle.objects.get(pk=battle.pk).workings_granted_json
        release_seat(battle.pk, leaving.pk, leaver)
        after = Battle.objects.get(pk=battle.pk).workings_granted_json
        self.assertEqual(
            after, [entry for entry in before if entry["combatant_id"] == staying.pk]
        )
        self.assertEqual(
            [entry["working"] for entry in after], [FIRST_TIER, SECOND_TIER]
        )
