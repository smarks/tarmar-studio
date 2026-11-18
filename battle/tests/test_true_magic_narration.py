"""True Magic in the battle log, in the configured words (tarmar-studio #208).

The engine logs every event of a turn with a line of its own. The events True
Magic brings (tarmar-engine #77) are reworded by this app as the turn hands
them back (:func:`battle.true_magic_narration.narrated`, from
``TarmarProfile.run_turn``), in the words of ``config/battle-rulings.toml``'s
``[true_magic_log]`` table: a craft's beginning and each share, the spells
that end when it begins, the roll to keep it through a hit with its target and
its band, a working lost by each ending, the working's roll when its craft is
whole, a hand-back, an effect the Game Master describes with its tier and its
magnitude, the drain and a collapse from it, and a True cast.

Every turn here is played on the default canon (its workings Sear, Knit and
Figment), through the profile as a battle runs it
(:mod:`battle.tests.true_magic_play`), under
:data:`~.rulings_words.FIXED_WORDS`: each sentence is its key followed by what
filled it, in the order of the names it may use.
"""

from dataclasses import replace
from unittest.mock import patch

from tarmar_engine.engine import CRAFT_LOSS_REASONS, DRAIN_REASON
from tarmar_engine.engine import run_turn as run_engine_turn
from tarmar_engine.magic import CastingEnding, MagicRules
from tarmar_engine.policy import choose_option
from tarmar_engine.state import RunawayState

from battle import magic_bounds, profiles, true_magic_narration
from battle.engine.state import BattleState
from battle.models import Battle

from . import true_magic_play as play
from .magic_fixtures import DEFAULT_MAGIC_RULES, seated_rules
from .rulings_words import FIXED_WORDS, with_fixed_words
from .test_magic_narration import BothReadersTestCase, as_row
from .test_working_grants import FIRST_AT, DefaultCanonTestCase, record_granting

#: The craft of Sear at its first tier, aimed at the foe: one mana over three
#: turns, so its shares are 0, 0 and 1. And the same craft with two mana
#: pushed into it: three mana, a share of 1 each turn.
SEAR = play.craft(play.SEAR, play.FOE)
PUSHED_SEAR = play.craft(play.SEAR, play.FOE, push=2)
BORROWING_SEAR = play.craft(play.SEAR, play.FOE, push=2, borrow=True)

#: A foe who may counter, and whose counter a low roll of the mage's loses to.
COUNTERING_FOE = {
    "spells": ["counter_1"],
    "spell_skill_levels": {"counter_1": 1},
    "mana": 6,
    "max_mana": 6,
    "intelligence": 16,
}
#: The opposed roll's dice: the mage's first, then the foe's.
COUNTER_WINS = {"counter": [(1, 1), (10, 10)]}

#: The battle whose Game Master has granted the mage Figment's first tier.
FIGMENT_GRANTED = {
    "workings_granted_json": record_granting(play.MAGE, play.FIGMENT).to_json()
}

#: The keys the engine writes on True Magic's events alone (its README,
#: "Events"), written here a second time on purpose: the test that no such
#: event keeps the engine's line must not read the list the code reads.
TRUE_MAGIC_KEYS = frozenset(
    {
        "craft",
        "crafting",
        "craft_interrupted",
        "craft_lost",
        "working",
        "working_effect",
        "true_cast",
    }
)

#: How a collapse the drain brought opens, under the fixed words.
COLLAPSE = "true_magic_log.drain_collapse"

#: A casting roll that fails and keeps its mana, one that fails and loses it
#: (the default canon's ``mana_lost_totals``), under the fumble floor of 13.
FAILS_KEPT = {"casting": [(5, 5, 5)]}
FAILS_SPENT = {"casting": [(5, 4, 4)]}


def lines(events: list[dict], sentence: str) -> list[str]:
    """The messages among ``events`` that one ``[true_magic_log]`` sentence
    wrote, under the fixed words."""
    opening = f"true_magic_log.{sentence} "
    return [
        event["message"]
        for event in events
        if f"{event['message']} ".startswith(opening)
    ]


def diced_figment() -> MagicRules:
    """The default canon's rules, its narrated working rolling 2d6 a tier:
    the default canon's own has no dice, so no magnitude."""
    return replace(
        DEFAULT_MAGIC_RULES,
        true_magic=replace(
            play.TRUE_MAGIC,
            effects=tuple(
                replace(effect, dice_per_tier="2d6") if effect.kind is None else effect
                for effect in play.TRUE_MAGIC.effects
            ),
        ),
    )


