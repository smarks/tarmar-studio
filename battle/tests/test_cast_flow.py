"""The wizard cast flow (#250): what a caster is told before they commit.

The command bar's cast stage is gated — a player who declares CAST SPELL
owes an explicit spell pick, and the spells they *cannot* cast are shown
with the reason rather than silently omitted. That needs more than the
policy's candidate list (which only carries the castable ones), so
``services.caster_magic`` publishes the caster's whole magical situation
and ``pending_payload`` carries it under the same secrecy gate as the menu.

The castable set is derived in two places for two purposes — the engine's
``policy._cast_candidates`` (what may be chosen) and ``caster_magic`` (what
is shown, with reasons) — so :class:`CastableSetAgreesWithPolicyTest` pins
them together the way ``test_rules_drift`` pins the rules docs.
"""

import json
from collections.abc import Callable
from dataclasses import replace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from battle import policy, profiles
from battle.classic_adaptation import (
    classic_caster_magic,
    classic_shield,
    classic_state,
)
from battle.engine.state import BattleState
from battle.models import Battle, BattleCombatant, BattleEvent
from battle.services import (
    advance_turn,
    caster_magic,
    claim_seat,
    create_battle,
    record_player_action,
    set_seat_ready,
    start_battle,
)
from battle.turn_menu import borrowed_mana
from common.test_helpers import BaseTestCase, FacesRoller

from .magic_fixtures import (
    BORROWING_RULES,
    CASTING_FAILURE,
    INVENTED_RULES,
    seated_rules,
)
from .test_classic_adaptation import snapshot as classic_snapshot
from .test_state import make_combatant

# A caster with enough mana for everything in the catalog's reach and a
# fighter to aim at — the shared fixture for the unit tests below.
WIZARD_SPELLS = ["fire_missile", "lightning_bolt", "shield", "heal"]
#: Each spell's recorded level: under the invented Channel (twice the level)
#: every spell is within it.
WIZARD_SPELL_LEVELS = {"fire_missile": 1, "lightning_bolt": 2, "shield": 1, "heal": 1}


def wizard_state(**overrides):
    fields = {
        "mana": 6,
        "max_mana": 10,
        "spells": list(WIZARD_SPELLS),
        "spell_skill_levels": dict(WIZARD_SPELL_LEVELS),
        "intelligence": 14,
        "wisdom": 12,
    }
    fields.update(overrides)
    return BattleState(
        combatants=[
            make_combatant(1, q=0, r=0, **fields),
            make_combatant(2, q=2, r=0),
        ]
    )


