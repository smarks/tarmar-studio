"""Dice fidelity guards for the battle engine (issue #290).

The engine→event column of ``docs/dice-fidelity-audit.md``, plus the
doc→engine guard for spell casting affinities. ``test_engine.py`` proves the
*behavior* of each phase; these tests pin the *roll contract* — that every
die the engine throws carries exactly the documented specification, the
modifier from the documented source, and the documented target number, and
that the emitted roll event mirrors the RollRecord field for field.

Doc sources: turn-sequence.md (initiative), attack-rolls.md (d20 attack,
confirm, 1d6 fumble), casting-spells.md and mana-pool.md (3d6 casting, the
extra DEX roll), spell-descriptions.md (per-spell affinity),
special-combat-situations.md (3d6 ≤ DEX retreat save, rolled only when
no neighbouring hex is clear), hand-to-hand-and-grappling.md (4d6 ≤ DEX
escape, and the entry conditions a grapple attempt needs), and
injury-thresholds-death.md (3d6 ≤ CON survival saves).

tarmar-engine 0.9.5 (studio #776–#826) changed what several of these rolls
are for, not their dice: a forced retreat is armed by physical hits and
pushes into any clear hex (#779, #813), so the blocked-retreat test boxes its
victim in; a grapple attempt needs an "Entering Hand-to-Hand" condition
(#823), so the severe-band test gives it one; and the injury band reaches
initiative and the retreat save (#817). The audit's rows A5–A7, A10, A13,
A14, C1, C6, C8, D3 and D5 say which test guards each.

Issue #291 (engine cast Protection's Shield on INT where spell-descriptions.md
says WIS) is fixed as of tarmar-engine v0.9.2 — the doc won, per Spencer's
ruling. ``SpellAffinityDocTest`` used to pin both sides of that known
mismatch so fixing either side would force a visit here; the pin is retired
now that the fix has landed and every catalog spell goes through the plain
"attribute matches the doc" assertion.
"""

import re

from django.test import SimpleTestCase
from tarmar_rules import dex_modifier

from battle.engine import combat_math, engine, hexes
from battle.spells import SPELLS
from reference.markdown_tables import split_table_row
from reference.paths import PUBLIC_RULES_DIR

from .test_engine import ScriptedRoller, box_in, duel_state, events_of_type
from .test_state import make_combatant

SPELL_DESCRIPTIONS_MD = PUBLIC_RULES_DIR / "magic" / "spell-descriptions.md"


def make_runner(state):
    events = []
    runner = engine.TurnRunner(state, ScriptedRoller(), events.append)
    return runner, events


def roll_events(events):
    return events_of_type(events, "roll")


class RollEventPayloadTest(SimpleTestCase):
    """Every roll event's payload and message mirror the RollRecord."""

    def test_payload_mirrors_the_record_field_for_field(self):
        runner, events = make_runner(duel_state())
        runner.roller = ScriptedRoller([[4, 2]])
        record, sequence = runner.roll(
            "2d6",
            purpose="audit",
            actor="Fighter 1",
            modifier=3,
            target_number=9,
        )
        event = roll_events(events)[0]
        self.assertEqual(event["sequence"], sequence)
        self.assertEqual(
            event["payload"],
            {
                "purpose": record.purpose,
                "specification": record.specification,
                "faces": list(record.faces),
                "modifier": record.modifier,
                "total": record.total,
                "target_number": record.target_number,
                "versus": "TN",
                "roll_under": False,
                "outcome": record.outcome,
            },
        )

    def test_message_spells_out_faces_modifier_total_and_target(self):
        runner, events = make_runner(duel_state())
        runner.roller = ScriptedRoller([[4, 2]])
        runner.roll(
            "2d6", purpose="audit", actor="Fighter 1", modifier=3, target_number=9
        )
        self.assertEqual(
            roll_events(events)[0]["message"],
            "Fighter 1 audit 2d6: [4, 2] +3 = 9 vs TN 9 (need 9+)",
        )

    def test_a_modifier_free_roll_omits_the_modifier_from_the_message(self):
        runner, events = make_runner(duel_state())
        runner.roller = ScriptedRoller([[5]])
        runner.roll("1d6", purpose="audit", actor="Fighter 1")
        self.assertEqual(
            roll_events(events)[0]["message"], "Fighter 1 audit 1d6: [5] = 5"
        )