@with_fixed_words
class CraftActionTest(DefaultCanonTestCase):
    def test_a_craft_begins_and_goes_on_a_share_a_turn(self) -> None:
        events = play.play(
            play.board(), [{play.MAGE: PUSHED_SEAR, play.FOE: play.idle}] * 2
        )
        # channel, gathered, mana_total, name, share, turn, turns, working;
        # the share is mana_left then share.
        self.assertEqual(
            lines(events, "craft_begun"),
            [
                "true_magic_log.craft_begun 6 1 3 "
                "Mage true_magic_log.share_paid 9 1 1 3 Sear (tier 1)"
            ],
        )
        self.assertEqual(
            lines(events, "craft_continued"),
            [
                "true_magic_log.craft_continued 6 2 3 "
                "Mage true_magic_log.share_paid 8 1 2 3 Sear (tier 1)"
            ],
        )

    def test_a_turn_with_no_share_due_says_so(self) -> None:
        events = play.play_turn(play.board(), {play.MAGE: SEAR, play.FOE: play.idle})
        self.assertEqual(
            lines(events, "craft_begun"),
            [
                "true_magic_log.craft_begun 6 0 1 "
                "Mage true_magic_log.share_none 10 1 3 Sear (tier 1)"
            ],
        )

    def test_a_borrowed_share_names_what_it_borrowed_and_the_debt(self) -> None:
        # One mana in the pool: the first share is paid, the second borrowed.
        events = play.play(
            play.board(mage={"mana": 1}),
            [{play.MAGE: BORROWING_SEAR, play.FOE: play.idle}] * 2,
        )
        # borrowed, debt, mana_left, share.
        self.assertEqual(
            lines(events, "craft_continued"),
            [
                "true_magic_log.craft_continued 6 2 3 "
                "Mage true_magic_log.share_borrowed 1 1 0 1 2 3 Sear (tier 1)"
            ],
        )

    def test_the_spells_kept_up_end_when_a_craft_begins(self) -> None:
        events = play.play_turn(
            play.board(mage={"active_spells": ["shield"]}),
            {play.MAGE: SEAR, play.FOE: play.idle},
        )
        self.assertEqual(
            lines(events, "spell_let_go"), ["true_magic_log.spell_let_go Mage Shield"]
        )


@with_fixed_words
class InterruptionTest(DefaultCanonTestCase):
    """The roll to keep a craft through a hit: 2d10 at or under WIS 14 less 4
    a mana gathered."""

    def hit(self, craft: play.Pick, concentration: tuple[int, int]) -> list[dict]:
        """One turn in which the craft begins and the foe's blow lands."""
        return play.play_turn(
            play.board(foe=play.BESIDE),
            {play.MAGE: craft, play.FOE: play.ATTACK},
            {**play.HIT, "concentration": [concentration]},
        )

    def test_a_roll_that_holds_names_its_total_and_its_target(self) -> None:
        events = self.hit(PUSHED_SEAR, (1, 1))
        # name, natural, target, working.
        self.assertEqual(
            lines(events, "craft_kept"),
            ["true_magic_log.craft_kept Mage 2 10 Sear (tier 1)"],
        )
        self.assertEqual(lines(events, "lost_interrupted"), [])

    def test_a_failure_names_its_band_as_the_canon_labels_it(self) -> None:
        events = self.hit(PUSHED_SEAR, (6, 6))
        # band, margin, name, natural, target, working.
        self.assertEqual(
            lines(events, "craft_broken"),
            ["true_magic_log.craft_broken Scatter 2 Mage 12 10 Sear (tier 1)"],
        )
        # mana_spent, name, working.
        self.assertEqual(
            lines(events, "lost_interrupted"),
            ["true_magic_log.lost_interrupted 1 Mage Sear (tier 1)"],
        )

    def test_a_runaway_band_says_the_working_runs_away(self) -> None:
        # A natural 20 is a Runaway whatever the target.
        events = self.hit(PUSHED_SEAR, (10, 10))
        self.assertEqual(
            lines(events, "craft_broken_runaway"),
            [
                "true_magic_log.craft_broken_runaway Breakaway 10 Mage 20 10 Sear (tier 1)"
            ],
        )
        self.assertEqual(lines(events, "craft_broken"), [])

    def test_a_runaway_band_with_nothing_gathered_does_not_run_away(self) -> None:
        events = self.hit(SEAR, (10, 10))
        self.assertEqual(
            lines(events, "craft_broken"),
            ["true_magic_log.craft_broken Breakaway 6 Mage 20 14 Sear (tier 1)"],
        )
        self.assertEqual(lines(events, "craft_broken_runaway"), [])


