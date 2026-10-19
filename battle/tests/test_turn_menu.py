"""A seated player's turn menu: every target (#775), and choices to weigh (#788).

#775: the engine's menu aims each attack where the AI would, so a player
beside two wolves was offered one "Strike Gray Wolf" and no way to strike the
other, and an archer could not shoot anyone but the nearest enemy.

#788: the menu led with the AI's scoring sheet ("score 0.178 — Strike Gray
Wolf: bare hands at 1 hex, d20 -3 vs TN 16 — P(hit) 0.100 ..."). Each option
now carries a plain line built from the same figures, what it costs, and
whether it is the AI's pick; the score stays on the payload.
"""

from dataclasses import replace
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from tarmar_engine import movement

from battle import control, turn_menu
from battle.engine import hexes
from battle.engine.state import (
    BattleState,
    CombatantState,
    GroundWeapon,
    WeaponState,
)
from battle.magic_rules import magic_rules
from battle.models import Battle, BattleEvent
from battle.persistence import CLASSIC_PROFILE, TARMAR_PROFILE, dump_state
from battle.policy import Candidate, choose_option
from battle.services import (
    _catalog_weapon,
    advance_turn,
    create_battle,
    record_player_action,
    set_combatant_control_mode,
)
from common.test_helpers import BaseTestCase

from .magic_fixtures import CASTING_FAILURE, seated_rules


def figure(combatant_id: int, name: str, position: tuple[int, int], **fields):
    """A plain figure with a real catalog weapon, at ``position``.

    It faces the origin, where each test puts the figure choosing, so an
    enemy beside it engages it (movement.md: engagement is an enemy's front).
    """
    defaults = {
        "strength": 13,
        "dexterity": 12,
        "weapon": _catalog_weapon("broadsword"),
        "weapon_skill_level": 2,
        "facing": hexes.direction_towards(position, (0, 0))
        if position != (0, 0)
        else 0,
    }
    defaults.update(fields)
    return CombatantState(
        combatant_id=combatant_id,
        name=name,
        q=position[0],
        r=position[1],
        **defaults,
    )