class EngineRollSpecTest(SimpleTestCase):
    """Each engine roll uses the documented dice, modifier source and target."""

    def test_initiative_rolls_1d6_with_the_adjdex_modifier_and_no_target(self):
        # turn-sequence.md: "1d6 plus their adjDEX modifier", per combatant.
        state = duel_state()
        state.by_id(1).dexterity = 16
        runner, events = make_runner(state)
        runner.phase_initiative()
        first, second = roll_events(events)
        for event, combatant_id in ((first, 1), (second, 2)):
            combatant = state.by_id(combatant_id)
            self.assertEqual(event["payload"]["specification"], "1d6")
            self.assertEqual(event["payload"]["purpose"], "initiative")
            self.assertEqual(
                event["payload"]["modifier"], dex_modifier(combatant.dexterity)
            )
            self.assertIsNone(event["payload"]["target_number"])

    def test_attack_rolls_1d20_with_the_computed_bonus_and_target_number(self):
        # attack-rolls.md: d20 + to-hit bonus ≥ TN. The recorded modifier and
        # TN must be the very numbers combat_math computed — no re-derivation.
        state = duel_state()
        attacker, defender = state.by_id(1), state.by_id(2)
        expected = combat_math.attack_numbers(attacker, defender, ranged=False)
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[15], [4, 3]])
        runner.resolve_attack(attacker, defender, ranged=False)
        attack = roll_events(events)[0]
        self.assertEqual(attack["payload"]["specification"], "1d20")
        self.assertEqual(attack["payload"]["purpose"], "attack")
        self.assertEqual(attack["payload"]["modifier"], expected.bonus)
        self.assertEqual(attack["payload"]["target_number"], expected.target_number)
        damage = roll_events(events)[1]
        self.assertEqual(damage["payload"]["purpose"], "damage")
        self.assertEqual(damage["payload"]["specification"], attacker.weapon.damage)

    def test_confirm_rolls_a_second_d20_at_the_same_tn_and_bonus(self):
        # attack-rolls.md: "roll a second d20 to-hit against the same TN ...
        # with the same bonus".
        state = duel_state()
        attacker, defender = state.by_id(1), state.by_id(2)
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[20], [12], [6, 6], [6, 6]])
        runner.resolve_attack(attacker, defender, ranged=False)
        attack, confirm = roll_events(events)[0], roll_events(events)[1]
        self.assertEqual(confirm["payload"]["specification"], "1d20")
        self.assertEqual(confirm["payload"]["purpose"], "confirm")
        self.assertEqual(confirm["payload"]["modifier"], attack["payload"]["modifier"])
        self.assertEqual(
            confirm["payload"]["target_number"],
            attack["payload"]["target_number"],
        )

    def test_a_natural_one_rolls_the_1d6_fumble_subtable(self):
        state = duel_state()
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[1], [4]])
        runner.resolve_attack(state.by_id(1), state.by_id(2), ranged=False)
        fumble = roll_events(events)[1]
        self.assertEqual(fumble["payload"]["specification"], "1d6")
        self.assertEqual(fumble["payload"]["purpose"], "fumble")

    def test_casting_rolls_3d6_against_the_spell_attribute(self):
        # casting-spells.md: 3d6 ≤ INT or WIS depending on the spell.
        # fire_missile is INT; heal is WIS (spell-descriptions.md).
        for spell_key, attribute_name in (
            ("fire_missile", "intelligence"),
            ("heal", "wisdom"),
        ):
            with self.subTest(spell=spell_key):
                state = duel_state()
                caster = state.by_id(1)
                caster.mana = 10
                caster.intelligence = 13
                caster.wisdom = 15
                caster.chosen_spell = spell_key
                caster.chosen_target = 2
                runner, events = make_runner(state)
                runner.roller = ScriptedRoller([[6, 6, 6]])  # fails; one roll
                runner.cast_spell(caster)
                cast = roll_events(events)[0]
                self.assertEqual(cast["payload"]["specification"], "3d6")
                self.assertEqual(cast["payload"]["purpose"], "casting")
                self.assertEqual(cast["payload"]["modifier"], 0)
                self.assertEqual(
                    cast["payload"]["target_number"],
                    getattr(caster, attribute_name),
                )

    def test_targeted_spells_roll_an_additional_3d6_dex_check(self):
        # casting-spells.md: "Magic requiring hitting a target needs an
        # additional DEX roll."
        state = duel_state()
        caster = state.by_id(1)
        caster.mana = 10
        caster.chosen_spell = "fire_missile"
        caster.chosen_target = 2
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[3, 3, 3], [2, 2, 2], [4]])
        runner.cast_spell(caster)
        aim = roll_events(events)[1]
        self.assertEqual(aim["payload"]["specification"], "3d6")
        self.assertEqual(aim["payload"]["purpose"], "spell aim")
        self.assertEqual(aim["payload"]["target_number"], caster.dexterity)
        damage = roll_events(events)[2]
        self.assertEqual(
            damage["payload"]["specification"], SPELLS["fire_missile"].damage
        )

    def test_escape_rolls_4d6_against_effective_dex(self):
        # hand-to-hand-and-grappling.md: "roll 4d6 ≤ effective DEX" —
        # success frees, failure (total over DEX) stays held. Roll-under.
        state = duel_state()
        held, grappler = state.by_id(1), state.by_id(2)
        held.grappled_by = grappler.combatant_id
        grappler.grappling = held.combatant_id
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[4, 4, 4, 4]])  # 16 > DEX 12: fails
        runner.grapple_struggle_free(held)
        escape = roll_events(events)[0]
        self.assertEqual(escape["payload"]["specification"], "4d6")
        self.assertEqual(escape["payload"]["purpose"], "escape")
        self.assertEqual(escape["payload"]["target_number"], held.dexterity)
        self.assertIsNotNone(held.grappled_by)  # over the target: still held
        runner.roller = ScriptedRoller([[3, 3, 3, 3]])  # 12 ≤ DEX 12: frees
        runner.grapple_struggle_free(held)
        self.assertIsNone(held.grappled_by)

    def test_hth_disengage_rolls_4d6_against_effective_dex(self):
        # special-combat-situations.md, "From HTH (option v)": "Roll 4d6 ≤
        # DEX", "Success: stand up and move to adjacent empty hex", "Failure:
        # remain in HTH" (tarmar-engine #19, outside a grapple).
        state = duel_state()
        first, second = state.by_id(1), state.by_id(2)
        first.hth_with, second.hth_with = [2], [1]
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[4, 4, 4, 4]])  # 16 > DEX 12: stays in
        runner.hth_disengage(first)
        escape = roll_events(events)[0]
        self.assertEqual(escape["payload"]["specification"], "4d6")
        self.assertEqual(escape["payload"]["purpose"], "escape")
        self.assertEqual(escape["payload"]["target_number"], first.dexterity)
        self.assertEqual(first.hth_with, [2])
        runner.roller = ScriptedRoller([[3, 3, 3, 3]])  # 12 ≤ DEX 12: breaks away
        runner.hth_disengage(first)
        self.assertEqual((first.hth_with, second.hth_with), ([], []))
        self.assertFalse(combat_math.figures_adjacent(first, second))

    def test_blocked_retreat_saves_3d6_against_dex(self):
        # special-combat-situations.md: "No retreat hex → enemy rolls
        # 3d6 ≤ DEX or falls."
        state = duel_state()
        pusher, victim = state.by_id(1), state.by_id(2)
        victim.position = (6, 0)  # arena rim: nothing straight behind
        pusher.position = (5, 0)
        # The push goes "in any direction" (tarmar-engine 0.9.5, #779), so
        # the rim alone no longer blocks it: the two side hexes are filled.
        box_in(state, (6, -1), (5, 1))
        # "If you dealt physical hits and took none" (#813).
        pusher.dealt_physical_hit_this_turn = True
        pusher.chosen_target = 2
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[6, 6, 6]])
        runner.phase_forced_retreat()
        save = roll_events(events)[0]
        self.assertEqual(save["payload"]["specification"], "3d6")
        self.assertEqual(save["payload"]["purpose"], "retreat save")
        self.assertEqual(save["payload"]["target_number"], victim.dexterity)
        self.assertTrue(victim.prone)  # 18 > DEX 12: falls (roll-under)

    def test_survival_saves_3d6_against_con_with_the_excess_penalty(self):
        # injury-thresholds-death.md: below −½ max, save each turn 3d6 ≤ CON;
        # past −max the save is penalized by how far past the pool sits. The
        # engine adds the penalty to the roll — arithmetically identical to
        # subtracting it from CON, and the event shows the modifier.
        state = duel_state()
        fallen = state.by_id(2)
        fallen.fatigue = -45  # max 40: 5 past −max
        fallen.conscious = False
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[2, 2, 2]])
        runner.survival_saves()
        save = roll_events(events)[0]
        self.assertEqual(save["payload"]["specification"], "3d6")
        self.assertEqual(save["payload"]["purpose"], "survival")
        self.assertEqual(save["payload"]["target_number"], fallen.constitution)
        self.assertEqual(save["payload"]["modifier"], 5)
        self.assertTrue(fallen.alive)  # 6 + 5 = 11 ≤ CON 12: survives