@with_fixed_words
class LostWorkingTest(DefaultCanonTestCase):
    """A craft lost by each ending the engine loses one by: mana_spent, name,
    working, and for a countered one the defenders before them."""

    def test_every_ending_a_craft_is_lost_by_has_its_sentence(self) -> None:
        sentences = true_magic_narration.lost_sentences(FIXED_WORDS.true_magic_log)
        self.assertEqual(set(sentences), set(CRAFT_LOSS_REASONS))
        self.assertEqual(
            {ending: sentence.split()[0] for ending, sentence in sentences.items()},
            {
                ending: f"true_magic_log.lost_{ending.value}"
                for ending in CRAFT_LOSS_REASONS
            },
        )

    def test_abandoned_for_another_action(self) -> None:
        events = play.play(
            play.board(),
            [
                {play.MAGE: SEAR, play.FOE: play.idle},
                {play.MAGE: play.idle, play.FOE: play.idle},
            ],
        )
        self.assertEqual(
            lines(events, "lost_abandoned"),
            ["true_magic_log.lost_abandoned 0 Mage Sear (tier 1)"],
        )

    def test_blocked_where_no_working_may_be_crafted(self) -> None:
        # In hand-to-hand with a sword in hand the continuation is withheld,
        # and whatever the mage does instead loses the working, blocked.
        state = play.board(foe=play.BESIDE)
        play.play_turn(state, {play.MAGE: SEAR, play.FOE: play.idle})
        state.by_id(play.MAGE).hth_with = [play.FOE]
        state.by_id(play.FOE).hth_with = [play.MAGE]
        events = play.play_turn(state, {play.MAGE: play.letter("t")})
        self.assertEqual(
            lines(events, "lost_blocked"),
            ["true_magic_log.lost_blocked 0 Mage Sear (tier 1)"],
        )

    def test_unpaid_when_the_pool_cannot_pay_a_share(self) -> None:
        state = play.board()
        play.play_turn(state, {play.MAGE: PUSHED_SEAR, play.FOE: play.idle})
        state.by_id(play.MAGE).mana = 0
        events = play.play_turn(state, {play.MAGE: PUSHED_SEAR, play.FOE: play.idle})
        self.assertEqual(
            lines(events, "lost_unpaid"),
            ["true_magic_log.lost_unpaid 1 Mage Sear (tier 1)"],
        )

    def test_unconscious_when_a_blow_fells_the_crafter(self) -> None:
        events = play.play_turn(
            play.board(mage={"fatigue": 3}, foe=play.BESIDE),
            {play.MAGE: SEAR, play.FOE: play.ATTACK},
            {**play.HIT, "damage": [(6, 6)]},
        )
        self.assertEqual(
            lines(events, "lost_unconscious"),
            ["true_magic_log.lost_unconscious 0 Mage Sear (tier 1)"],
        )

    def test_grappled_when_a_grapple_takes_hold(self) -> None:
        events = play.play_turn(
            play.board(foe=play.BESIDE),
            {play.MAGE: SEAR, play.FOE: play.letter("o")},
            battle=Battle(hth_agreement_json={"whole_battle": True, "pairs": []}),
        )
        self.assertEqual(
            lines(events, "lost_grappled"),
            ["true_magic_log.lost_grappled 0 Mage Sear (tier 1)"],
        )

    def test_countered_names_who_countered_it(self) -> None:
        events = play.play(
            play.board(foe=COUNTERING_FOE),
            [{play.MAGE: SEAR, play.FOE: play.idle}] * 2
            + [{play.MAGE: SEAR, play.FOE: play.counter}],
            {2: COUNTER_WINS},
        )
        # defenders, mana_spent, name, working.
        self.assertEqual(
            lines(events, "lost_countered"),
            ["true_magic_log.lost_countered Foe 1 Mage Sear (tier 1)"],
        )
        # The default canon drains 7 a mana for a countered working.
        self.assertEqual(
            lines(events, "drain"), ["true_magic_log.drain 7 33 Mage fatigue"]
        )