class EveryTargetTest(BaseTestCase):
    """The engine's menu, with each attack also aimed at every legal target."""

    def menu(self, state: BattleState, actor: CombatantState):
        advice = choose_option(state, actor)
        return advice, turn_menu.every_target(state, actor, advice.candidates)

    def test_an_attack_is_offered_at_each_adjacent_enemy(self):
        fighter = figure(1, "Aldwin", (0, 0))
        gray = figure(2, "Gray Wolf", (1, 0), stops=3, armour_tier="Medium")
        dire = figure(3, "Dire Wolf", (0, 1))
        far = figure(4, "Far Wolf", (4, 0))
        state = BattleState(arena_radius=6, combatants=[fighter, gray, dire, far])
        advice, menu = self.menu(state, fighter)
        engine_strikes = [c for c in advice.candidates if c.letter == "j"]
        strikes = [c for c in menu if c.letter == "j"]
        # The engine offers one; the player gets both adjacent wolves, and
        # nobody who is out of reach.
        self.assertEqual(len(engine_strikes), 1)
        self.assertEqual({c.target_id for c in strikes}, {2, 3})
        # The engine's own candidate keeps its place, first.
        self.assertIs(strikes[0], engine_strikes[0])
        other = strikes[1]
        self.assertEqual(other.name, "ATTACK")
        self.assertTrue(
            other.rationale.startswith(
                f"Strike {state.by_id(int(other.target_id or 0)).name}: "
            )
        )
        self.assertIn("P(hit)", other.rationale)
        # Everything the engine offered is still there, in its order.
        self.assertEqual([c for c in menu if c in advice.candidates], advice.candidates)

    def test_a_missile_is_offered_at_every_enemy(self):
        archer = figure(1, "Dorra", (0, 0), weapon=_catalog_weapon("longbow"))
        near = figure(2, "Near", (0, 3))
        middle = figure(3, "Middle", (4, 0))
        far = figure(4, "Far", (-5, 0))
        state = BattleState(arena_radius=6, combatants=[archer, near, middle, far])
        advice, menu = self.menu(state, archer)
        shots = [c for c in menu if c.letter == "f"]
        self.assertEqual({c.target_id for c in shots}, {2, 3, 4})
        engine_shot = next(c for c in advice.candidates if c.letter == "f")
        self.assertEqual(engine_shot.target_id, 2)  # the AI aims at the nearest
        far_shot = next(c for c in shots if c.target_id == 4)
        self.assertTrue(far_shot.rationale.startswith("Shoot Far: "))
        self.assertIn("(range", far_shot.rationale)

    def test_a_charge_reaches_only_as_far_as_the_jog_and_a_hex(self):
        fighter = figure(1, "Aldwin", (0, 0), move_jog=3)
        near = figure(2, "Near", (3, 0))
        just = figure(3, "Just", (0, 4))
        beyond = figure(4, "Beyond", (-5, 0))
        state = BattleState(arena_radius=6, combatants=[fighter, near, just, beyond])
        _advice, menu = self.menu(state, fighter)
        charges = [c for c in menu if c.letter == "b"]
        self.assertEqual({c.target_id for c in charges}, {2, 3})
        yielded = [c for c in menu if c.letter == "b_yield"]
        self.assertEqual({c.target_id for c in yielded}, {2, 3})
        self.assertTrue(
            all(
                c.rationale.startswith(
                    "As CHARGE ATTACK, moving in Final Movement instead: "
                )
                for c in yielded
            )
        )

    def test_a_throw_of_the_only_hand_weapon_is_the_players_alone_at_any_target(self):
        thrower = figure(1, "Harar", (0, 0), weapon=_catalog_weapon("dagger"))
        near = figure(2, "Near", (0, 3))
        far = figure(3, "Far", (4, 0))
        state = BattleState(arena_radius=6, combatants=[thrower, near, far])
        _advice, menu = self.menu(state, thrower)
        throws = [c for c in menu if c.letter == "f"]
        self.assertEqual({c.target_id for c in throws}, {2, 3})
        for throw in throws:
            # The engine's rule for its own target, kept for every other.
            self.assertEqual(throw.score, 0.0)
            self.assertIn("so the AI keeps it", throw.rationale)

    def test_one_last_shot_is_offered_at_each_adjacent_enemy(self):
        archer = figure(1, "Dorra", (0, 0), weapon=_catalog_weapon("longbow"))
        left = figure(2, "Left", (1, 0))
        right = figure(3, "Right", (-1, 0))
        state = BattleState(arena_radius=4, combatants=[archer, left, right])
        _advice, menu = self.menu(state, archer)
        shots = [c for c in menu if c.letter == "l"]
        self.assertEqual({c.target_id for c in shots}, {2, 3})
        self.assertTrue(all(c.rationale.startswith("One last shot at ") for c in shots))

    def test_a_close_strike_is_offered_at_each_enemy_an_entry_admits(self):
        brawler = figure(
            1, "Harar", (0, 0), weapon=_catalog_weapon("dagger"), movement_modifier=2
        )
        left = figure(2, "Left", (1, 0), movement_modifier=0)
        right = figure(3, "Right", (-1, 0), movement_modifier=0)
        state = BattleState(arena_radius=4, combatants=[brawler, left, right])
        _advice, menu = self.menu(state, brawler)
        for letter in ("t", "o"):
            with self.subTest(letter=letter):
                offered = [c for c in menu if c.letter == letter]
                self.assertEqual({c.target_id for c in offered}, {2, 3})

    def test_a_figure_in_a_grapple_keeps_the_grapples_menu(self):
        held = figure(1, "Held", (0, 0), grappled_by=2)
        captor = figure(2, "Captor", (1, 0), grappling=1)
        bystander = figure(3, "Bystander", (0, 1))
        state = BattleState(arena_radius=4, combatants=[held, captor, bystander])
        advice, menu = self.menu(state, held)
        self.assertEqual(menu, advice.candidates)

    def test_teammates_are_never_offered(self):
        fighter = figure(1, "Aldwin", (0, 0), team="Red")
        friend = figure(2, "Friend", (1, 0), team="Red")
        foe = figure(3, "Foe", (0, 1), team="Blue")
        state = BattleState(arena_radius=4, combatants=[fighter, friend, foe])
        _advice, menu = self.menu(state, fighter)
        self.assertEqual({c.target_id for c in menu if c.letter == "j"}, {3})


