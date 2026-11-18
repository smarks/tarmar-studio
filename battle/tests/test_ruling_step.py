"""The steps every Game Master's ruling on a battle takes (tarmar-studio #208).

Three rulings are recorded on a battle: hand-to-hand by agreement (#867), a
figure's lifted magic bounds (#37) and a working granted to a figure (#208).
Each locks the battle, is held to the fight's Game Master, refuses a classic
battle and a figure not in the battle, writes its own record, clears a pause
on a person's move, files one line in the log and saves. Those steps are
written once (``battle.services._record_ruling``), so each test here runs
under all three services: every one still refuses in its own words and
records as it did when it carried the steps itself.

The rulings' own refusals and records are their own tests'
(``test_hth_agreement``, ``test_magic_bounds``, ``test_working_grants``).
"""

from collections.abc import Callable
from dataclasses import dataclass

from accounts.models import User
from battle import magic_bounds
from battle.access import Actor
from battle.models import Battle, BattleEvent
from battle.services import (
    advance_turn,
    create_battle,
    set_hth_agreement,
    set_magic_bounds_lifted,
    set_working_granted,
)
from common.test_helpers import BaseTestCase

from .magic_fixtures import DEFAULT_MAGIC_RULES, seated_rules
from .rulings_words import with_fixed_words
from .test_working_grants import (
    FIRST_TIER,
    lobby_with_a_true_mage_seated,
    start_with_the_seat_ready,
)

#: A ruling's change for two figures of a battle: the first is the one a
#: single-figure ruling names, and the two are the pair an agreement marks.
Change = Callable[[int, Actor, int, int], tuple[Battle, BattleEvent | None]]


@dataclass(frozen=True)
class Ruling:
    """One of the three rulings, as these tests ask it.

    Attributes:
        name: What the ruling is called in a failure.
        change: Makes the ruling for two figures of a battle.
        battle_field: The ``Battle`` field that holds its record.
        payload_key: The key its log line's payload carries the record under.
        log_sentence: The sentence its log line is written in, under the
            fixed words.
        game_master_only: Its refusal for anyone but the fight's Game Master,
            under the fixed words.
        refuse_classic: Its refusal for a classic battle.
        refuse_combatant: Its refusal for a figure not in the battle.
    """

    name: str
    change: Change
    battle_field: str
    payload_key: str
    log_sentence: str
    game_master_only: str
    refuse_classic: str
    refuse_combatant: str

    def recorded(self, battle: Battle) -> object:
        """The ruling's record as the database holds it."""
        return getattr(Battle.objects.get(pk=battle.pk), self.battle_field)


def mark_the_pair(
    battle_id: int, actor: Actor, first_id: int, second_id: int
) -> tuple[Battle, BattleEvent | None]:
    return set_hth_agreement(battle_id, actor, agreed=True, pair=(first_id, second_id))


def lift_channel(
    battle_id: int, actor: Actor, first_id: int, second_id: int
) -> tuple[Battle, BattleEvent | None]:
    return set_magic_bounds_lifted(
        battle_id,
        actor,
        combatant_id=first_id,
        bounds=[magic_bounds.CHANNEL],
        lifted=True,
    )


def grant_the_working(
    battle_id: int, actor: Actor, first_id: int, second_id: int
) -> tuple[Battle, BattleEvent | None]:
    return set_working_granted(
        battle_id, actor, combatant_id=first_id, working_key=FIRST_TIER, granted=True
    )


RULINGS = (
    Ruling(
        name="hand-to-hand by agreement",
        change=mark_the_pair,
        battle_field="hth_agreement_json",
        payload_key="hth_agreement",
        log_sentence="hth_agreement.log_pair_marked",
        game_master_only="hth_agreement.game_master_only",
        refuse_classic="hth_agreement.refuse_classic",
        refuse_combatant="hth_agreement.refuse_pair",
    ),
    Ruling(
        name="magic bounds lifted",
        change=lift_channel,
        battle_field="magic_bounds_lifted_json",
        payload_key="magic_bounds_lifted",
        log_sentence="magic_bounds.log_lifted",
        game_master_only="magic_bounds.game_master_only",
        refuse_classic="magic_bounds.refuse_classic",
        refuse_combatant="magic_bounds.refuse_combatant",
    ),
    Ruling(
        name="a working granted",
        change=grant_the_working,
        battle_field="workings_granted_json",
        payload_key="workings_granted",
        log_sentence="working_grants.log_granted",
        game_master_only="working_grants.game_master_only",
        refuse_classic="working_grants.refuse_classic",
        refuse_combatant="working_grants.refuse_combatant",
    ),
)