@with_fixed_words
class CompletionTest(DefaultCanonTestCase):
    """A working's roll when its craft is whole: 3d6 at or under WIS 14 and 5
    a level of the True skill, 24 for this mage."""

    def completed(self, craft: play.Pick = SEAR, **keywords) -> list[dict]:
        """The three turns of the craft, the foe standing by."""
        faces = keywords.pop("faces", None)
        return play.play(
            play.board(**keywords),
            [{play.MAGE: craft, play.FOE: play.idle}] * 3,
            None if faces is None else {2: faces},
        )

    def test_a_success_reads_its_roll_and_the_mana_gathered(self) -> None:
        events = self.completed()
        # channel, mana, mana_left, name, roll, working; the roll is dice,
        # target, total, versus.
        self.assertEqual(
            lines(events, "completed"),
            [
                "true_magic_log.completed 6 1 9 Mage "
                "true_magic_log.casting_roll 3d6 24 9 WIS+Arcane Sear (tier 1)"
            ],
        )

    def test_a_pushed_working_counts_its_pushed_mana(self) -> None:
        events = self.completed(PUSHED_SEAR)
        self.assertEqual(
            lines(events, "completed"),
            [
                "true_magic_log.completed 6 3 7 Mage "
                "true_magic_log.casting_roll 3d6 24 9 WIS+Arcane Sear (tier 1)"
            ],
        )

    def test_a_failure_that_keeps_the_mana_hands_every_share_back(self) -> None:
        events = self.completed(faces=FAILS_KEPT)
        # channel, mana_left, mana_returned, name, roll, working.
        self.assertEqual(
            lines(events, "failed_handed_back"),
            [
                "true_magic_log.failed_handed_back 6 "
                "10 1 Mage true_magic_log.casting_roll 3d6 24 15 WIS+Arcane "
                "Sear (tier 1)"
            ],
        )
        self.assertEqual(lines(events, "completed") + lines(events, "drain"), [])

    def test_a_hand_back_pays_off_what_the_craft_borrowed(self) -> None:
        # One mana paid and two borrowed: one goes back to the pool and two
        # come off the debt. debt, debt_returned, line.
        events = self.completed(BORROWING_SEAR, mage={"mana": 1}, faces=FAILS_KEPT)
        self.assertEqual(
            lines(events, "debt_handed_back"),
            [
                "true_magic_log.debt_handed_back 0 2 "
                "true_magic_log.failed_handed_back 6 "
                "1 1 Mage true_magic_log.casting_roll 3d6 24 15 WIS+Arcane "
                "Sear (tier 1)"
            ],
        )

    def test_a_failure_on_a_total_that_loses_the_mana_spends_it(self) -> None:
        events = self.completed(faces=FAILS_SPENT)
        # channel, mana, mana_left, name, roll, working.
        self.assertEqual(
            lines(events, "failed_spent"),
            [
                "true_magic_log.failed_spent 6 1 9 "
                "Mage true_magic_log.casting_roll 3d6 24 13 WIS+Arcane Sear (tier 1)"
            ],
        )
        self.assertEqual(lines(events, "failed_handed_back"), [])