class MenuEntryTest(BaseTestCase):
    """What each option says to the player who reads it (#788)."""

    def entries(self, state: BattleState, actor: CombatantState) -> list[dict]:
        advice = choose_option(state, actor)
        menu = turn_menu.player_menu(TARMAR_PROFILE, state, actor, advice)
        return [candidate.to_payload() for candidate in menu]

    def test_an_attack_reads_as_who_it_hits_and_the_odds(self):
        fighter = figure(1, "Aldwin", (0, 0))
        wolf = figure(2, "Gray Wolf", (1, 0), stops=1, armour_tier="Light")
        state = BattleState(arena_radius=4, combatants=[fighter, wolf])
        strike = next(
            entry for entry in self.entries(state, fighter) if entry["letter"] == "j"
        )
        self.assertEqual(strike["target_name"], "Gray Wolf")
        self.assertRegex(
            strike["plain"],
            r"^Attack Gray Wolf with the Broadsword: \d+ in 20 to hit, "
            r"2d6 less 1 armour \((about \d+|under 1) a hit\)$",
        )
        self.assertEqual(strike["cost"], "")
        # The score and its working are still there for the page to show.
        self.assertIn("score", strike)
        self.assertIn("P(hit)", strike["rationale"])

    def test_the_plain_odds_match_the_engine_working(self):
        fighter = figure(1, "Aldwin", (0, 0))
        wolf = figure(2, "Gray Wolf", (1, 0))
        state = BattleState(arena_radius=4, combatants=[fighter, wolf])
        strike = next(
            entry for entry in self.entries(state, fighter) if entry["letter"] == "j"
        )
        faces = int(strike["rationale"].split(" of 20 faces")[0].rsplit("(", 1)[1])
        self.assertIn(f": {faces} in 20 to hit", strike["plain"])

    def test_movement_says_what_it_costs(self):
        runner = figure(1, "Aldwin", (0, 0), move_run=10, move_sprint=14)
        far = figure(2, "Far", (5, 0))
        state = BattleState(arena_radius=6, combatants=[runner, far])
        entries = {entry["letter"]: entry for entry in self.entries(state, runner)}
        self.assertEqual(entries["a"]["plain"], "Run up to 10 hexes toward Far")
        self.assertEqual(entries["a"]["cost"], "1 Fatigue")
        self.assertEqual(entries["sprint"]["plain"], "Sprint up to 14 hexes toward Far")
        self.assertEqual(entries["sprint"]["cost"], "6 Fatigue")
        self.assertEqual(
            entries["a_yield"]["plain"],
            "Run up to 10 hexes toward Far, moving in Final Movement instead",
        )
        self.assertEqual(entries["c"]["cost"], "")

    def test_a_cast_costs_its_mana(self):
        wizard = figure(
            1,
            "Morwen",
            (0, 0),
            spells=["fire_missile"],
            mana=8,
            max_mana=8,
            intelligence=14,
        )
        foe = figure(2, "Foe", (3, 0))
        state = BattleState(arena_radius=6, combatants=[wizard, foe])
        # #816: a spell's mana is paid on a success, or on a failing total the
        # canon's casting_failure block names (tarmar-engine #46).
        for casting_failure, paid_when in (
            (CASTING_FAILURE, "paid only if it succeeds or the roll is 12 or 13"),
            (None, "paid only if it succeeds"),
        ):
            loaded = replace(magic_rules(), casting_failure=casting_failure)
            with self.subTest(paid_when=paid_when), seated_rules(loaded):
                cast = next(
                    entry for entry in self.entries(state, wizard) if entry["spell_key"]
                )
                self.assertEqual(cast["plain"], "Cast Fire Missile at Foe")
                self.assertRegex(cast["cost"], rf"^\d+ mana, {paid_when}$")

    def test_throwing_the_only_hand_weapon_says_so(self):
        thrower = figure(1, "Harar", (0, 0), weapon=_catalog_weapon("dagger"))
        foe = figure(2, "Foe", (3, 0))
        state = BattleState(arena_radius=6, combatants=[thrower, foe])
        throw = next(
            entry for entry in self.entries(state, thrower) if entry["letter"] == "f"
        )
        self.assertEqual(throw["cost"], "the Dagger leaves your hand")

    def test_exactly_one_option_is_the_ai_pick_and_it_is_the_engine_choice(self):
        archer = figure(1, "Dorra", (0, 0), weapon=_catalog_weapon("longbow"))
        near = figure(2, "Near", (0, 3), stops=6, armour_tier="Heavy")
        far = figure(3, "Far", (5, 0))
        state = BattleState(arena_radius=6, combatants=[archer, near, far])
        advice = choose_option(state, archer)
        entries = self.entries(state, archer)
        picked = [entry for entry in entries if entry["ai_choice"]]
        self.assertEqual(len(picked), 1)
        self.assertEqual(
            (picked[0]["letter"], picked[0]["target_id"]),
            (advice.chosen.letter, advice.chosen.target_id),
        )

    def test_a_classic_menu_is_the_engines_described_with_the_pick_marked(self):
        # Rewritten for #890: this handed a Tarmar state under the classic
        # profile's name and asserted no plain line, pinning the design in
        # which a classic menu was passed through undescribed. It now reads a
        # real classic GameState, as the pause does.
        from tarmar_engine.classic import policy as classic_policy

        from battle.tests.test_persistence import classic_game

        game = classic_game()
        fighter, _foe = game.figures
        advice = classic_policy.choose_option(game, fighter)
        menu = turn_menu.player_menu(CLASSIC_PROFILE, game, fighter, advice)
        self.assertEqual(
            [(c.letter, c.target_id, c.score) for c in menu],
            [(c.letter, c.target_id, c.score) for c in advice.candidates],
        )
        entries = [candidate.to_payload() for candidate in menu]
        self.assertTrue(all(entry["plain"] for entry in entries))
        self.assertEqual(sum(entry["ai_choice"] for entry in entries), 1)
        # The engine's own payload is unchanged but for what is added.
        engine_payload = advice.candidates[0].to_payload()
        self.assertEqual(
            {key: entries[0][key] for key in engine_payload}, engine_payload
        )