class CasterMagicTest(SimpleTestCase):
    def setUp(self):
        super().setUp()
        # Invented magic rules, so the rows read the same under either canon.
        self.enterContext(seated_rules())

    def test_a_non_caster_has_no_magic_to_show(self):
        state = BattleState(combatants=[make_combatant(1), make_combatant(2)])
        self.assertIsNone(caster_magic(state, 1))

    def test_the_pool_and_every_known_spell_are_published(self):
        magic = caster_magic(wizard_state(), 1)
        assert magic is not None
        # The pool names itself: the page prints the label rather than
        # assuming Tarmar's vocabulary (#263).
        self.assertEqual(magic["pool_label"], "Mana")
        self.assertEqual(magic["pool"], 6)
        self.assertEqual(magic["max_pool"], 10)
        self.assertEqual([spell["key"] for spell in magic["spells"]], WIZARD_SPELLS)
        missile = magic["spells"][0]
        self.assertEqual(missile["name"], "Fire Missile")
        self.assertEqual(missile["cost"], "Elemental — 1 mana")
        self.assertEqual(missile["target_id"], 2)
        self.assertTrue(missile["castable"])
        self.assertEqual(missile["reason"], "")

    def test_a_spell_with_nobody_to_aim_at_says_so(self):
        # The engine offers no candidate for a damage spell with no enemy
        # in the state, so the row cannot claim to be castable.
        alone = BattleState(
            combatants=[
                make_combatant(
                    1,
                    mana=6,
                    max_mana=10,
                    spells=list(WIZARD_SPELLS),
                    intelligence=14,
                    wisdom=12,
                )
            ]
        )
        magic = caster_magic(alone, 1)
        assert magic is not None
        missile = magic["spells"][0]
        self.assertIsNone(missile["target_id"])
        self.assertFalse(missile["castable"])
        self.assertEqual(missile["reason"], "nothing in reach to aim at")

    def test_an_unaffordable_spell_is_shown_with_what_it_costs(self):
        magic = caster_magic(wizard_state(mana=2), 1)
        assert magic is not None
        bolt = next(
            spell for spell in magic["spells"] if spell["key"] == "lightning_bolt"
        )
        self.assertFalse(bolt["castable"])
        self.assertEqual(bolt["reason"], "needs 3 mana, 2 left")

    def test_a_continuing_spell_already_up_is_shown_as_up(self):
        magic = caster_magic(wizard_state(active_spells=["shield"]), 1)
        assert magic is not None
        shield = next(spell for spell in magic["spells"] if spell["key"] == "shield")
        self.assertFalse(shield["castable"])
        self.assertEqual(shield["reason"], "already up — renews itself")

    def test_the_casting_chance_is_worded_per_spell(self):
        magic = caster_magic(wizard_state(), 1)
        assert magic is not None
        by_key = {spell["key"]: spell for spell in magic["spells"]}
        # 3d6 <= INT for the INT spells, <= WIS for Heal (a Mending spell),
        # already worded here because a classic cast rolls against neither
        # (#263) and the page must not phrase one profile's odds for both.
        self.assertEqual(
            by_key["fire_missile"]["note"],
            f"{policy.three_d6_at_most(14):.0%} on 3d6 \u2264 INT,"
            " then a DEX roll to land it",
        )
        self.assertEqual(
            by_key["heal"]["note"],
            f"{policy.three_d6_at_most(12):.0%} on 3d6 \u2264 WIS",
        )

    def test_the_casting_chance_includes_the_fumble_floor(self):
        """#46: the odds shown are the engine's, the casting floor included,
        and the note names the floor where it lowers the chance."""
        # The invented floor is 12: a roll of 12 or more fails.
        with seated_rules(replace(INVENTED_RULES, casting_failure=CASTING_FAILURE)):
            magic = caster_magic(wizard_state(), 1)
        assert magic is not None
        by_key = {spell["key"]: spell for spell in magic["spells"]}
        # INT 14 past the floor: 3d6 at most 11, not at most 14.
        self.assertEqual(
            by_key["fire_missile"]["note"],
            f"{policy.three_d6_at_most(11):.0%} on 3d6 \u2264 INT, under the "
            "fumble floor 12, then a DEX roll to land it",
        )
        self.assertNotEqual(
            f"{policy.three_d6_at_most(11):.0%}", f"{policy.three_d6_at_most(14):.0%}"
        )
        # WIS 12 too: a 12 would pass the attribute but meets the floor.
        self.assertEqual(
            by_key["heal"]["note"],
            f"{policy.three_d6_at_most(11):.0%} on 3d6 \u2264 WIS, under the "
            "fumble floor 12",
        )

    def test_a_floor_above_the_attribute_is_not_named(self):
        # A floor of 16 lowers neither INT 14 nor WIS 12.
        high_floor = replace(
            CASTING_FAILURE,
            fumble_floor=16,
            mana_lost_totals=frozenset({16, 17}),
            runaway_total=17,
        )
        with seated_rules(replace(INVENTED_RULES, casting_failure=high_floor)):
            magic = caster_magic(wizard_state(), 1)
        assert magic is not None
        heal = next(spell for spell in magic["spells"] if spell["key"] == "heal")
        self.assertEqual(
            heal["note"], f"{policy.three_d6_at_most(12):.0%} on 3d6 \u2264 WIS"
        )

    def test_a_spell_at_two_foes_gets_a_row_each_nearest_first(self):
        state = wizard_state()
        far = make_combatant(3, q=-4, r=0)
        state.combatants.append(far)
        magic = caster_magic(state, 1)
        assert magic is not None
        near = state.by_id(2)
        missiles = [row for row in magic["spells"] if row["key"] == "fire_missile"]
        self.assertEqual([row["target_id"] for row in missiles], [2, 3])
        self.assertEqual(
            [row["name"] for row in missiles],
            [f"Fire Missile → {near.name}", f"Fire Missile → {far.name}"],
        )
        # A spell aimed at nobody keeps its one row and its own name.
        shield = [row for row in magic["spells"] if row["key"] == "shield"]
        self.assertEqual(
            [(row["target_id"], row["name"]) for row in shield], [(None, "Shield")]
        )

    def test_a_dead_or_missing_combatant_has_no_magic(self):
        self.assertIsNone(caster_magic(wizard_state(), 99))

    def test_each_row_carries_its_channel(self):
        # The invented Channel is twice the recorded level.
        magic = caster_magic(wizard_state(), 1)
        assert magic is not None
        self.assertEqual(
            {spell["key"]: spell["channel"] for spell in magic["spells"]},
            {key: 2 * level for key, level in WIZARD_SPELL_LEVELS.items()},
        )

    def test_no_channel_rules_carry_no_channel(self):
        with seated_rules(replace(INVENTED_RULES, channel=None)):
            magic = caster_magic(wizard_state(), 1)
        assert magic is not None
        self.assertEqual({spell["channel"] for spell in magic["spells"]}, {None})

    def test_a_spell_past_the_channel_says_so(self):
        # Level 1 in Lightning Bolt: the invented Channel is 2, its cost 3.
        magic = caster_magic(
            wizard_state(
                spell_skill_levels={**WIZARD_SPELL_LEVELS, "lightning_bolt": 1}
            ),
            1,
        )
        assert magic is not None
        bolt = next(
            spell for spell in magic["spells"] if spell["key"] == "lightning_bolt"
        )
        self.assertFalse(bolt["castable"])
        self.assertEqual(bolt["reason"], "costs 3 mana, past a Channel of 2 for it")