@with_fixed_words
class EffectAndDrainTest(DefaultCanonTestCase):
    def figment(self, target: int | None) -> list[dict]:
        """Figment's first tier crafted whole, naming ``target``."""
        return play.play(
            play.board(),
            [{play.MAGE: play.craft(play.FIGMENT, target), play.FOE: play.idle}] * 3,
            battle=Battle(**FIGMENT_GRANTED),
        )

    def test_a_narrated_effect_gives_its_tier_for_the_game_master(self) -> None:
        # magnitude, name, target, tier, working; Figment rolls no dice, so
        # its magnitude clause is empty.
        self.assertEqual(
            lines(self.figment(play.FOE), "narrated_naming"),
            ["true_magic_log.narrated_naming  Mage Foe 1 Figment (tier 1)"],
        )
        # magnitude, name, tier, working.
        self.assertEqual(
            lines(self.figment(None), "narrated"),
            ["true_magic_log.narrated  Mage 1 Figment (tier 1)"],
        )

    def test_a_narrated_effect_with_dice_gives_its_magnitude(self) -> None:
        with seated_rules(diced_figment()):
            events = self.figment(None)
        # 2d6 on its middle faces is 6.
        self.assertEqual(
            lines(events, "narrated"),
            [
                "true_magic_log.narrated true_magic_log.magnitude_clause 6 Mage 1 "
                "Figment (tier 1)"
            ],
        )

    def test_a_working_that_goes_off_drains_its_caster(self) -> None:
        events = play.play(play.board(), [{play.MAGE: SEAR, play.FOE: play.idle}] * 3)
        # cost, left, name, pool: 8 Fatigue a mana, of the mage's 40.
        self.assertEqual(
            lines(events, "drain"), ["true_magic_log.drain 8 32 Mage fatigue"]
        )
        self.assertEqual(lines(events, "drain_collapse"), [])

    def test_a_drain_that_fells_the_caster_says_so(self) -> None:
        events = play.play(
            play.board(mage={"fatigue": 8}),
            [{play.MAGE: SEAR, play.FOE: play.idle}] * 3,
        )
        self.assertEqual(
            lines(events, "drain"), ["true_magic_log.drain 8 0 Mage fatigue"]
        )
        self.assertEqual(
            lines(events, "drain_collapse"), ["true_magic_log.drain_collapse Mage"]
        )

    def test_a_collapse_from_a_blow_is_not_a_collapse_from_the_drain(self) -> None:
        events = play.play_turn(
            play.board(mage={"fatigue": 3}, foe=play.BESIDE),
            {play.MAGE: SEAR, play.FOE: play.ATTACK},
            {**play.HIT, "damage": [(6, 6)]},
        )
        self.assertEqual(lines(events, "drain_collapse"), [])
        self.assertIn(
            "Mage collapses unconscious", [event["message"] for event in events]
        )

    def test_a_healing_working_names_the_teammate_it_restores(self) -> None:
        events = play.play(
            play.board(friend=True),
            [
                {
                    play.MAGE: play.craft(play.KNIT, play.FRIEND),
                    play.FOE: play.idle,
                    play.FRIEND: play.idle,
                }
            ]
            * 3,
        )
        # healed, left, name, pool, target: 1d4+1 on its middle face is 3.
        self.assertEqual(
            lines(events, "healed_teammate"),
            ["true_magic_log.healed_teammate 3 13 Mage body Friend"],
        )


@with_fixed_words
class TrueCastTest(DefaultCanonTestCase):
    def test_a_true_cast_is_marked_around_the_casts_own_line(self) -> None:
        events = play.play_turn(
            play.board(mage=play.MISSILE_MAGE),
            {play.MAGE: play.true_cast("fire_missile", push=1), play.FOE: play.idle},
        )
        (marked,) = lines(events, "true_cast")
        self.assertTrue(
            marked.startswith("true_magic_log.true_cast Mage casts Fire Missile "),
            marked,
        )
        # It drains for the mana it pushed: 8 Fatigue.
        self.assertEqual(
            lines(events, "drain"), ["true_magic_log.drain 8 32 Mage fatigue"]
        )

    def test_a_recipe_cast_keeps_the_engines_line(self) -> None:
        events = play.play_turn(
            play.board(mage=play.MISSILE_MAGE),
            {play.MAGE: play.recipe_cast("fire_missile"), play.FOE: play.idle},
        )
        self.assertEqual(lines(events, "true_cast"), [])
        self.assertTrue(
            any(
                event["message"].startswith("Mage casts Fire Missile ")
                for event in events
            )
        )

    def test_a_countered_true_cast_is_marked_too(self) -> None:
        events = play.play_turn(
            play.board(mage=play.MISSILE_MAGE, foe=COUNTERING_FOE),
            {
                play.MAGE: play.true_cast("fire_missile", push=1),
                play.FOE: play.counter,
            },
            COUNTER_WINS,
        )
        (marked,) = lines(events, "true_cast")
        self.assertTrue(
            marked.startswith(
                "true_magic_log.true_cast Mage's Fire Missile is countered by Foe"
            ),
            marked,
        )