class PlayerAimsThroughTheBattleTest(BaseTestCase):
    """The widened menu, end to end through a paused battle (#775).

    A manual swordsman stands between two enemies. The pause offers a strike
    at each; the player strikes the one the AI would not, the replay accepts
    it, and the engine swings at that one.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(
            self.dm,
            name="Two foes",
            count=3,
            seed=4242,
            archetypes=("unarmoured duelist",),
        )
        state = BattleState.from_dict(self.battle.state_json)
        chooser, left, right = state.combatants
        chooser.position = (0, 0)
        left.position = (1, 0)
        right.position = (-1, 0)
        # The two foes cannot walk away before the swing lands.
        for foe in (left, right):
            foe.move_walk = foe.move_jog = foe.move_run = foe.move_sprint = 0
        self.chooser, self.left, self.right = chooser, left, right
        self.battle.state_json = dump_state(state)
        self.battle.save(update_fields=["state_json"])
        set_combatant_control_mode(self.battle.pk, chooser.combatant_id, control.MANUAL)

    def pause(self) -> Battle:
        battle, _events = advance_turn(self.battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_AWAITING)
        self.assertEqual(
            battle.pending_input_json["combatant_id"], self.chooser.combatant_id
        )
        return battle

    def test_the_pause_offers_a_strike_at_each_foe_and_marks_the_ai_pick(self):
        battle = self.pause()
        candidates = battle.pending_input_json["candidates"]
        strikes = [entry for entry in candidates if entry["letter"] == "j"]
        self.assertEqual(
            {entry["target_id"] for entry in strikes},
            {self.left.combatant_id, self.right.combatant_id},
        )
        self.assertEqual(sum(entry["ai_choice"] for entry in candidates), 1)
        self.assertTrue(all(entry["plain"] for entry in candidates))
        self.assertEqual(
            {entry["target_name"] for entry in strikes},
            {self.left.name, self.right.name},
        )

    def test_the_player_strikes_the_foe_the_ai_would_not(self):
        battle = self.pause()
        candidates = battle.pending_input_json["candidates"]
        ai_strike = next(
            (
                entry
                for entry in candidates
                if entry["letter"] == "j" and entry["ai_choice"]
            ),
            None,
        )
        engine_strike = ai_strike or next(
            entry for entry in candidates if entry["letter"] == "j"
        )
        other = next(
            entry
            for entry in candidates
            if entry["letter"] == "j"
            and entry["target_id"] != engine_strike["target_id"]
        )
        battle, _events = record_player_action(
            battle.pk,
            self.dm,
            combatant_id=self.chooser.combatant_id,
            turn=battle.pending_input_json["turn"],
            letter="j",
            target_id=other["target_id"],
        )
        self.assertNotEqual(battle.status, Battle.STATUS_AWAITING)
        decision = BattleEvent.objects.get(
            battle=battle, event_type="decision", actor=self.chooser.name
        )
        self.assertEqual(decision.payload["chosen"]["target_id"], other["target_id"])
        self.assertIn(f"Strike {other['target_name']}", decision.message)
        # The engine swings at the chosen foe.
        swing = BattleEvent.objects.get(
            battle=battle, event_type="action", actor=self.chooser.name
        )
        self.assertEqual(swing.payload["target"], other["target_id"])
        self.assertIn(other["target_name"], swing.message)

    def test_a_target_off_the_menu_is_refused(self):
        battle = self.pause()
        with self.assertRaisesMessage(ValueError, "not on the pending menu"):
            record_player_action(
                battle.pk,
                self.dm,
                combatant_id=self.chooser.combatant_id,
                turn=battle.pending_input_json["turn"],
                letter="j",
                target_id=self.chooser.combatant_id,
            )


class AiStandInTakesTheAiPickTest(BaseTestCase):
    """Handing the choice to the AI takes the AI's pick, not the best score.

    With every target on the menu, an archer's shot at a far, unarmoured foe
    can outscore the AI's own shot at the nearest (heavily armoured) one.
    "Have the AI choose" used to take the highest score on the stored menu.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()
        self.battle = create_battle(
            self.dm, name="Stand in", count=3, seed=4242, archetypes=("archer",)
        )
        state = BattleState.from_dict(self.battle.state_json)
        archer, near, far = state.combatants
        archer.position = (0, 0)
        near.position = (0, 3)
        far.position = (5, 0)
        near.stops, near.armour_tier = 8, "Heavy"
        far.stops, far.armour_tier = 0, "None"
        for foe in (near, far):
            foe.move_walk = foe.move_jog = foe.move_run = foe.move_sprint = 0
        self.archer, self.near, self.far = archer, near, far
        self.battle.state_json = dump_state(state)
        self.battle.save(update_fields=["state_json"])
        set_combatant_control_mode(self.battle.pk, archer.combatant_id, control.MANUAL)

    def test_the_marked_pick_is_recorded(self):
        battle, _events = advance_turn(self.battle.pk)
        self.assertEqual(
            battle.pending_input_json["combatant_id"], self.archer.combatant_id
        )
        candidates = battle.pending_input_json["candidates"]
        marked = next(entry for entry in candidates if entry["ai_choice"])
        best = max(candidates, key=lambda entry: entry["score"])
        # The situation this pins: a shot the AI did not choose scores higher.
        self.assertNotEqual(
            (best["letter"], best["target_id"]), (marked["letter"], marked["target_id"])
        )
        battle, _events = record_player_action(
            battle.pk,
            self.dm,
            combatant_id=self.archer.combatant_id,
            turn=battle.pending_input_json["turn"],
            letter="",
            as_ai=True,
        )
        decision = BattleEvent.objects.get(
            battle=battle, event_type="decision", actor=self.archer.name
        )
        self.assertEqual(
            (
                decision.payload["chosen"]["letter"],
                decision.payload["chosen"]["target_id"],
            ),
            (marked["letter"], marked["target_id"]),
        )