class BorrowableSpellTest(SimpleTestCase):
    """A spell the caster cannot afford, under Borrowing (tarmar-engine #28).

    Invented rules: a cap of three owed, and a Channel of twice the
    recorded level.
    """

    def setUp(self):
        super().setUp()
        self.enterContext(seated_rules(BORROWING_RULES))

    def row(self, state, key: str) -> dict:
        magic = caster_magic(state, 1)
        assert magic is not None
        return next(spell for spell in magic["spells"] if spell["key"] == key)

    def test_a_spell_short_of_mana_is_borrowable_with_its_shortfall(self):
        bolt = self.row(wizard_state(mana=1), "lightning_bolt")
        # Not castable: the plain cast is not on the menu; not withheld.
        self.assertFalse(bolt["castable"])
        self.assertTrue(bolt["borrowable"])
        self.assertEqual(bolt["shortfall"], 2)
        self.assertEqual(bolt["reason"], "needs 3 mana, 1 left: borrow the 2 it lacks")

    def test_an_affordable_spell_borrows_nothing(self):
        missile = self.row(wizard_state(mana=1), "fire_missile")
        self.assertTrue(missile["castable"])
        self.assertFalse(missile["borrowable"])
        self.assertEqual(missile["shortfall"], 0)

    def test_the_debt_cap_bounds_what_may_be_borrowed(self):
        # Owing 2 of a cap of 3, the caster may borrow 1 more: Fire Missile
        # yes, Lightning Bolt (3 short) no.
        state = wizard_state(mana=0, debt=2)
        self.assertTrue(self.row(state, "fire_missile")["borrowable"])
        bolt = self.row(state, "lightning_bolt")
        self.assertFalse(bolt["borrowable"])
        self.assertEqual(bolt["reason"], "needs 3 mana, 0 left")

    def test_a_spell_past_the_channel_is_not_borrowable(self):
        bolt = self.row(
            wizard_state(
                mana=1, spell_skill_levels={**WIZARD_SPELL_LEVELS, "lightning_bolt": 1}
            ),
            "lightning_bolt",
        )
        self.assertFalse(bolt["borrowable"])

    def test_no_borrowing_rules_borrow_nothing(self):
        with seated_rules(INVENTED_RULES):
            bolt = self.row(wizard_state(mana=1), "lightning_bolt")
        self.assertFalse(bolt["borrowable"])
        self.assertEqual(bolt["reason"], "needs 3 mana, 1 left")

    def test_the_pool_says_what_is_owed(self):
        magic = caster_magic(wizard_state(mana=0, debt=2), 1)
        assert magic is not None
        self.assertEqual(magic["owed"], 2)


def replace_mana(state: BattleState, mana: int) -> BattleState:
    """``state`` with combatant 1's pool set to ``mana``, the rest shared."""
    caster = replace(state.by_id(1), mana=mana)
    return replace(
        state,
        combatants=[
            caster if combatant.combatant_id == 1 else combatant
            for combatant in state.combatants
        ],
    )