#: Lines of the engine's that sit one predicate from a line of True Magic's,
#: each brought by a scenario of :func:`every_scenario`: a spell kept up that
#: ends with no craft begun, a recipe Heal, and Knit restoring its own caster,
#: which the engine logs as it logs any healing of oneself.
RECIPE_LINES = (
    "Mage cannot pay for Shield; it ends",
    "Mage heals 3 fatigue (33/40)",
    "Mage heals 3 body (13/27)",
)


def told_beside_emitted(
    state: BattleState, turns: list[dict], faces: dict, battle: Battle
) -> list[tuple[dict, dict]]:
    """Play the turns as a battle runs them, and pair each event as the
    profile hands it back with the same event as the engine emitted it."""
    emitted: list[dict] = []
    narrate = true_magic_narration.narrated

    def keeping(board: BattleState, events: list[dict]) -> list[dict]:
        emitted.extend(events)
        return narrate(board, events)

    with patch.object(true_magic_narration, "narrated", keeping):
        told = play.play(state, turns, faces, battle=battle)
    return list(zip(told, emitted, strict=True))


def every_scenario() -> list[tuple[BattleState, list[dict], dict, dict]]:
    """Boards, turns, dice and battle fields that between them bring every
    event True Magic has, and the events of other castings that most
    resemble them (:data:`RECIPE_LINES`): ``(state, turns, faces by turn,
    battle fields)``."""
    idle = {play.MAGE: SEAR, play.FOE: play.idle}
    nobody_acts = {play.MAGE: play.idle, play.FOE: play.idle}
    return [
        # A spell kept up that ends because its caster cannot renew it: an
        # ended spell, and no craft began.
        (
            play.board(mage={"active_spells": ["shield"], "mana": 0}),
            [nobody_acts],
            {},
            {},
        ),
        # A recipe Heal, and Knit naming its own caster: healing that names
        # no other figure.
        (
            play.board(
                mage={
                    "spells": ["heal"],
                    "spell_skill_levels": {"heal": 1},
                    "fatigue": 30,
                }
            ),
            [{play.MAGE: play.recipe_cast("heal"), play.FOE: play.idle}],
            {},
            {},
        ),
        (
            play.board(mage={"body": 10}),
            [{play.MAGE: play.craft(play.KNIT, play.MAGE), play.FOE: play.idle}] * 3,
            {},
            {},
        ),
        (play.board(mage={"active_spells": ["shield"]}), [idle] * 3, {}, {}),
        (
            play.board(mage={"mana": 1}),
            [{play.MAGE: BORROWING_SEAR, play.FOE: play.idle}] * 3,
            {2: FAILS_KEPT},
            {},
        ),
        (play.board(), [idle] * 3, {2: FAILS_SPENT}, {}),
        (play.board(mage={"fatigue": 8}), [idle] * 3, {}, {}),
        (
            play.board(foe=play.BESIDE),
            [{play.MAGE: PUSHED_SEAR, play.FOE: play.ATTACK}] * 2,
            {
                0: {**play.HIT, "concentration": [(1, 1)]},
                1: {**play.HIT, "concentration": [(10, 10)]},
            },
            {},
        ),
        (play.board(), [idle, nobody_acts], {}, {}),
        (
            play.board(foe=COUNTERING_FOE),
            [idle, idle, {play.MAGE: SEAR, play.FOE: play.counter}],
            {2: COUNTER_WINS},
            {},
        ),
        (
            play.board(),
            [{play.MAGE: play.craft(play.FIGMENT, play.FOE), play.FOE: play.idle}] * 3,
            {},
            FIGMENT_GRANTED,
        ),
        (
            play.board(friend=True),
            [
                {
                    play.MAGE: play.craft(play.KNIT, play.FRIEND),
                    play.FOE: play.idle,
                    play.FRIEND: play.idle,
                }
            ]
            * 3,
            {},
            {},
        ),
        (
            play.board(mage=play.MISSILE_MAGE, foe=COUNTERING_FOE),
            [
                {
                    play.MAGE: play.true_cast("fire_missile", push=1),
                    play.FOE: play.counter,
                },
                {
                    play.MAGE: play.true_cast("fire_missile", push=1),
                    play.FOE: play.idle,
                },
            ],
            {0: COUNTER_WINS},
            {},
        ),
    ]