class CastingFumbleVerdictTest(SimpleTestCase):
    """mana-pool.md's 16-18 casting-roll verdicts, proved through the app's
    own ``battle.engine`` re-export (issue #292)."""

    def test_a_roll_of_sixteen_fails_a_spell_a_high_attribute_would_pass(self):
        state = duel_state()
        caster = state.by_id(1)
        caster.mana = 10
        caster.intelligence = 18
        caster.chosen_spell = "fire_missile"
        caster.chosen_target = 2
        runner, events = make_runner(state)
        # 3d6 = [6, 6, 4] = 16 <= 18, but mana-pool.md's table fails it.
        runner.roller = ScriptedRoller([[6, 6, 4]])
        runner.cast_spell(caster)
        cast = roll_events(events)[0]
        self.assertEqual(cast["payload"]["total"], 16)
        self.assertIn("failure", events_of_type(events, "action")[0]["message"])
        self.assertEqual(len(roll_events(events)), 1)  # no aim/damage roll

    def test_a_roll_of_fifteen_still_succeeds(self):
        # Regression guard: only 16-18 change the verdict.
        state = duel_state()
        caster = state.by_id(1)
        caster.mana = 10
        caster.wisdom = 18  # shield rolls against WIS (#291)
        caster.chosen_spell = "shield"
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[6, 6, 3]])  # 15 <= 18
        runner.cast_spell(caster)
        self.assertEqual(caster.active_spells, ["shield"])