def assert_stage_agrees_with_menu(test: SimpleTestCase, state: BattleState) -> None:
    """The cast stage's castable and borrowable rows equal the engine menu's
    plain and borrowing casts for combatant 1, on the seated rules."""
    magic = caster_magic(state, 1)
    assert magic is not None
    # Keyed by (spell, target) rather than by spell: that pair is what a
    # candidate names, and what the stage must pair a row with (#263).
    shown = {
        (spell["key"], spell["target_id"])
        for spell in magic["spells"]
        if spell["castable"]
    }
    borrowable = {
        (spell["key"], spell["target_id"]): spell["shortfall"]
        for spell in magic["spells"]
        if spell["borrowable"]
    }
    # The menu as the decider builds it for a seated player, with the magic
    # rules the turn runs on (issue #960; its Channel withholds casts too)
    # and every target (tarmar-engine #21: a damaging cast at each active
    # enemy). A pushed variant is the same cast with more mana, not another
    # row; a borrowing copy of a spell's plain cast is its borrowable row.
    actor = state.by_id(1)
    menu = policy.choose_option(
        state, actor, magic=profiles.tarmar_engine_profile().magic, every_target=True
    )
    offered = {
        (candidate.spell_key, candidate.target_id)
        for candidate in menu.candidates
        if candidate.spell_key and not candidate.push_mana and not candidate.borrow
    }
    offered_borrowing = {
        (candidate.spell_key, candidate.target_id): borrowed_mana(actor, candidate)
        for candidate in menu.candidates
        if candidate.borrow and not candidate.push_mana
    }
    test.assertEqual(shown, offered)
    test.assertEqual(borrowable, offered_borrowing)


class CastableSetAgreesWithPolicyTest(SimpleTestCase):
    """The shown-castable set must equal the choosable set, exactly.

    A drift here would offer a player a spell the replayed menu rejects
    (or hide one they are entitled to), so it is pinned rather than
    trusted to stay in step by inspection.
    """

    def setUp(self):
        super().setUp()
        # With Borrowing (tarmar-engine #28), so the borrowable set is pinned
        # to the menu's borrowing copies too.
        self.enterContext(seated_rules(BORROWING_RULES))

    def assert_sets_agree(self, state):
        assert_stage_agrees_with_menu(self, state)

    def test_agreement_with_a_full_pool(self):
        self.assert_sets_agree(wizard_state())

    def test_agreement_when_mana_runs_short(self):
        self.assert_sets_agree(wizard_state(mana=1))

    def test_agreement_when_a_continuing_spell_is_up(self):
        self.assert_sets_agree(wizard_state(active_spells=["shield"]))

    def test_agreement_when_the_channel_withholds_a_spell(self):
        self.assert_sets_agree(
            wizard_state(
                spell_skill_levels={**WIZARD_SPELL_LEVELS, "lightning_bolt": 1}
            )
        )

    def test_agreement_when_nothing_is_recorded(self):
        # No recorded level: Channel 0, every cast withheld on both sides.
        self.assert_sets_agree(wizard_state(spell_skill_levels={}))

    def test_agreement_with_an_empty_pool(self):
        self.assert_sets_agree(wizard_state(mana=0))

    def test_agreement_when_the_debt_cap_is_near(self):
        self.assert_sets_agree(wizard_state(mana=0, debt=2))

    def test_agreement_when_the_debt_cap_is_reached(self):
        self.assert_sets_agree(wizard_state(mana=0, debt=3))

    def test_agreement_when_the_channel_bounds_a_borrowing(self):
        self.assert_sets_agree(
            wizard_state(
                mana=1, spell_skill_levels={**WIZARD_SPELL_LEVELS, "lightning_bolt": 1}
            )
        )

    def test_agreement_with_two_foes_to_aim_at(self):
        state = wizard_state()
        state.combatants.append(make_combatant(3, q=-4, r=0))
        self.assert_sets_agree(state)
        self.assert_sets_agree(replace_mana(state, 1))

    def test_agreement_with_no_foe_to_aim_at(self):
        state = wizard_state(mana=0)
        state.by_id(2).alive = False
        self.assert_sets_agree(state)