class PlainLineTest(BaseTestCase):
    """Every option's plain line and cost, letter by letter (#788)."""

    def setUp(self):
        super().setUp()
        self.actor = figure(
            1,
            "Aldwin",
            (0, 0),
            spare_weapons=[_catalog_weapon("dagger"), _catalog_weapon("longbow")],
        )
        self.foe = figure(2, "Foe", (1, 0))
        self.state = BattleState(
            arena_radius=4,
            combatants=[self.actor, self.foe],
            ground_weapons=[GroundWeapon(q=0, r=1, weapon=_catalog_weapon("mace"))],
        )

    def line(self, letter: str, *, target: bool = True) -> tuple[str, str]:
        described = turn_menu.describe(
            self.state,
            self.actor,
            Candidate(letter, letter.upper(), 0.0, "", target_id=2 if target else None),
            ai_choice=False,
        )
        return described.plain, described.cost

    def test_each_option_reads_as_what_it_does_and_costs(self):
        expected = {
            # Foe is already beside Aldwin, so no jog gets nearer (#57).
            "c": (
                "Dodge: +4 to your Target Number against missiles and thrown weapons only, "
                "standing, since no clear jog gets nearer to Foe",
                "",
            ),
            # A DROP may jog first to a picked hex (#866).
            "d": (
                "Drop prone where you stand, or first jog up to 7 hexes to a hex you pick",
                "",
            ),
            "e": ("Ready the Longbow", ""),
            "g": ("Stand up; it takes the whole turn", ""),
            "p": ("Stand up; it takes the whole turn", ""),
            "k": (
                "Defend: +4 to your Target Number against melee and non-missile attacks only",
                "",
            ),
            "m": ("Change to the Dagger", "the Broadsword is dropped"),
            "n": ("Step one hex away instead of attacking", ""),
            "o": ("Try to grapple Foe: a hold pins them rather than hurting them", ""),
            "q": ("Pick up the Mace", "the Broadsword is dropped"),
            "u": ("Try to draw your dagger: 3d6 at or under your DEX", ""),
            # Outside a grapple, v is the HTH DISENGAGE (tarmar-engine #19).
            "v": (
                "Break away from hand-to-hand: 4d6 at or under your DEX, then "
                "stand in a hex next to you",
                "",
            ),
            "hold_still": ("Hold still in Foe's grip", ""),
            "squeeze": ("Squeeze Foe", ""),
            "maintain": ("Keep your hold on Foe", ""),
            "release": ("Let Foe go", ""),
            "c_yield": (
                "Dodge: +4 to your Target Number against missiles and thrown weapons only, "
                "standing, since no clear jog gets nearer to Foe, unless a way "
                "opens by Final Movement",
                "",
            ),
        }
        for letter, wanted in expected.items():
            with self.subTest(letter=letter):
                self.assertEqual(self.line(letter), wanted)

    def block_the_step_away(self) -> list[CombatantState]:
        """Stand Aldwin's own side in every hex the engine would step him
        to, away from Foe, found through the engine's own
        ``movement.step_away_hex``: straight back, then either flank."""
        blockers: list[CombatantState] = []
        while (
            cell := movement.step_away_hex(self.state, self.actor, self.foe)
        ) is not None:
            blocker = figure(10 + len(blockers), f"Shield {len(blockers)}", cell)
            blocker.team = self.actor.team = "Red"
            self.state.combatants.append(blocker)
            blockers.append(blocker)
        return blockers

    def test_a_step_away_with_its_three_hexes_blocked_stays_put(self):
        """The engine steps n and v straight back or to either flank only,
        and logs "nowhere to step to" when all three are blocked (#57)."""
        self.assertEqual(len(self.block_the_step_away()), 3)
        stuck = (
            "with no hex picked you stay where you are, since no hex straight "
            "back from Foe or to either flank is clear"
        )
        # Another hex beside Aldwin is still clear for a player to pick.
        self.assertTrue(movement.legal_destinations(self.state, self.actor, "n"))
        self.assertEqual(
            self.line("n"),
            (f"Step one hex away instead of attacking: {stuck}", ""),
        )
        self.actor.hth_with = [self.foe.combatant_id]
        self.foe.hth_with = [self.actor.combatant_id]
        self.assertTrue(movement.legal_destinations(self.state, self.actor, "v"))
        self.assertEqual(
            self.line("v"),
            (f"Break away from hand-to-hand: 4d6 at or under your DEX; {stuck}", ""),
        )

    def test_a_step_away_with_a_clear_hex_steps(self):
        self.actor.hth_with = [self.foe.combatant_id]
        self.foe.hth_with = [self.actor.combatant_id]
        self.assertEqual(
            self.line("v"),
            (
                "Break away from hand-to-hand: 4d6 at or under your DEX, then "
                "stand in a hex next to you",
                "",
            ),
        )

    def test_a_dodge_with_no_missile_threat_names_nobody(self):
        self.assertEqual(
            self.line("c", target=False),
            (
                "Dodge: +4 to your Target Number against missiles and thrown weapons only",
                "",
            ),
        )

    def test_an_option_the_menu_does_not_know_is_named_as_the_engine_names_it(self):
        self.assertEqual(self.line("zz", target=False), ("Zz", ""))

    def test_an_attack_with_no_forecast_in_its_working_names_no_odds(self):
        self.assertEqual(self.line("j"), ("Attack Foe with the Broadsword", ""))

    def test_a_held_figure_struggles_free(self):
        self.actor.grappled_by = 2
        self.assertEqual(
            self.line("v"), ("Struggle free of Foe: 4d6 at or under your DEX", "")
        )

    def test_a_sword_in_hand_strikes_in_close_with_bare_hands(self):
        """The HTH table's t is "Bare hands or dagger" (tarmar-engine #19)."""
        self.assertEqual(self.line("t"), ("Strike Foe in close with bare hands", ""))
        self.actor.weapon = _catalog_weapon("dagger")
        self.assertEqual(self.line("t"), ("Strike Foe in close with the Dagger", ""))

    def test_bare_hands_and_nothing_to_ready(self):
        self.actor.weapon = WeaponState(damage="1d6-2")
        self.actor.spare_weapons = []
        self.state.ground_weapons = []
        self.assertEqual(self.line("t"), ("Strike Foe in close with bare hands", ""))
        self.assertEqual(self.line("e"), ("Ready another weapon", ""))
        self.assertEqual(self.line("m"), ("Change weapons", ""))
        self.assertEqual(self.line("q"), ("Pick up a weapon", ""))

    def test_the_odds_read_under_one_when_a_hit_barely_hurts(self):
        rationale = (
            "Strike Foe: Broadsword at 1 hex, d20 +0 vs TN 16 — P(hit) 0.250 "
            "(5 of 20 faces) x 0.078 expected damage (1d6-1 less 5 armour stops) "
            "= 0.020"
        )
        self.assertEqual(
            turn_menu._odds(rationale),
            "5 in 20 to hit, 1d6-1 less 5 armour (under 1 a hit)",
        )
        unarmoured = rationale.replace("less 5 armour", "less 0 armour")
        self.assertEqual(
            turn_menu._odds(unarmoured), "5 in 20 to hit, 1d6-1 (under 1 a hit)"
        )