@dataclass(frozen=True)
class Fight:
    """A battle and the two figures a ruling is made for: a player's seated
    True mage, whom all three rulings can name, and a generated fighter."""

    battle: Battle
    mage_id: int
    fighter_id: int


@with_fixed_words
class RulingStepTest(BaseTestCase):
    """Each step, under each of the three services."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(seated_rules(DEFAULT_MAGIC_RULES))
        self.dm = self.create_dm()
        self.game_master = Actor(user=self.dm, guest_key="")
        self.players_by_seat: dict[int, User] = {}

    def lobby(self) -> Fight:
        """A lobby the Game Master opened, a player's True mage seated in it."""
        fights_made = len(self.players_by_seat)
        player = self.create_player(username=f"player-{fights_made}")
        battle, seat = lobby_with_a_true_mage_seated(
            self.dm, player, seed=11 + fights_made
        )
        fighter = battle.combatants.exclude(pk=seat.pk).get()
        self.players_by_seat[seat.pk] = player
        return Fight(battle=battle, mage_id=seat.pk, fighter_id=fighter.pk)

    def awaited(self) -> Fight:
        """A started fight, paused on the seated player's move."""
        fight = self.lobby()
        seat = fight.battle.combatants.get(pk=fight.mage_id)
        start_with_the_seat_ready(
            fight.battle, seat, self.players_by_seat[fight.mage_id]
        )
        battle, _events = advance_turn(fight.battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(battle.pending_input_json["combatant_id"], fight.mage_id)
        return Fight(battle=battle, mage_id=fight.mage_id, fighter_id=fight.fighter_id)

    def assert_untouched(self, ruling: Ruling, fight: Fight) -> None:
        """Nothing was written: the record, the pause and the log stand."""
        battle = Battle.objects.get(pk=fight.battle.pk)
        self.assertFalse(ruling.recorded(battle))
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(battle.pending_input_json, fight.battle.pending_input_json)
        self.assertEqual(battle.events.count(), 0)

    def test_only_the_fights_game_master_makes_a_ruling(self) -> None:
        bystander = Actor(user=self.create_dm(username="bystander"), guest_key="")
        for ruling in RULINGS:
            fight = self.awaited()
            player = Actor(user=self.players_by_seat[fight.mage_id], guest_key="")
            for who, actor in (("player", player), ("bystander", bystander)):
                with (
                    self.subTest(ruling=ruling.name, who=who),
                    self.assertRaisesMessage(ValueError, ruling.game_master_only),
                ):
                    ruling.change(
                        fight.battle.pk, actor, fight.mage_id, fight.fighter_id
                    )
            self.assert_untouched(ruling, fight)

    def test_a_classic_battle_is_refused_in_the_rulings_own_words(self) -> None:
        classic = create_battle(
            self.dm, name="Melee", count=2, seed=3, rules_profile="classic-melee"
        )
        first_id, second_id = classic.combatants.values_list("pk", flat=True)
        for ruling in RULINGS:
            with (
                self.subTest(ruling=ruling.name),
                self.assertRaisesMessage(ValueError, ruling.refuse_classic),
            ):
                ruling.change(classic.pk, self.game_master, first_id, second_id)
            self.assertFalse(ruling.recorded(classic))

    def test_on_a_classic_battle_anyone_else_is_told_it_is_the_game_masters(
        self,
    ) -> None:
        # Two faults at once: not the fight's Game Master, and a classic
        # battle. Whose ruling it is comes first; what kind of battle it is,
        # is the Game Master's to be told.
        classic = create_battle(
            self.dm, name="Melee", count=2, seed=3, rules_profile="classic-melee"
        )
        first_id, second_id = classic.combatants.values_list("pk", flat=True)
        player = Actor(user=self.create_player(username="onlooker"), guest_key="")
        bystander = Actor(user=self.create_dm(username="bystander"), guest_key="")
        for ruling in RULINGS:
            for who, actor in (("player", player), ("bystander", bystander)):
                with (
                    self.subTest(ruling=ruling.name, who=who),
                    self.assertRaisesMessage(ValueError, ruling.game_master_only),
                ):
                    ruling.change(classic.pk, actor, first_id, second_id)
            self.assertFalse(ruling.recorded(classic))

    def test_a_classic_battle_is_refused_before_its_figures_are_looked_for(
        self,
    ) -> None:
        # Two faults at once: a classic battle, and a figure that is not in
        # it. A classic battle takes no such ruling for any figure.
        classic = create_battle(
            self.dm, name="Melee", count=2, seed=3, rules_profile="classic-melee"
        )
        own_id = classic.combatants.values_list("pk", flat=True)[0]
        elsewhere = create_battle(self.dm, name="Elsewhere", count=2, seed=9)
        stranger_id = elsewhere.combatants.values_list("pk", flat=True)[0]
        for ruling in RULINGS:
            with (
                self.subTest(ruling=ruling.name),
                self.assertRaisesMessage(ValueError, ruling.refuse_classic),
            ):
                ruling.change(classic.pk, self.game_master, stranger_id, own_id)
            self.assertFalse(ruling.recorded(classic))

    def test_a_figure_not_in_the_battle_is_refused_in_the_rulings_own_words(
        self,
    ) -> None:
        elsewhere = create_battle(self.dm, name="Elsewhere", count=2, seed=9)
        stranger_id = elsewhere.combatants.values_list("pk", flat=True)[0]
        for ruling in RULINGS:
            fight = self.awaited()
            with (
                self.subTest(ruling=ruling.name),
                self.assertRaisesMessage(ValueError, ruling.refuse_combatant),
            ):
                ruling.change(
                    fight.battle.pk, self.game_master, stranger_id, fight.mage_id
                )
            self.assert_untouched(ruling, fight)

    def test_a_ruling_is_recorded_reopens_the_awaited_menu_and_is_logged_once(
        self,
    ) -> None:
        for ruling in RULINGS:
            with self.subTest(ruling=ruling.name):
                fight = self.awaited()
                next_sequence = fight.battle.state_json["next_sequence"]
                battle, event = ruling.change(
                    fight.battle.pk, self.game_master, fight.mage_id, fight.fighter_id
                )
                # The record is written and saved.
                recorded = ruling.recorded(battle)
                self.assertTrue(recorded)
                self.assertEqual(getattr(battle, ruling.battle_field), recorded)
                # The pause on the player's move is cleared, and saved so.
                saved = Battle.objects.get(pk=battle.pk)
                for held in (battle, saved):
                    self.assertEqual(held.status, Battle.STATUS_PAUSED)
                    self.assertEqual(held.pending_input_json, {})
                # One line in the log, numbered as the engine's next would
                # be, carrying the record; the board's counter moves past it.
                assert event is not None
                self.assertEqual(list(saved.events.all()), [event])
                self.assertEqual(
                    (event.event_type, event.sequence, event.turn),
                    ("status", next_sequence, saved.state_json["turn"]),
                )
                self.assertTrue(
                    event.message.startswith(f"{ruling.log_sentence} "), event.message
                )
                self.assertEqual(event.payload[ruling.payload_key], recorded)
                self.assertEqual(saved.state_json["next_sequence"], next_sequence + 1)

    def test_a_ruling_that_already_stands_changes_nothing_and_keeps_the_pause(
        self,
    ) -> None:
        for ruling in RULINGS:
            with self.subTest(ruling=ruling.name):
                fight = self.awaited()
                ruling.change(
                    fight.battle.pk, self.game_master, fight.mage_id, fight.fighter_id
                )
                awaiting, _events = advance_turn(fight.battle.pk)
                self.assertEqual(awaiting.status, Battle.STATUS_AWAITING)
                recorded = ruling.recorded(awaiting)
                battle, event = ruling.change(
                    fight.battle.pk, self.game_master, fight.mage_id, fight.fighter_id
                )
                self.assertIsNone(event)
                saved = Battle.objects.get(pk=battle.pk)
                for held in (battle, saved):
                    self.assertEqual(held.status, Battle.STATUS_AWAITING)
                    self.assertEqual(
                        held.pending_input_json, awaiting.pending_input_json
                    )
                self.assertEqual(ruling.recorded(saved), recorded)
                self.assertEqual(saved.events.count(), 1)

    def test_a_battle_with_no_log_yet_records_the_ruling_and_files_no_line(
        self,
    ) -> None:
        for ruling in RULINGS:
            with self.subTest(ruling=ruling.name):
                fight = self.lobby()
                self.assertEqual(fight.battle.status, Battle.STATUS_LOBBY)
                battle, event = ruling.change(
                    fight.battle.pk, self.game_master, fight.mage_id, fight.fighter_id
                )
                self.assertIsNone(event)
                self.assertTrue(ruling.recorded(battle))
                self.assertEqual(battle.events.count(), 0)
                self.assertEqual(battle.status, Battle.STATUS_LOBBY)