def awaiting_wizard_battle(
    dm,
    player,
    *,
    mana: int | None = None,
    before_turn: Callable[[dict, BattleCombatant], None] | None = None,
) -> tuple[Battle, BattleCombatant]:
    """A battle paused on ``player``'s seated wizard's decision.

    Args:
        dm: The user who creates the battle.
        player: The user who claims the one player seat, as a wizard.
        mana: The mana the wizard has when the turn begins, or ``None`` for
            the generated wizard's full pool.
        before_turn: Changes the started battle's ``state_json`` in place,
            given it and the wizard's seat, before the first turn runs.

    Returns:
        The paused battle and the wizard's seat.
    """
    battle = create_battle(dm, name="Cast flow", seed=7, count=2, player_seats=1)
    seat = battle.combatants.filter(is_open_seat=True, player=None).first()
    assert seat is not None
    seat = claim_seat(battle.pk, seat.pk, player, archetype="wizard")
    set_seat_ready(battle.pk, seat.pk, player, ready=True)
    battle = start_battle(battle.pk)
    if mana is not None or before_turn is not None:
        state = battle.state_json
        for combatant in state["combatants"]:
            if combatant["combatant_id"] == seat.pk and mana is not None:
                combatant["mana"] = mana
        if before_turn is not None:
            before_turn(state, seat)
        Battle.objects.filter(pk=battle.pk).update(state_json=state)
    battle, _events = advance_turn(battle.pk)
    return battle, seat


class CastAtTheFarFoeTest(BaseTestCase):
    """A seated wizard casts at the foe the AI would not (tarmar-engine #21).

    The every-target menu offers a damaging cast at each active enemy; the
    stage rows them, the player records the far one, and the engine casts at
    it.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()

    def place(self, state: dict, seat: BattleCombatant) -> None:
        """The wizard at the centre, one foe two hexes off and one four
        hexes the other way, neither able to move before the cast."""
        foes = [c for c in state["combatants"] if c["combatant_id"] != seat.pk]
        self.assertEqual(len(foes), 2)
        wizard = next(c for c in state["combatants"] if c["combatant_id"] == seat.pk)
        self.wizard_name = wizard["name"]
        # The generated wizard's attributes are rolled; fix the two its cast
        # and aim are rolled under.
        wizard["intelligence"], wizard["dexterity"] = 14, 12
        for combatant, (q, r) in zip(
            [wizard, *foes],
            [(0, 0), (2, 0), (-4, 0)],
            strict=True,
        ):
            combatant["q"], combatant["r"] = q, r
        for foe in foes:
            for gait in ("move_walk", "move_jog", "move_run", "move_sprint"):
                foe[gait] = 0
        self.near_id, self.far_id = (foe["combatant_id"] for foe in foes)
        self.far_name = foes[1]["name"]

    def test_the_cast_is_recorded_at_the_far_foe_and_lands_there(self):
        battle, seat = awaiting_wizard_battle(
            self.dm, self.player, before_turn=self.place
        )
        self.assertEqual(battle.pending_input_json["combatant_id"], seat.pk)
        casts = [
            entry
            for entry in battle.pending_input_json["candidates"]
            if entry.get("spell_key") == "fire_missile"
            and not entry.get("push_mana")
            and not entry.get("borrow")
        ]
        # Nearest first, the AI's own target leading.
        self.assertEqual(
            [entry["target_id"] for entry in casts], [self.near_id, self.far_id]
        )
        magic = caster_magic(BattleState.from_dict(battle.state_json), seat.pk)
        assert magic is not None
        rows = [row for row in magic["spells"] if row["key"] == "fire_missile"]
        self.assertEqual(
            [row["target_id"] for row in rows], [self.near_id, self.far_id]
        )
        far_cast = casts[1]
        # Every roll on low faces, so the cast and its aim succeed whatever
        # the generated wizard rolled up and the spell's effect shows whom
        # it was cast at.
        with patch.object(
            profiles.TarmarProfile, "roller", lambda _profile: FacesRoller(2, 2, 2)
        ):
            battle, _events = record_player_action(
                battle.pk,
                self.player,
                combatant_id=seat.pk,
                turn=battle.pending_input_json["turn"],
                letter=far_cast["letter"],
                target_id=self.far_id,
                spell_key="fire_missile",
            )
        decision = BattleEvent.objects.get(
            battle=battle, event_type="decision", actor=self.wizard_name
        )
        self.assertEqual(decision.payload["chosen"]["target_id"], self.far_id)
        # The engine casts it, and it lands on the far foe: the spell's damage
        # names that foe, in its payload and its line.
        events = list(
            BattleEvent.objects.filter(battle=battle, actor=self.wizard_name).order_by(
                "sequence"
            )
        )
        cast_at = next(
            index
            for index, event in enumerate(events)
            if event.event_type == "action"
            and (event.payload or {}).get("spell") == "fire_missile"
        )
        self.assertTrue(events[cast_at].payload["success"])
        damage = next(
            event for event in events[cast_at:] if event.event_type == "damage"
        )
        self.assertEqual(damage.payload["target"], self.far_id)
        self.assertTrue(damage.message.startswith(f"{self.far_name} takes "))


class PendingPayloadCarriesMagicTest(BaseTestCase):
    """The cast stage's data reaches exactly the viewers the menu does."""

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.player = self.create_player()
        self.watcher = self.create_user(username="castwatcher")

    def awaiting_wizard_battle(self):
        """A battle paused on a player-seated wizard's decision."""
        return awaiting_wizard_battle(self.dm, self.player)

    def state_for(self, user, battle):
        self.login_as(user)
        response = self.client.get(reverse("battle:state", args=[battle.pk]))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)["pending"]

    def test_the_awaited_wizard_gets_their_pool_and_spell_list(self):
        battle, seat = self.awaiting_wizard_battle()
        pending = self.state_for(self.player, battle)
        self.assertEqual(pending["combatant_id"], seat.pk)
        magic = pending["magic"]
        self.assertIsNotNone(magic)
        self.assertTrue(magic["spells"])
        self.assertEqual(magic["pool_label"], "Mana")
        self.assertEqual(magic["max_pool"], magic["pool"])

    def test_the_dm_sees_it_too(self):
        battle, _seat = self.awaiting_wizard_battle()
        self.assertIsNotNone(self.state_for(self.dm, battle)["magic"])

    def test_a_spectator_is_told_nothing_of_the_caster_s_hand(self):
        battle, _seat = self.awaiting_wizard_battle()
        pending = self.state_for(self.watcher, battle)
        self.assertFalse(pending["is_you"])
        self.assertEqual(pending["candidates"], [])
        self.assertIsNone(pending["magic"])