class MoversSayTheMoveTheEngineMakesTest(BaseTestCase):
    """A DODGE, MOVE or SPRINT reads as the move the engine will make (#57).

    Since tarmar-engine 0.9.16 (#37) a figure no clear path brings nearer
    stands still, and the engine's DODGE working says "standing"; the menu
    still promised a jog toward the archer. Aldwin is boxed in by his own
    side here, three hexes from an archer, and in the open on the other
    board.
    """

    def menu(self, state: BattleState, actor: CombatantState) -> dict[str, dict]:
        advice = choose_option(state, actor)
        menu = turn_menu.player_menu(TARMAR_PROFILE, state, actor, advice)
        return {entry.letter: entry.to_payload() for entry in menu}

    def boxed_in(self) -> tuple[BattleState, CombatantState]:
        aldwin = figure(1, "Aldwin", (0, 0), team="Red", move_run=10, move_sprint=14)
        shields = [
            figure(10 + index, f"Shield {index}", cell, team="Red")
            for index, cell in enumerate(hexes.neighbors((0, 0)))
        ]
        bowman = figure(
            2, "Bowman", (3, 0), team="Blue", weapon=_catalog_weapon("longbow")
        )
        return (
            BattleState(arena_radius=5, combatants=[aldwin, *shields, bowman]),
            aldwin,
        )

    def in_the_open(self) -> tuple[BattleState, CombatantState]:
        aldwin = figure(1, "Aldwin", (0, 0), team="Red", move_run=10, move_sprint=14)
        bowman = figure(
            2, "Bowman", (4, 0), team="Blue", weapon=_catalog_weapon("longbow")
        )
        return BattleState(arena_radius=5, combatants=[aldwin, bowman]), aldwin

    def test_a_boxed_in_dodge_reads_standing_as_the_engine_does(self):
        entries = self.menu(*self.boxed_in())
        # The engine's own working says it will dodge standing.
        self.assertIn(
            "standing, since no clear jog gets nearer to Bowman",
            entries["c"]["rationale"],
        )
        self.assertEqual(
            entries["c"]["plain"],
            "Dodge: +4 to your Target Number against missiles and thrown weapons "
            "only, standing, since no clear jog gets nearer to Bowman",
        )
        self.assertEqual(
            entries["c_yield"]["plain"],
            "Dodge: +4 to your Target Number against missiles and thrown weapons "
            "only, standing, since no clear jog gets nearer to Bowman, unless a "
            "way opens by Final Movement",
        )

    def test_a_boxed_in_run_or_sprint_stands_and_costs_nothing_unless_it_moves(self):
        entries = self.menu(*self.boxed_in())
        # The engine's own working says a run gains nothing.
        self.assertIn("so moving gains nothing", entries["a"]["rationale"])
        self.assertEqual(
            (entries["a"]["plain"], entries["a"]["cost"]),
            (
                "Stand where you are, since no clear run gets nearer to Bowman",
                "1 Fatigue, only if you move",
            ),
        )
        self.assertEqual(
            (entries["sprint"]["plain"], entries["sprint"]["cost"]),
            (
                "Stand where you are, since no clear sprint gets nearer to Bowman",
                "6 Fatigue, only if you move",
            ),
        )
        self.assertEqual(
            entries["a_yield"]["plain"],
            "Stand where you are, since no clear run gets nearer to Bowman, "
            "unless a way opens by Final Movement",
        )

    def test_in_the_open_the_dodge_jogs_toward_the_archer(self):
        entries = self.menu(*self.in_the_open())
        self.assertIn("jogging toward Bowman", entries["c"]["rationale"])
        self.assertEqual(
            entries["c"]["plain"],
            "Dodge: +4 to your Target Number against missiles and thrown weapons "
            "only, jogging toward Bowman",
        )
        self.assertEqual(
            (entries["a"]["plain"], entries["a"]["cost"]),
            ("Run up to 10 hexes toward Bowman", "1 Fatigue"),
        )
        self.assertEqual(
            (entries["sprint"]["plain"], entries["sprint"]["cost"]),
            ("Sprint up to 14 hexes toward Bowman", "6 Fatigue"),
        )

    def test_a_boxed_in_charge_says_it_cannot_reach(self):
        """A charge no clear jog brings nearer stands and strikes nothing (#56)."""
        entries = self.menu(*self.boxed_in())
        # The engine's own working: out of reach, scored 0.
        self.assertIn("out of charge reach", entries["b"]["rationale"])
        self.assertEqual(entries["b"]["score"], 0.0)
        self.assertEqual(
            entries["b"]["plain"],
            "Charge Bowman: no attack, since no clear jog gets nearer to Bowman",
        )
        self.assertEqual(
            entries["b_yield"]["plain"],
            "Charge Bowman: no attack, since no clear jog gets nearer to Bowman, "
            "unless a way opens by Final Movement",
        )