@with_fixed_words
class NoEventFallsThroughTest(DefaultCanonTestCase):
    """Every event True Magic brings reads in the configured words, and no
    other event is touched."""

    def is_true_magics(self, event: dict) -> bool:
        """Does the engine's README file this event under True Magic?"""
        payload = event["payload"]
        return bool(
            TRUE_MAGIC_KEYS & {key for key, value in payload.items() if value}
            or payload.get("reason") == DRAIN_REASON
            or ("healed" in payload and "target" in payload)
        )

    def test_every_true_magic_event_is_worded_and_no_other_is(self) -> None:
        seen: set[str] = set()
        untouched: set[str] = set()
        for state, turns, faces, battle_fields in every_scenario():
            for event, emitted in told_beside_emitted(
                state, turns, faces, Battle(**battle_fields)
            ):
                message = event["message"]
                with self.subTest(message=message):
                    if self.is_true_magics(event):
                        self.assertRegex(message, r"^true_magic_log\.\w+( |$)")
                        seen.add(message.split()[0])
                    elif message.startswith(COLLAPSE):
                        # A collapse carries no key of True Magic's: it is the
                        # drain's because it comes straight after the drain.
                        seen.add(COLLAPSE)
                    else:
                        # Handed back as the engine emitted it: the very event.
                        self.assertIs(event, emitted)
                        untouched.add(message)
                    # Rewording changes an event's message and nothing else.
                    self.assertEqual({**event, "message": emitted["message"]}, emitted)
        self.assertLessEqual(set(RECIPE_LINES), untouched)
        # Between them the scenarios bring every sentence that opens a line.
        opening = {
            f"true_magic_log.{name}"
            for name in vars(FIXED_WORDS.true_magic_log)
            if name
            not in {
                # Clauses and wrapped lines, which fill another sentence.
                "share_none",
                "share_paid",
                "share_borrowed",
                "casting_roll",
                "magnitude_clause",
                "failed_handed_back",
                # Endings LostWorkingTest plays one by one, and the narrated
                # effect with nobody named, which EffectAndDrainTest plays.
                "lost_blocked",
                "lost_unpaid",
                "lost_unconscious",
                "lost_grappled",
                "narrated",
                "craft_broken",
            }
        }
        self.assertEqual(seen, opening)

    def test_an_event_that_is_not_true_magics_is_handed_back_untouched(self) -> None:
        # A turn with no True mage in it, so nothing of True Magic's happens.
        state = play.board(mage={"skill_levels": {}})
        engine_events: list[dict] = []
        run_engine_turn(
            BattleState.from_dict(state.to_dict()),
            play.PurposeRoller({}),
            engine_events.append,
            lambda board, actor: choose_option(
                board, actor, magic=profiles.tarmar_engine_profile().magic
            ),
            profile=profiles.tarmar_engine_profile(),
        )
        told = true_magic_narration.narrated(state, engine_events)
        self.assertEqual(told, engine_events)
        for narrated, emitted in zip(told, engine_events, strict=True):
            self.assertIs(narrated, emitted)

    def test_a_canon_with_no_true_magic_rewords_nothing(self) -> None:
        events = [
            {
                "event_type": "action",
                "actor": "Mage",
                "sequence": 1,
                "payload": {"craft": "begun"},
                "message": "as the engine wrote it",
            }
        ]
        with seated_rules(replace(DEFAULT_MAGIC_RULES, true_magic=None)):
            self.assertIs(true_magic_narration.narrated(play.board(), events), events)

    def test_another_figures_event_after_a_drain_is_not_a_collapse_from_it(
        self,
    ) -> None:
        # The engine marks two events of the Renew Spells phase unconscious
        # that are no collapse: a Runaway feeding on a figure who is down, and
        # a spell such a figure cannot renew. One comes straight after a
        # drain when a Runaway fells a crafter there, under a canon that
        # drains for a working lost that way: the craft is lost as the mage
        # falls, the drain is taken, and the next event is the foe's, who was
        # down already with Shield up.
        drains_the_fallen = replace(
            DEFAULT_MAGIC_RULES,
            true_magic=replace(
                play.TRUE_MAGIC,
                lost_drain_per_mana={
                    **play.TRUE_MAGIC.lost_drain_per_mana,
                    CastingEnding.UNCONSCIOUS: 5,
                },
            ),
        )
        # The Runaway takes its cost of 1 and 1 more a turn: the mage's last 2.
        state = play.crafting_sear(mana=2, runaway=RunawayState("shield", 1, 1))
        foe = state.by_id(play.FOE)
        foe.conscious, foe.active_spells = False, ["shield"]
        with seated_rules(drains_the_fallen):
            events = play.play_turn(state, {})
        messages = [event["message"] for event in events]
        drain = "true_magic_log.drain 5 35 Mage fatigue"
        self.assertEqual(
            messages[messages.index(drain) - 2 : messages.index(drain) + 2],
            [
                "Mage collapses unconscious",
                "true_magic_log.lost_unconscious 1 Mage Sear (tier 1)",
                drain,
                "Foe is unconscious and cannot renew Shield; it ends",
            ],
        )
        self.assertEqual(lines(events, "drain_collapse"), [])

    def test_a_collapse_with_no_event_before_it_is_nobodys_drain(self) -> None:
        collapse = {
            "event_type": "status",
            "actor": "Mage",
            "sequence": 1,
            "payload": {"unconscious": True, "chain": []},
            "message": "as the engine wrote it",
        }
        (told,) = true_magic_narration.narrated(play.board(), [collapse])
        self.assertIs(told, collapse)

    def test_an_ending_with_no_sentence_is_refused_not_passed_through(self) -> None:
        # ``failed`` is an ending of a casting, and no craft is lost by it.
        self.assertNotIn(CastingEnding.FAILED, CRAFT_LOSS_REASONS)
        event = {
            "event_type": "status",
            "actor": "Mage",
            "sequence": 1,
            "payload": {
                "craft_lost": True,
                "ending": CastingEnding.FAILED.value,
                "spell": play.SEAR,
                "mana_spent": 0,
            },
            "message": "as the engine wrote it",
        }
        with self.assertRaises(KeyError):
            true_magic_narration.narrated(play.board(), [event])