def classic_wizard_state(intelligence: int = 12):
    """A classic game with one wizard and two foes it can aim at.

    Two foes rather than one on purpose: a classic wizard is offered the
    same spell at each of them, which is the arrangement #263's collapse
    bug needed and the reason a row is keyed by (spell, target).
    """
    state, _roster = classic_state(
        [
            classic_snapshot(
                1, "Ada", intelligence=intelligence, spells=("mage bolt",)
            ),
            classic_snapshot(2, "Bram"),
            classic_snapshot(3, "Cyn"),
        ],
        seed=5,
    )
    return state


def raise_shield(state):
    """Put a shield back in the wizard's hand, which forbids casting (p.23)."""
    wizard = next(one for one in state.figures if one.uid == "1")
    wizard.shield = classic_shield(2)
    wizard.shield_ready = True
    return state


class ClassicCasterMagicTest(SimpleTestCase):
    """The classic wizard's own stage (#263), in its own vocabulary."""

    def test_a_classic_fighter_has_no_magic_to_show(self):
        self.assertIsNone(classic_caster_magic(classic_wizard_state(), 2))

    def test_a_figure_not_in_this_battle_has_no_magic(self):
        self.assertIsNone(classic_caster_magic(classic_wizard_state(), 99))

    def test_the_pool_is_st_and_says_so(self):
        # The pool a classic wizard spends is the pool that holds its hit
        # points, and it is never mana — the whole reason the payload names
        # its own pool instead of letting the page assume.
        magic = classic_caster_magic(classic_wizard_state(), 1)
        assert magic is not None
        self.assertEqual(magic["pool_label"], "ST")
        figure = next(one for one in classic_wizard_state().figures if one.uid == "1")
        self.assertEqual(magic["pool"], figure.current_st)
        self.assertEqual(magic["max_pool"], figure.strength)

    def test_a_spell_offered_at_two_foes_gets_a_row_each(self):
        magic = classic_caster_magic(classic_wizard_state(), 1)
        assert magic is not None
        fists = [spell for spell in magic["spells"] if spell["key"] == "magic_fist"]
        self.assertEqual([row["target_id"] for row in fists], ["2", "3"])
        self.assertEqual(
            [row["name"] for row in fists],
            ["Magic Fist → Bram", "Magic Fist → Cyn"],
        )

    def test_a_self_cast_names_no_target_in_its_row(self):
        magic = classic_caster_magic(classic_wizard_state(), 1)
        assert magic is not None
        blur = next(spell for spell in magic["spells"] if spell["key"] == "blur")
        self.assertEqual(blur["target_id"], "1")
        self.assertEqual(blur["name"], "Blur")
        # No range to aim: a self-cast takes no distance penalty, so its
        # printed chance is the whole of it.
        self.assertNotIn("range", blur["note"])

    def test_a_cast_costs_st_and_says_what_it_rolls(self):
        magic = classic_caster_magic(classic_wizard_state(), 1)
        assert magic is not None
        fist = next(spell for spell in magic["spells"] if spell["key"] == "magic_fist")
        self.assertEqual(fist["cost"], "Missile — 3 ST")
        self.assertIn("adjDX", fist["note"])
        self.assertIn("before the range to aim", fist["note"])
        self.assertNotIn("mana", fist["cost"])

    def test_a_spell_nothing_can_be_cast_at_is_shown_with_the_reason(self):
        # Staff is in a crossed wizard's hand but the cast layer aims it at
        # nobody, so it is listed and disabled rather than quietly dropped.
        magic = classic_caster_magic(classic_wizard_state(), 1)
        assert magic is not None
        staff = next(spell for spell in magic["spells"] if spell["key"] == "staff")
        self.assertFalse(staff["castable"])
        self.assertEqual(staff["reason"], "nothing this spell can be cast at")
        self.assertIsNone(staff["target_id"])

    def test_a_spell_the_catalog_never_heard_of_is_left_out(self):
        # A saved battle can outlive a catalog entry; the stage skips what
        # it cannot describe rather than printing a blank row, which is what
        # ``classic.ai.castable_spells`` does with the same id.
        state = classic_wizard_state()
        wizard = next(one for one in state.figures if one.uid == "1")
        wizard.spells_known = [*wizard.spells_known, "spell_from_a_lost_age"]
        magic = classic_caster_magic(state, 1)
        assert magic is not None
        self.assertTrue(magic["spells"])
        self.assertNotIn(
            "spell_from_a_lost_age", {spell["key"] for spell in magic["spells"]}
        )

    def test_a_wizard_that_cannot_cast_at_all_is_told_once_per_spell(self):
        # A shield in hand forbids casting outright (Wizard p.23). The
        # crossing strips a caster's shield for exactly that reason, so the
        # block has to be put back by hand to see what the stage says: the
        # whole hand listed, every row disabled, one true reason on each.
        state = raise_shield(classic_wizard_state())
        magic = classic_caster_magic(state, 1)
        assert magic is not None
        self.assertTrue(magic["spells"])
        self.assertTrue(all(not spell["castable"] for spell in magic["spells"]))
        self.assertEqual(
            {spell["reason"] for spell in magic["spells"]},
            {"cannot cast with a shield ready"},
        )