class InjuryRollPenaltyThroughTheAppTest(SimpleTestCase):
    """injury-thresholds-death.md's -1/-2 band, proved through the app's own
    ``battle.engine`` re-export (issue #296). Survival saves are untouched —
    ``EngineRollSpecTest.test_survival_saves_3d6_against_con_with_the_excess_penalty``
    above already pins that separate penalty arithmetic."""

    def test_attack_roll_modifier_drops_by_two_in_the_severe_band(self):
        state = duel_state()
        attacker = state.by_id(1)
        attacker.fatigue = 5  # max_fatigue 40: the 1-5 band, -2.
        expected = combat_math.attack_numbers(attacker, state.by_id(2), ranged=False)
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[15], [4, 3]])
        runner.resolve_attack(attacker, state.by_id(2), ranged=False)
        attack = roll_events(events)[0]
        self.assertEqual(attack["payload"]["modifier"], expected.bonus - 2)

    def test_casting_and_escape_target_numbers_drop_by_one_at_half_health(self):
        state = duel_state()
        caster = state.by_id(1)
        caster.mana = 10
        caster.fatigue = 20  # max_fatigue 40: at half, -1.
        caster.chosen_spell = "fire_missile"
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[3, 3, 3]])
        runner.cast_spell(caster)
        cast = roll_events(events)[0]
        self.assertEqual(cast["payload"]["target_number"], caster.intelligence - 1)

        held = state.by_id(2)
        held.grappled_by = 1
        state.by_id(1).grappling = 2
        held.fatigue = 20  # at half, -1.
        runner2, events2 = make_runner(state)
        runner2.roller = ScriptedRoller([[3, 3, 3, 3]])
        runner2.grapple_struggle_free(held)
        escape = roll_events(events2)[0]
        self.assertEqual(escape["payload"]["target_number"], held.dexterity - 1)

    def test_grapple_attempt_modifier_drops_by_two_in_the_severe_band(self):
        """The gap this class used to leave open (#672).

        Attack, cast and escape were pinned here; the grapple attempt was
        not, and it was the one roll path that never subtracted the band —
        the same fighter punched at -2 and grabbed at +0. Fixed in the
        engine by smarks/tarmar-engine#12 and pinned here so the enumeration
        above is complete.
        """
        state = duel_state()
        attacker = state.by_id(1)
        attacker.fatigue = 5  # max_fatigue 40: the 1-5 band, -2.
        attacker.chosen_target = 2
        # A way into hand-to-hand (tarmar-engine 0.9.5, #823): the defender
        # is slower. The movement modifier is in no to-hit number.
        state.by_id(2).movement_modifier = -1
        expected = combat_math.attack_numbers(
            attacker,
            state.by_id(2),
            ranged=False,
            weapon_class="Flexible / Snare",
            extra_situational=hexes.HTH_TO_HIT_BONUS,
            ignore_attacker_skill=True,
        )
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[9]])
        runner.attempt_grapple(attacker)
        grab = roll_events(events)[0]
        self.assertEqual(grab["payload"]["purpose"], "grapple attempt")
        self.assertEqual(grab["payload"]["modifier"], expected.bonus - 2)