class BothReadersReadTheConfiguredLinesTest(BothReadersTestCase):
    """The battle page and the Games Master's battle report both print the
    configured line, since it is the stored event's own message."""

    @with_fixed_words
    def test_a_craft_past_a_lifted_bound_still_says_so_beneath_its_line(self) -> None:
        # Rewording an event keeps its payload, which the line beneath reads.
        past_the_cap = play.TRUE_MAGIC.working_key("sear", 5)
        lifted = magic_bounds.LiftedBounds().lifted(
            play.MAGE, [magic_bounds.CHANNEL], by="gm", by_id=1, at=FIRST_AT
        )
        with seated_rules(DEFAULT_MAGIC_RULES):
            events = play.play_turn(
                play.board(),
                {play.MAGE: play.craft(past_the_cap, play.FOE), play.FOE: play.idle},
                battle=Battle(magic_bounds_lifted_json=lifted.to_json()),
            )
            self.assert_both_read(
                [as_row(event) for event in events],
                # Five mana over fifteen turns: nothing is due on the first.
                "true_magic_log.craft_begun 6 0 5 Mage true_magic_log.share_none 10 "
                "1 15 Sear (tier 5)",
                # The line beneath, its first letter raised as both print it.
                "Magic_bounds.past_by_ruling magic_bounds.channel",
            )

    @with_fixed_words
    def test_a_craft_from_its_first_turn_to_its_drain(self) -> None:
        with seated_rules(DEFAULT_MAGIC_RULES):
            events = play.play(
                play.board(mage={"active_spells": ["shield"]}),
                [{play.MAGE: SEAR, play.FOE: play.idle}] * 3,
            )
        self.assert_both_read(
            [as_row(event) for event in events if event["turn"] == events[-1]["turn"]],
            "true_magic_log.craft_continued 6 1 1 Mage "
            "true_magic_log.share_paid 8 1 3 3 Sear (tier 1)",
            "true_magic_log.completed 6 1 8 Mage "
            "true_magic_log.casting_roll 3d6 24 9 WIS+Arcane Sear (tier 1)",
            "true_magic_log.drain 8 32 Mage fatigue",
        )
        self.assert_both_read(
            [as_row(event) for event in events if event["turn"] == events[0]["turn"]],
            "true_magic_log.spell_let_go Mage Shield",
            "true_magic_log.craft_begun 6 0 1 Mage "
            "true_magic_log.share_none 9 1 3 Sear (tier 1)",
        )