class ChargeRowsReadTheEngineReachTest(BaseTestCase):
    """A CHARGE row is offered only at a target the engine's charge reaches (#56).

    The menu used to aim a charge at every enemy within a jog and a hex as
    the crow flies, so an enemy walled off by Aldwin's own side drew a
    positive forecast the engine would never act on: its jog along a clear
    path ends short, it scores the charge 0, and since tarmar-engine 0.9.16
    (#37) it charges a farther enemy it can reach instead.
    """

    def board(self, *, with_far: bool = True) -> tuple[BattleState, CombatantState]:
        """Aldwin, Near three hexes off ringed by Aldwin's side, and Far five
        hexes the other way in the open."""
        aldwin = figure(1, "Aldwin", (0, 0), team="Red")
        near = figure(2, "Near", (3, 0), team="Blue")
        wall = [
            figure(10 + index, f"Guard {index}", cell, team="Red")
            for index, cell in enumerate(hexes.neighbors((3, 0)))
        ]
        far = figure(3, "Far", (-5, 0), team="Blue")
        combatants = [aldwin, near, *wall, *([far] if with_far else [])]
        return BattleState(arena_radius=7, combatants=combatants), aldwin

    def engine_charge(self, state: BattleState, actor: CombatantState) -> Candidate:
        return next(
            candidate
            for candidate in choose_option(state, actor).candidates
            if candidate.letter == "b"
        )

    def menu(self, state: BattleState, actor: CombatantState) -> list[dict]:
        advice = choose_option(state, actor)
        return [
            entry.to_payload()
            for entry in turn_menu.player_menu(TARMAR_PROFILE, state, actor, advice)
        ]

    def test_only_the_reachable_enemy_is_offered_a_charge(self):
        state, aldwin = self.board()
        # The engine charges Far, past Near, which is the nearer.
        engine = self.engine_charge(state, aldwin)
        self.assertEqual(engine.target_id, 3)
        self.assertIn("Near is nearer but out of charge reach", engine.rationale)
        entries = self.menu(state, aldwin)
        for letter in ("b", "b_yield"):
            with self.subTest(letter=letter):
                rows = [entry for entry in entries if entry["letter"] == letter]
                self.assertEqual([row["target_id"] for row in rows], [3])
                self.assertEqual(rows[0]["score"], engine.score)

    def test_each_row_matches_the_engine_s_scoring_of_that_target_alone(self):
        state, aldwin = self.board()
        offered = {
            entry["target_id"]
            for entry in self.menu(state, aldwin)
            if entry["letter"] == "b"
        }
        for enemy in state.enemies_of(aldwin):
            with self.subTest(enemy=enemy.name):
                alone = replace(
                    state,
                    combatants=[
                        combatant
                        for combatant in state.combatants
                        if combatant.team == aldwin.team or combatant is enemy
                    ],
                )
                engine = self.engine_charge(alone, aldwin)
                # The engine reaches only what the menu offers a charge at.
                self.assertEqual(
                    "out of charge reach" not in engine.rationale,
                    enemy.combatant_id in offered,
                )

    def test_a_charge_that_ends_short_says_where_it_stops(self):
        state, aldwin = self.board(with_far=False)
        engine = self.engine_charge(state, aldwin)
        self.assertEqual((engine.target_id, engine.score), (2, 0.0))
        rows = {entry["letter"]: entry for entry in self.menu(state, aldwin)}
        self.assertEqual(
            rows["b"]["plain"],
            "Charge Near: no attack, since a clear jog of up to 7 hexes ends "
            "2 hexes from Near",
        )