class SpellAffinityDocTest(SimpleTestCase):
    """spell-descriptions.md's Affinity column vs the engine catalog.

    The battle drift guard checks names and levels against
    schools-of-magic.md; this closes the remaining seam — *which attribute*
    the 3d6 casting roll targets. Issue #291 (Shield casting on INT where
    the doc says WIS) was the mismatch that motivated this guard; it is
    fixed as of tarmar-engine v0.9.2, and every catalog spell — Shield
    included — now goes through the plain equality assertion below.
    """

    @staticmethod
    def _documented_affinity(school: str, base_name: str, level: int) -> str:
        markdown = SPELL_DESCRIPTIONS_MD.read_text()
        section = markdown.split(f"## {school}", 1)[1].split("\n## ", 1)[0]
        for line in section.splitlines():
            if not line.startswith("|"):
                continue
            cells = split_table_row(line)
            if len(cells) < 3:
                continue
            # "Shield (Protection)" -> "Shield"; the parenthetical
            # disambiguates same-named spells across schools.
            name = re.sub(r"\s*\(.*\)$", "", cells[0])
            if name != base_name:
                continue
            level_match = re.match(r"\d+", cells[1])
            if level_match is None or int(level_match.group()) != level:
                continue
            return cells[2]
        raise AssertionError(
            f"{base_name} (level {level}) not found in spell-descriptions.md's "
            f"{school} table"
        )

    def test_every_catalog_spell_casts_on_the_documented_attribute(self):
        for spell in SPELLS.values():
            base_name = spell.name.split()[-1]
            documented = self._documented_affinity(spell.school, base_name, spell.level)
            with self.subTest(spell=spell.key):
                self.assertEqual(
                    spell.attribute,
                    documented,
                    f"{spell.key}: casting attribute drifted from "
                    "spell-descriptions.md",
                )


class BareHandedDamageRollTest(SimpleTestCase):
    """A bare-handed HTH strike rolls the documented STR-band expression."""

    def test_squeeze_rolls_the_str_band_expression(self):
        # special-combat-situations.md: STR 15-16 -> 1d6. The grappler's
        # Squeeze is a bare-handed strike using that table.
        state = duel_state()
        grappler, held = state.by_id(1), state.by_id(2)
        grappler.strength = 15
        grappler.grappling = held.combatant_id
        held.grappled_by = grappler.combatant_id
        runner, events = make_runner(state)
        runner.roller = ScriptedRoller([[15], [4]])
        runner.grapple_squeeze(grappler)
        damage = roll_events(events)[1]
        self.assertEqual(damage["payload"]["specification"], "1d6")


class GrappleStateFactoryNote(SimpleTestCase):
    """Guard the factory assumption the escape tests lean on."""

    def test_default_combatant_rolls_the_documented_broadsword_damage(self):
        # weapons.md: Broadsword 2d6 — the factory weapon every attack
        # spec test above rolls damage with.
        self.assertEqual(make_combatant(1).weapon.damage, "2d6")