class ClassicCastableSetAgreesWithPolicyTest(SimpleTestCase):
    """The classic twin of :class:`CastableSetAgreesWithPolicyTest`.

    Same contract, and the same reason for it: the stage's rows and the
    menu's candidates are built by different code for different purposes,
    so what a wizard is shown as castable must be exactly what the replayed
    menu will accept — down to the target, since classic offers one cast
    per (spell, target) pair.
    """

    def assert_sets_agree(self, state):
        from tarmar_engine.classic.policy import cast_candidates

        magic = classic_caster_magic(state, 1)
        assert magic is not None
        shown = {
            (spell["key"], spell["target_id"])
            for spell in magic["spells"]
            if spell["castable"]
        }
        wizard = next(one for one in state.figures if one.uid == "1")
        offered = {
            (candidate.spell_key, candidate.target_id)
            for candidate in cast_candidates(state, wizard)
        }
        self.assertEqual(shown, offered)

    def test_agreement_with_a_full_pool(self):
        self.assert_sets_agree(classic_wizard_state())

    def test_agreement_when_the_pool_runs_short(self):
        state = classic_wizard_state()
        wizard = next(one for one in state.figures if one.uid == "1")
        wizard.damage_taken = wizard.strength - 1
        self.assert_sets_agree(state)

    def test_agreement_when_the_wizard_cannot_cast_at_all(self):
        self.assert_sets_agree(raise_shield(classic_wizard_state()))