class DefendAndDodgeDriftTest(SimpleTestCase):
    """DODGE and DEFEND say what the rules page says they guard against.

    special-combat-situations.md's "Defending and Dodging" table: Dodge
    "Missiles and thrown only", Defend "Melee and non-missile only". The
    first plain lines said Defend guarded against thrown attacks, which it
    does not (the engine applies it only to an attack that is not ranged,
    and a throw is ranged). Review of #788.
    """

    PAGE = (
        Path(settings.BASE_DIR)
        / "reference/content/public-rules/combat/action-options"
        / "special-combat-situations.md"
    )

    def table_row(self, action: str) -> str:
        """The "Defends Against" cell of ``action``'s row."""
        for line in self.PAGE.read_text().splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and cells[0] == action:
                return cells[2]
        self.fail(f"no {action} row in {self.PAGE}")

    def test_the_page_still_says_what_the_lines_were_written_from(self):
        self.assertEqual(self.table_row("Dodge"), "Missiles and thrown only")
        self.assertEqual(self.table_row("Defend"), "Melee and non-missile only")

    def test_the_plain_lines_carry_the_pages_words(self):
        for action, words in (
            ("Dodge", turn_menu.DODGE_DEFENDS_AGAINST),
            ("Defend", turn_menu.DEFEND_DEFENDS_AGAINST),
        ):
            with self.subTest(action=action):
                row = self.table_row(action).lower().split()
                # Every word of the cell is in the line, "only" included.
                self.assertTrue(
                    all(word in words.split() for word in row), (row, words)
                )


class EveryPlainLineCarriesItsOddsTest(BaseTestCase):
    """Each kind of attack line reads its odds off the engine's own working.

    The odds come from the engine's rationale prose (``turn_menu._odds``); a
    change to that wording must fail here rather than silently drop the odds
    from the menu. Every kind is built from a real engine menu, not a
    hand-written rationale. (Structured forecast fields are asked of the
    engine in smarks/tarmar-engine#22.)
    """

    ODDS = r": \d+ in 20 to hit, \S+( less \d+ armour)? \((about \d+|under 1) a hit\)"

    def plain_of(self, state, actor, letter: str) -> str:
        advice = choose_option(state, actor)
        menu = turn_menu.player_menu(TARMAR_PROFILE, state, actor, advice)
        entry = next(c for c in menu if c.letter == letter).to_payload()
        return entry["plain"]

    def test_attack_charge_shot_last_shot_and_close_strike(self):
        swordsman = figure(1, "Aldwin", (0, 0))
        foe = figure(2, "Foe", (1, 0), stops=1, armour_tier="Light")
        engaged = BattleState(arena_radius=4, combatants=[swordsman, foe])
        self.assertRegex(
            self.plain_of(engaged, swordsman, "j"),
            r"^Attack Foe with the Broadsword" + self.ODDS + "$",
        )

        far = figure(2, "Foe", (3, 0))
        apart = BattleState(arena_radius=6, combatants=[swordsman, far])
        charge = r"^Charge Foe: jog up to \d+ hexes and attack with the Broadsword"
        self.assertRegex(self.plain_of(apart, swordsman, "b"), charge + self.ODDS + "$")
        self.assertRegex(
            self.plain_of(apart, swordsman, "b_yield"),
            charge + self.ODDS + ", moving in Final Movement instead$",
        )

        archer = figure(1, "Dorra", (0, 0), weapon=_catalog_weapon("longbow"))
        shooting = BattleState(
            arena_radius=6, combatants=[archer, figure(2, "Foe", (3, 0))]
        )
        self.assertRegex(
            self.plain_of(shooting, archer, "f"),
            r"^Shoot Foe with the Longbow" + self.ODDS + "$",
        )
        cornered = BattleState(
            arena_radius=4, combatants=[archer, figure(2, "Foe", (1, 0))]
        )
        self.assertRegex(
            self.plain_of(cornered, archer, "l"),
            r"^One last shot at Foe with the Longbow" + self.ODDS + "$",
        )

        brawler = figure(
            1, "Harar", (0, 0), weapon=_catalog_weapon("dagger"), movement_modifier=2
        )
        slower = figure(2, "Foe", (1, 0), movement_modifier=0)
        close = BattleState(arena_radius=4, combatants=[brawler, slower])
        self.assertRegex(
            self.plain_of(close, brawler, "t"),
            r"^Strike Foe in close with the Dagger" + self.ODDS + "$",
        )
