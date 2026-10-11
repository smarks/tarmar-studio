"""Studio roster → classic figures (issue #251, slice 6).

Slice 5 taught the turn loop the profile seam but built its classic states by
hand. These pin the crossing itself: what a studio combatant becomes under
classic rules, what the crossing costs, and that the result is a state this
app can store, reload and advance.
"""

from dataclasses import replace

from battle import profiles, viewmodel
from battle.classic_adaptation import (
    ARENA_SEATS,
    BASE_ARENA_COLS,
    arena_rim,
    classic_arena,
    classic_armor,
    classic_figure,
    classic_figures,
    classic_shield,
    classic_side,
    classic_state,
    classic_team,
    classic_weapon,
    name_sides_in_lines,
    rim_placements,
    wizard_spells,
)
from battle.engine.state import CombatantState, WeaponState
from battle.models import BattleEvent
from battle.persistence import CLASSIC_PROFILE, TARMAR_PROFILE
from common.test_helpers import BaseTestCase


def snapshot(
    combatant_id: int,
    name: str,
    *,
    strength: int = 12,
    dexterity: int = 12,
    intelligence: int = 10,
    weapon: WeaponState | None = None,
    armour_tier: str = "Light",
    stops: int = 2,
    shield_bonus: int = 0,
    spells: tuple[str, ...] = (),
    archetype: str = "fighter",
) -> CombatantState:
    """One studio combatant snapshot, the input side of the crossing."""
    return CombatantState(
        combatant_id=combatant_id,
        name=name,
        archetype=archetype,
        strength=strength,
        dexterity=dexterity,
        intelligence=intelligence,
        wisdom=10,
        constitution=10,
        max_fatigue=10,
        max_body=10,
        fatigue=10,
        body=10,
        weapon=weapon
        or WeaponState(name="Broadsword", weapon_class="Sword", damage="2d6"),
        weapon_skill_level=0,
        armour_tier=armour_tier,
        stops=stops,
        shield_bonus=shield_bonus,
        move_walk=4,
        move_jog=7,
        move_run=12,
        spells=list(spells),
    )


class ClassicWeaponTest(BaseTestCase):
    """Which classic weapon a studio combatant ends up holding."""

    def test_matching_name_is_the_same_weapon(self):
        weapon, matched = classic_weapon(
            WeaponState(name="Broadsword", weapon_class="Sword", damage="2d6"), 12
        )
        self.assertEqual(weapon.name, "Broadsword")
        self.assertTrue(matched)

    def test_name_match_ignores_case_and_spacing(self):
        weapon, matched = classic_weapon(
            WeaponState(name="  two-handed SWORD ", weapon_class="Sword", damage="3d6"),
            15,
        )
        self.assertEqual(weapon.name, "Two-handed sword")
        self.assertTrue(matched)

    def test_unknown_melee_weapon_becomes_the_closest_by_damage(self):
        """A studio weapon with no classic twin keeps its kind and its weight."""
        from tarmar_engine.classic.data import WeaponKind

        weapon, matched = classic_weapon(
            WeaponState(name="Glaive of Woe", weapon_class="Polearm", damage="2d6+1"),
            14,
        )
        self.assertFalse(matched)
        self.assertIn(weapon.kind, {WeaponKind.MELEE, WeaponKind.POLE})
        # 2d6+1 averages 8.0; the closest wieldable classic weapon is no more
        # than a die's worth away from that.
        average = weapon.damage.count * 3.5 + weapon.damage.modifier
        self.assertLess(abs(average - 8.0), 3.5)

    def test_a_missile_weapon_stays_a_missile_weapon(self):
        from tarmar_engine.classic.data import WeaponKind

        weapon, _matched = classic_weapon(
            WeaponState(
                name="Elfshot Bow",
                weapon_class="Missile Bow",
                damage="1d6+2",
                is_missile=True,
            ),
            12,
        )
        self.assertEqual(weapon.kind, WeaponKind.MISSILE)

    def test_a_weapon_too_heavy_for_this_strength_is_never_chosen(self):
        weapon, _matched = classic_weapon(
            WeaponState(name="Doom Cleaver", weapon_class="Axe", damage="4d6"), 8
        )
        self.assertLessEqual(weapon.min_strength, 8)

    def test_a_named_weapon_too_heavy_to_wield_falls_through(self):
        """Classic gates weapons on ST, and a Figure refuses to hold one it
        cannot wield — so a name match alone is not enough."""
        weapon, matched = classic_weapon(
            WeaponState(name="Mace", weapon_class="Club", damage="2d6-1"), 6
        )
        self.assertNotEqual(weapon.name, "Mace")
        self.assertLessEqual(weapon.min_strength, 6)
        self.assertFalse(matched)

    def test_bare_hands_fall_back_rather_than_arming_a_brawler(self):
        """A bare-handed studio combatant gets the rulebook's own minimum."""
        weapon, matched = classic_weapon(WeaponState(damage="1d6-2"), 10)
        self.assertFalse(matched)
        self.assertLessEqual(weapon.min_strength, 10)


class ClassicGearTest(BaseTestCase):
    """Armour and shields across the two catalogues."""

    def test_each_studio_tier_maps_to_its_classic_armour(self):
        self.assertEqual(classic_armor("None", 0).name, "None")
        self.assertEqual(classic_armor("Light", 2).name, "Leather")
        self.assertEqual(classic_armor("Medium", 3).name, "Chainmail")
        self.assertEqual(classic_armor("Heavy", 5).name, "Plate")

    def test_stopping_power_breaks_a_tie_the_tier_cannot(self):
        """Heavy armour that stops 4 is half-plate, not inflated to plate."""
        self.assertEqual(classic_armor("Heavy", 4).name, "Half-plate")

    def test_an_unknown_tier_is_unarmoured_rather_than_a_guess(self):
        self.assertEqual(classic_armor("Mithril", 0).name, "None")

    def test_shield_bonus_buys_the_shield_that_stops_as_much(self):
        self.assertEqual(classic_shield(0).name, "None")
        self.assertEqual(classic_shield(1).name, "Small shield")
        self.assertEqual(classic_shield(2).name, "Large shield")

    def test_an_oversized_shield_bonus_stops_at_the_largest_shield(self):
        self.assertEqual(classic_shield(9).name, "Large shield")


class ClassicFigureTest(BaseTestCase):
    """One combatant crossing over."""

    def test_the_figure_carries_its_row_pk_as_its_uid(self):
        """``profiles.CLASSIC_UID_IS_ROW_PK`` is the contract this honours."""
        from hexarena.hex import Hex

        figure = classic_figure(snapshot(77, "Ada"), position=Hex(1, 1), facing=0)
        self.assertEqual(figure.uid, "77")

    def test_attributes_cross_without_rescaling(self):
        from hexarena.hex import Hex

        figure = classic_figure(
            snapshot(1, "Ada", strength=13, dexterity=11, intelligence=14),
            position=Hex(1, 1),
            facing=2,
        )
        self.assertEqual((figure.strength, figure.dexterity), (13, 11))
        self.assertEqual(figure.intelligence, 14)
        self.assertEqual(figure.name, "Ada")
        self.assertEqual(figure.facing, 2)

    def test_every_figure_is_its_own_side_so_the_battle_is_a_free_for_all(self):
        """A studio battle has no teams; classic sides are per-figure here."""
        arena = classic_arena(3)
        roster = classic_figures(
            [snapshot(index, f"F{index}") for index in (1, 2, 3)], arena
        )
        sides = {figure.side for figure in roster.figures}
        self.assertEqual(len(sides), 3)
        first, rest = roster.figures[0], roster.figures[1:]
        enemies = {figure.uid for figure in rest}
        self.assertEqual(
            {one.uid for one in roster.figures if one.side != first.side}, enemies
        )

    def test_attributes_are_floored_where_classic_forbids_zero(self):
        """A studio combatant ground to DX 0 by encumbrance still fights."""
        from hexarena.hex import Hex

        figure = classic_figure(
            snapshot(1, "Morin", strength=0, dexterity=0, intelligence=0),
            position=Hex(1, 1),
            facing=0,
        )
        self.assertEqual(figure.strength, 1)
        self.assertEqual(figure.dexterity, 1)
        self.assertEqual(figure.intelligence, 1)

    def test_a_figure_carries_a_dagger_alongside_its_weapon(self):
        from hexarena.hex import Hex

        figure = classic_figure(snapshot(1, "Ada"), position=Hex(1, 1), facing=0)
        self.assertIsNotNone(figure.ready_weapon)
        assert figure.ready_weapon is not None  # narrows for the type checker
        self.assertEqual(figure.ready_weapon.name, "Broadsword")
        self.assertIn("Dagger", [weapon.name for weapon in figure.weapons])

    def test_a_dagger_wielder_is_not_given_two_daggers(self):
        from hexarena.hex import Hex

        figure = classic_figure(
            snapshot(
                1,
                "Ada",
                weapon=WeaponState(name="Dagger", weapon_class="Knife", damage="1d6-1"),
            ),
            position=Hex(1, 1),
            facing=0,
        )
        self.assertEqual([weapon.name for weapon in figure.weapons], ["Dagger"])


class ClassicRosterTest(BaseTestCase):
    """What the whole roster's crossing costs, reported rather than dropped."""

    def test_a_weapon_with_no_classic_twin_is_reported(self):
        arena = classic_arena(2)
        roster = classic_figures(
            [
                snapshot(1, "Ada"),
                snapshot(
                    2,
                    "Bram",
                    weapon=WeaponState(
                        name="Glaive of Woe", weapon_class="Polearm", damage="2d6+1"
                    ),
                ),
            ],
            arena,
        )
        self.assertEqual(roster.disarmed, ("Bram",))

    def test_a_caster_crosses_as_a_classic_wizard(self):
        """A studio caster is a wizard here — a *classic* one (slice 8).

        Slice 6 crossed a caster as a fighter, because the spell-canon
        ruling refuses to map one catalog onto the other. That ruling is
        untouched: none of the studio's own spell keys appear. What the
        crossing now does instead is classic chargen — the same move it
        already made for weapons and armour.
        """
        arena = classic_arena(2)
        roster = classic_figures(
            [snapshot(1, "Ada"), snapshot(2, "Wynn", spells=("mage bolt",))], arena
        )
        self.assertEqual(roster.wizards, ("Wynn",))
        wizard = roster.figures[1]
        self.assertNotIn("mage bolt", wizard.spells_known)
        self.assertTrue(wizard.spells_known)
        self.assertTrue(wizard.has_staff)

    def test_a_crossed_wizard_can_actually_cast(self):
        """The hands test, not just the spell list (Wizard p.23).

        A wizard holding a broadsword or a shield knows spells it can never
        cast: ``cast_block_reason`` refuses both. The studio's casters carry
        weapons, so a crossing that kept them readied would build wizards
        that are wizards on paper only.
        """
        from tarmar_engine.classic.state import cast_block_reason

        arena = classic_arena(2)
        roster = classic_figures(
            [
                snapshot(1, "Ada"),
                snapshot(2, "Wynn", spells=("mage bolt",), shield_bonus=2),
            ],
            arena,
        )
        wizard = roster.figures[1]
        self.assertIsNone(cast_block_reason(wizard))
        self.assertEqual(wizard.shield.name, "None")

    def test_a_caster_below_every_spell_tier_fights_as_a_warrior(self):
        """The IQ 7 caster: a fighter, and armed like one.

        The classic catalog starts at IQ 8, so this caster reaches no spell.
        It must not be handed a wizard's empty hands — no shield, no readied
        weapon — or the crossing would send it in unarmed for the sake of
        magic it does not have.
        """
        arena = classic_arena(2)
        roster = classic_figures(
            [
                snapshot(1, "Ada"),
                snapshot(
                    2, "Wynn", spells=("mage bolt",), intelligence=7, shield_bonus=2
                ),
            ],
            arena,
        )
        self.assertEqual(roster.unspelled, ("Wynn",))
        self.assertEqual(roster.wizards, ())
        dabbler = roster.figures[1]
        self.assertEqual(dabbler.spells_known, [])
        self.assertIsNotNone(dabbler.ready_weapon)
        self.assertNotEqual(dabbler.shield.name, "None")

    def test_a_fighter_is_still_a_fighter(self):
        arena = classic_arena(2)
        roster = classic_figures([snapshot(1, "Ada"), snapshot(2, "Bea")], arena)
        self.assertEqual(roster.figures[0].spells_known, [])
        self.assertFalse(roster.figures[0].has_staff)

    def test_a_clean_roster_reports_no_losses(self):
        arena = classic_arena(2)
        roster = classic_figures([snapshot(1, "Ada"), snapshot(2, "Bea")], arena)
        self.assertEqual(
            (roster.disarmed, roster.wizards, roster.unspelled), ((), (), ())
        )


class WizardSpellsTest(BaseTestCase):
    """Which classic spells a crossing caster ends up knowing.

    The rule is classic chargen's own, quoted in melee's validator: "a
    wizard may know at most IQ spells", none above its IQ tier. Nothing
    here reads the studio's spell list — that is the spell-canon ruling.
    """

    def test_only_spells_within_the_wizards_iq_tier(self):
        from tarmar_engine.classic.spells import SPELLS

        for intelligence in (8, 10, 12, 14):
            known = wizard_spells(intelligence)
            self.assertTrue(known, f"IQ {intelligence} learned nothing")
            for key in known:
                self.assertLessEqual(SPELLS[key].iq_tier, intelligence)

    def test_never_more_spells_than_iq(self):
        """The count bound holds at every IQ, even where the tiers bind first.

        With this catalog (14 spells, tiers 8-15) the tier gate is always
        the tighter of the two, so the count bound never actually trims a
        hand. It is still applied and pinned here rather than left to that
        arithmetic: a catalog that grew a dozen IQ 8 spells would otherwise
        hand an IQ 8 wizard an illegal list, silently.
        """
        for intelligence in range(1, 20):
            self.assertLessEqual(len(wizard_spells(intelligence)), intelligence)

    def test_a_low_iq_caster_learns_nothing_rather_than_an_illegal_spell(self):
        """IQ 7 is below every tier in the catalog — an empty hand, not a lie."""
        self.assertEqual(wizard_spells(7), [])

    def test_the_hand_is_deterministic(self):
        self.assertEqual(wizard_spells(12), wizard_spells(12))

    def test_the_staff_spell_comes_first_so_the_wizard_has_a_free_hand(self):
        """Staff is what arms a wizard to cast at all (Wizard p.19, p.23)."""
        self.assertIn("staff", wizard_spells(8))


class ClassicPlacementTest(BaseTestCase):
    """Where the figures start."""

    def test_the_arena_grows_with_the_roster(self):
        self.assertEqual(classic_arena(ARENA_SEATS).cols, BASE_ARENA_COLS)
        self.assertEqual(classic_arena(ARENA_SEATS + 3).cols, BASE_ARENA_COLS + 3)

    def test_the_rim_is_inside_the_arena_and_walks_a_cycle(self):
        arena = classic_arena(4)
        rim = arena_rim(arena)
        self.assertTrue(all(arena.contains(one) for one in rim))
        self.assertEqual(len(rim), len(set(rim)))
        # Consecutive rim hexes are neighbours, including the wrap.
        for index, here in enumerate(rim):
            nxt = rim[(index + 1) % len(rim)]
            self.assertLessEqual(arena.distance(here, nxt), 1 + 1)

    def test_figures_start_on_distinct_hexes_facing_the_middle(self):
        from hexarena.hex import Hex
        from tarmar_engine.classic.facing import facing_toward

        arena = classic_arena(6)
        placements = rim_placements(arena, 6)
        self.assertEqual(len({position for position, _ in placements}), 6)
        middle = Hex((arena.cols + 1) // 2, (arena.rows + 1) // 2)
        for position, facing in placements:
            self.assertEqual(facing, facing_toward(arena.layout, position, middle))

    def test_nobody_starts_the_fight_already_engaged(self):
        """An opening where two figures touch would skip the approach."""
        arena = classic_arena(6)
        placements = rim_placements(arena, 6)
        positions = [position for position, _ in placements]
        for index, here in enumerate(positions):
            for there in positions[index + 1 :]:
                self.assertGreater(arena.distance(here, there), 1)


class ClassicStateTest(BaseTestCase):
    """The state this app will actually store and advance."""

    def test_the_state_holds_the_whole_roster_and_a_seeded_die(self):
        state, roster = classic_state(
            [snapshot(index, f"F{index}") for index in (1, 2, 3)], seed=42
        )
        self.assertEqual(len(state.figures), 3)
        self.assertEqual(len(roster.figures), 3)
        self.assertEqual(state.turn_number, 1)

    def test_the_state_survives_the_snapshot_round_trip(self):
        """What ``Battle.state_json`` will hold has to come back the same."""
        import json

        from battle.persistence import CLASSIC_PROFILE, dump_state, load_state

        state, _roster = classic_state(
            [snapshot(index, f"F{index}") for index in (11, 12)], seed=7
        )
        snapshot_json = json.loads(json.dumps(dump_state(state)))
        restored = load_state(snapshot_json, CLASSIC_PROFILE)
        self.assertEqual(
            [(one.uid, one.name, one.position) for one in restored.figures],
            [(one.uid, one.name, one.position) for one in state.figures],
        )

    def test_a_built_roster_advances_a_turn(self):
        """The bar for this slice: a battle built here is actually playable.

        The runner enacts a chosen ``Candidate``; the classic policy returns
        the whole ``Decision`` (its menu plus the pick), which is what
        ``battle.profiles`` logs before handing the pick down. That unwrapping
        is repeated here so this stays a test of the roster, not of the seam.
        """
        from hexarena.dice import Dice
        from tarmar_engine.classic import policy as classic_policy
        from tarmar_engine.profile import get_profile

        state, _roster = classic_state(
            [snapshot(index, f"F{index}") for index in (1, 2)], seed=5
        )
        lines: list[str] = []
        get_profile("classic-melee").run_turn(
            state,
            Dice(seed=5),
            lines.append,
            lambda game, figure: classic_policy.choose_option(game, figure).chosen,
        )
        self.assertTrue(lines)
        self.assertEqual(state.turn_number, 2)


class ClassicBattleCreationTest(BaseTestCase):
    """``create_battle`` under the classic profile — the slice's actual point.

    Everything above pins the crossing in isolation; these pin the path a DM
    walks: pick classic on the create form, get a stored battle, play it.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()

    def make_battle(self, **kwargs):
        from battle.persistence import CLASSIC_PROFILE
        from battle.services import create_battle

        parameters = {"name": "Arena", "count": 3, "seed": 4242}
        parameters.update(kwargs)
        return create_battle(self.dm, rules_profile=CLASSIC_PROFILE, **parameters)

    def test_a_classic_battle_stores_classic_state(self):
        from battle.persistence import CLASSIC_PROFILE, load_state

        battle = self.make_battle()
        self.assertEqual(battle.rules_profile, CLASSIC_PROFILE)
        self.assertEqual(battle.combatants.count(), 3)
        state = load_state(battle.state_json, CLASSIC_PROFILE)
        self.assertEqual(len(state.figures), 3)

    def test_every_figure_uid_is_its_combatant_row(self):
        """``profiles.CLASSIC_UID_IS_ROW_PK`` end to end, through the DB."""
        from battle.persistence import CLASSIC_PROFILE, load_state

        battle = self.make_battle()
        state = load_state(battle.state_json, CLASSIC_PROFILE)
        self.assertEqual(
            {figure.uid for figure in state.figures},
            {str(pk) for pk in battle.combatants.values_list("id", flat=True)},
        )

    def test_the_frozen_copy_matches_the_playable_one(self):
        """Restart rewinds to ``initial_state_json``; it has to be the same."""
        battle = self.make_battle()
        self.assertEqual(battle.state_json, battle.initial_state_json)

    def test_the_roster_is_the_same_one_a_tarmar_battle_would_get(self):
        """Only the rules change: same seed, same characters, same names."""
        from battle.persistence import TARMAR_PROFILE
        from battle.services import create_battle

        classic = self.make_battle(name="Twin", count=3, seed=99)
        tarmar = create_battle(
            self.dm,
            name="Twin",
            count=3,
            seed=99,
            rules_profile=TARMAR_PROFILE,
        )
        self.assertEqual(
            list(classic.combatants.values_list("display_name", flat=True)),
            list(tarmar.combatants.values_list("display_name", flat=True)),
        )

    def test_a_classic_battle_advances_a_turn_through_the_app(self):
        """The whole slice in one line: created here, played by the app."""
        from battle.services import advance_turn

        battle = self.make_battle()
        battle, events = advance_turn(battle.pk)
        self.assertTrue(events)
        self.assertEqual(battle.turn_number, 1)
        sequences = [event.sequence for event in battle.events.all()]
        self.assertEqual(len(sequences), len(set(sequences)))

    def test_a_beast_crosses_over_with_the_characters(self):
        """Beasts snapshot to the same dataclass, so they cross for free."""
        from battle.persistence import CLASSIC_PROFILE, load_state
        from bestiary.models import Beast

        catalog_beast = Beast.objects.filter(is_generated=False).first()
        if catalog_beast is None:
            self.skipTest("no catalog beasts in this database")
        battle = self.make_battle(count=2, beast_slugs=(catalog_beast.slug,))
        state = load_state(battle.state_json, CLASSIC_PROFILE)
        self.assertEqual(len(state.figures), 3)

    def test_a_dropped_spell_is_recorded_in_the_battle_log(self):
        """The crossing's cost is state, not a banner that disappears."""
        battle = self.make_battle(count=6, seed=4242)
        messages = [event.message for event in battle.events.all()]
        if not messages:
            self.skipTest("this seed's roster crossed without loss")
        self.assertTrue(all(message for message in messages))

    def test_a_classic_lobby_holds_its_crossing_until_the_start(self):
        """The refusal this replaces is gone (#251, the classic lobby).

        A classic battle may now open player seats. What creation must NOT
        do is cross: the roster is unsettled, so the battle waits in its
        lobby with no state and no crossing log, exactly as a Tarmar lobby
        does. :class:`battle.tests.test_lobby.ClassicLobbyTest` carries the
        rest of the flow through to a played turn.
        """
        from battle.persistence import CLASSIC_PROFILE
        from battle.services import create_battle

        battle = create_battle(
            self.dm,
            name="Open",
            count=2,
            seed=1,
            player_seats=2,
            rules_profile=CLASSIC_PROFILE,
        )
        self.assertEqual(battle.status, battle.STATUS_LOBBY)
        self.assertEqual(battle.state_json, {})
        self.assertEqual(battle.events.count(), 0)

    def test_an_unknown_profile_is_refused_by_name(self):
        from battle.services import create_battle

        with self.assertRaises(ValueError) as caught:
            create_battle(
                self.dm,
                name="Odd",
                count=2,
                seed=1,
                rules_profile="chess",
            )
        self.assertIn("chess", str(caught.exception))


class ClassicBattlePageTest(BaseTestCase):
    """What the battle page does with a classic battle.

    This slice built the page's side: the arena is drawn in the browser, but
    from a **profile-neutral view model the server computes**
    (:mod:`battle.viewmodel`), not from the raw snapshot. So the page owes a
    classic battle the same three things it owes a Tarmar one — a field of
    cells, a card per figure, and the server-rendered event log — and the
    notice this class used to pin ("the map is the next piece of this work")
    is gone with the boundary it described.
    """

    def setUp(self):
        super().setUp()
        self.dm = self.create_dm()

    def make_battle(self):
        from battle.persistence import CLASSIC_PROFILE
        from battle.services import create_battle

        return create_battle(
            self.dm,
            name="Classic arena",
            count=3,
            seed=4242,
            rules_profile=CLASSIC_PROFILE,
        )

    def test_the_detail_page_serves_a_classic_battle(self):
        battle = self.make_battle()
        self.client.force_login(self.dm)
        response = self.client.get(f"/tarmar/battles/{battle.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Classic arena")

    def test_the_page_is_handed_a_field_and_a_card_per_figure(self):
        """The slice's whole point: a classic battle is now watchable."""
        battle = self.make_battle()
        self.client.force_login(self.dm)
        response = self.client.get(f"/tarmar/battles/{battle.pk}/")
        view = response.context["view_json"]
        self.assertEqual(view["profile"], CLASSIC_PROFILE)
        self.assertTrue(view["arena"]["cells"])
        self.assertEqual(len(view["combatants"]), 3)
        self.assertContains(response, 'id="battle-view"')

    def test_the_classic_field_reaches_the_page_flat_top(self):
        """The printed Melee map's orientation, carried rather than rotated."""
        battle = self.make_battle()
        self.client.force_login(self.dm)
        response = self.client.get(f"/tarmar/battles/{battle.pk}/")
        self.assertEqual(
            response.context["view_json"]["arena"]["orientation"], viewmodel.FLAT
        )

    def test_a_tarmar_battle_is_handed_its_own_geometry(self):
        from battle.services import create_battle

        battle = create_battle(self.dm, name="Ordinary", count=2, seed=1)
        self.client.force_login(self.dm)
        response = self.client.get(f"/tarmar/battles/{battle.pk}/")
        view = response.context["view_json"]
        self.assertEqual(view["profile"], TARMAR_PROFILE)
        self.assertEqual(view["arena"]["orientation"], viewmodel.POINTY)

    def test_the_page_survives_the_first_turn(self):
        """The view is recomputed per request, so it has to hold after a turn."""
        from battle.services import advance_turn

        battle = self.make_battle()
        advance_turn(battle.pk)
        self.client.force_login(self.dm)
        response = self.client.get(f"/tarmar/battles/{battle.pk}/")
        view = response.context["view_json"]
        self.assertEqual(len(view["combatants"]), 3)
        self.assertGreaterEqual(view["next_sequence"], battle.events.count())

    def test_the_event_log_renders_from_rows_after_a_turn(self):
        from battle.services import advance_turn

        battle = self.make_battle()
        advance_turn(battle.pk)
        self.client.force_login(self.dm)
        response = self.client.get(f"/tarmar/battles/{battle.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(battle.events.exists())

    def _fresh_draft(self):
        from battle.models import BattleDraft

        return BattleDraft.objects.create(created_by=self.dm)

    def test_the_setup_page_offers_both_profiles(self):
        from battle.persistence import CLASSIC_PROFILE, TARMAR_PROFILE

        self.client.force_login(self.dm)
        draft = self._fresh_draft()
        response = self.client.get(f"/tarmar/battles/setup/{draft.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{CLASSIC_PROFILE}"')
        self.assertContains(response, f'value="{TARMAR_PROFILE}"')

    def test_creating_a_classic_battle_through_the_setup_flow(self):
        """The DM's actual path: muster two fighters, pick classic, begin."""
        from battle.models import Battle
        from battle.persistence import CLASSIC_PROFILE

        self.client.force_login(self.dm)
        draft = self._fresh_draft()
        for archetype in ("archer", "wizard"):
            self.client.post(
                f"/tarmar/battles/setup/{draft.pk}/add/",
                {"kind": "fighter", "archetype": archetype},
            )
        response = self.client.post(
            f"/tarmar/battles/setup/{draft.pk}/start/",
            {
                "name": "Posted classic",
                "seed": 4242,
                "rules_profile": CLASSIC_PROFILE,
            },
        )
        self.assertEqual(response.status_code, 302)
        battle = Battle.objects.get(name="Posted classic")
        self.assertEqual(battle.rules_profile, CLASSIC_PROFILE)

    def test_an_unknown_profile_is_rejected_at_the_start(self):
        self.client.force_login(self.dm)
        draft = self._fresh_draft()
        for archetype in ("archer", "wizard"):
            self.client.post(
                f"/tarmar/battles/setup/{draft.pk}/add/",
                {"kind": "fighter", "archetype": archetype},
            )
        response = self.client.post(
            f"/tarmar/battles/setup/{draft.pk}/start/",
            {"name": "Bad", "seed": 1, "rules_profile": "chess"},
        )
        self.assertEqual(response.status_code, 400)


class ClassicMagicTest(BaseTestCase):
    """The crossing's point: classic magic is live in this app (slice 8).

    Everything below the UI had been in place for a while — the package has
    carried the 14-spell suite since v0.5.0 and, since v0.8.0, offers one
    candidate per castable spell and honours a chosen one in the combat
    phase. None of it could run here, because no figure this app built ever
    knew a spell. These pin the whole chain through the app's own loop.
    """

    def classic_game_of(self, battle):
        """This battle's state as the classic ``GameState`` it is.

        ``profiles.for_battle(...).load`` answers either profile's state
        type, which is the point of the seam — but a test calling into the
        classic package needs the classic type by name.
        """
        from tarmar_engine.classic.state import GameState

        state = profiles.for_battle(battle).load(battle.state_json)
        assert isinstance(state, GameState)
        return state

    def classic_wizard_battle(self, *, count: int = 2):
        """A classic battle of generated wizards, reproducible from its seed.

        This used to pin ``random.seed`` as well, because a battle's roster
        was NOT a function of its seed: ``generation.roll_all_character_stats``
        drew from the global RNG, so rolled IQ varied run to run — and IQ
        decides whether a crossing caster reaches a classic spell at all.
        These tests therefore passed alone and failed inside the suite, which
        is how the low-IQ case above was found. Issue #261 seeded the roster
        rolls, so the battle seed alone now fixes the wizards' attributes and
        the global pin is gone.
        """
        from battle.services import create_battle

        return create_battle(
            self.create_dm(),
            name="Wizards",
            count=count,
            seed=9119,
            archetypes=("wizard",),
            rules_profile=CLASSIC_PROFILE,
        )

    def test_a_generated_caster_reaches_the_field_able_to_cast(self):
        from tarmar_engine.classic.ai import castable_spells
        from tarmar_engine.classic.state import cast_block_reason

        battle = self.classic_wizard_battle()
        state = self.classic_game_of(battle)
        wizards = [one for one in state.figures if one.spells_known]
        self.assertTrue(wizards)
        for figure in wizards:
            self.assertIsNone(cast_block_reason(figure))
            self.assertTrue(castable_spells(state, figure))

    def test_the_policy_offers_a_candidate_per_castable_spell(self):
        from tarmar_engine.classic import policy as classic_policy

        battle = self.classic_wizard_battle()
        state = self.classic_game_of(battle)
        wizard = next(one for one in state.figures if one.spells_known)
        decision = classic_policy.choose_option(state, wizard)
        cast_keys = {
            candidate.spell_key
            for candidate in decision.candidates
            if candidate.spell_key
        }
        self.assertTrue(cast_keys)
        self.assertGreater(len(cast_keys), 1)

    def test_a_wizard_casts_and_the_cast_reaches_the_log(self):
        """A decision naming a spell, and a line reporting what it did.

        Which spell and which turn belong to the dice, so this runs the
        battle and asks whether magic happened at all — the decision half
        proves the menu carried the spell, the log half proves the combat
        phase honoured the declaration rather than re-deriving its own.
        """
        from battle.models import Battle
        from battle.services import advance_turn

        battle = self.classic_wizard_battle()
        cast_keys = set()
        for _turn in range(12):
            battle, events = advance_turn(battle.pk)
            for event in events:
                chosen = (event.payload or {}).get("chosen") or {}
                if chosen.get("spell_key"):
                    cast_keys.add(chosen["spell_key"])
            if cast_keys or battle.status == Battle.STATUS_FINISHED:
                break
        self.assertTrue(cast_keys, "no wizard ever chose to cast")

        from tarmar_engine.classic.spells import SPELLS

        names = {SPELLS[key].name for key in cast_keys}
        logged = " ".join(
            BattleEvent.objects.filter(battle=battle).values_list("message", flat=True)
        )
        self.assertTrue(
            any(name in logged for name in names),
            f"none of {sorted(names)} reached the log",
        )


def placed_figures(*entries: tuple[int, str, str]) -> list:
    """Classic figures for ``(combatant_id, name, team)`` entries, in order."""
    roster = classic_figures(
        [
            replace(snapshot(combatant_id, name), team=team)
            for combatant_id, name, team in entries
        ],
        classic_arena(len(entries)),
    )
    return roster.figures


class ClassicSideLabelTest(BaseTestCase):
    """#909: a classic log line names a side as a reader knows it.

    The classic narrative prints a figure as ``"Name (side)"`` and a win as
    ``"🏆 The <side> hold the field — victory!"``. A teamless figure's side
    is its uid, the ``BattleCombatant`` row pk, so without relabelling the
    log reads "Yaria Hawkridge (743)" and "The 743 hold the field". The
    label is the team's name when the side has one, else the figure's name,
    else "Side N" by its place in the roster.
    """

    def test_a_one_figure_side_is_named_for_its_figure(self):
        figures = placed_figures((743, "Yaria Hawkridge", ""), (744, "Ulfan", ""))
        self.assertEqual(
            name_sides_in_lines(
                [
                    "Yaria Hawkridge (743) swings a Saber at Ulfan (744).",
                    "Ulfan (744) falls, slain!",
                    "🏆 The 743 hold the field — victory!",
                ],
                figures,
            ),
            [
                "Yaria Hawkridge swings a Saber at Ulfan.",
                "Ulfan falls, slain!",
                "🏆 Yaria Hawkridge holds the field — victory!",
            ],
        )

    def test_a_multi_figure_side_keeps_its_team_name(self):
        figures = placed_figures((5, "Ada", "Red"), (6, "Bo", "Red"), (7, "Cy", "Blue"))
        self.assertEqual(
            name_sides_in_lines(
                [
                    "Ada (team:Red) swings a Broadsword at Cy (team:Blue).",
                    "🏆 The team:Red hold the field — victory!",
                ],
                figures,
            ),
            [
                "Ada (Red) swings a Broadsword at Cy (Blue).",
                "🏆 The Red hold the field — victory!",
            ],
        )

    def test_a_teamless_figure_beside_teams_is_named_for_itself(self):
        figures = placed_figures((5, "Ada", "Red"), (6, "Bo", "Red"), (9, "Lone", ""))
        self.assertEqual(
            name_sides_in_lines(
                [
                    "Lone (9) swings at Ada (team:Red).",
                    "🏆 The 9 hold the field — victory!",
                ],
                figures,
            ),
            ["Lone swings at Ada (Red).", "🏆 Lone holds the field — victory!"],
        )

    def test_a_nameless_figure_falls_back_to_its_roster_place(self):
        figures = placed_figures((11, "Ada", ""), (12, "", ""))
        self.assertEqual(
            name_sides_in_lines(
                [" (12) advances.", "🏆 The 12 hold the field — victory!"], figures
            ),
            ["Side 2 advances.", "🏆 Side 2 holds the field — victory!"],
        )

    def test_a_numbered_name_is_not_mistaken_for_another_figure(self):
        """``_numbered_variant`` names a duplicate "Thorn (2)"; when another
        "Thorn" happens to be row 2, its token sits inside the first one's."""
        figures = placed_figures((2, "Thorn", ""), (6, "Thorn (2)", ""))
        self.assertEqual(
            name_sides_in_lines(
                ["Thorn (2) (6) swings at Thorn (2).", "Thorn (2) falls."], figures
            ),
            ["Thorn (2) swings at Thorn.", "Thorn falls."],
        )

    def test_a_numbered_name_on_a_team_is_not_mistaken_for_a_teamless_one(self):
        """The teamed "Thorn (2)" prints as "Thorn (2) (team:Red)", which holds the
        teamless "Thorn" in row 2's token; the teamed token must win."""
        figures = placed_figures((2, "Thorn", ""), (6, "Thorn (2)", "Red"))
        self.assertEqual(
            name_sides_in_lines(["Thorn (2) (team:Red) hits Thorn (2)."], figures),
            ["Thorn (2) (Red) hits Thorn."],
        )

    def test_the_team_of_a_figure_is_its_side_unless_that_is_its_uid(self):
        figures = placed_figures((5, "Ada", "Red"), (9, "Lone", ""))
        self.assertEqual([classic_team(figure) for figure in figures], ["Red", ""])


class ClassicVictoryLineTest(BaseTestCase):
    """#909 end to end: a finished classic battle's log never names a row."""

    def finish(self, **kwargs):
        from battle.models import Battle
        from battle.persistence import CLASSIC_PROFILE
        from battle.services import advance_turn, create_battle

        battle = create_battle(
            self.create_dm(),
            name="Gold",
            count=4,
            seed=42,
            rules_profile=CLASSIC_PROFILE,
            **kwargs,
        )
        for _turn in range(300):
            if battle.status in (Battle.STATUS_FINISHED, Battle.STATUS_STALEMATE):
                break
            battle, _events = advance_turn(battle.pk)
        self.assertEqual(battle.status, Battle.STATUS_FINISHED)
        messages = list(
            BattleEvent.objects.filter(battle=battle)
            .order_by("turn", "sequence")
            .values_list("message", flat=True)
        )
        pks = list(battle.combatants.values_list("id", flat=True))
        for message in messages:
            for pk in pks:
                self.assertNotIn(f"({pk})", message)
                self.assertNotIn(f"The {pk} ", message)
        return messages

    def test_a_free_for_all_victory_names_the_winning_figure(self):
        messages = self.finish()
        self.assertIn("🏆 Yaria Hawkridge holds the field — victory!", messages)
        self.assertIn("Yaria Hawkridge stands victorious", messages)

    def test_a_team_victory_still_names_the_team(self):
        messages = self.finish(fighter_teams=("Red", "Red", "Blue", "Blue"))
        victories = [message for message in messages if "hold the field" in message]
        self.assertEqual(len(victories), 1)
        self.assertRegex(victories[0], r"^🏆 The (Red|Blue) hold the field — victory!$")
        self.assertTrue(any("(Red)" in message for message in messages))


def team_state(*entries: tuple[int, str, str]):
    """A classic game state for ``(combatant_id, name, team)`` entries."""
    state, _roster = classic_state(
        [
            replace(snapshot(combatant_id, name), team=team)
            for combatant_id, name, team in entries
        ],
        seed=1,
    )
    return state


class ClassicTeamNamedLikeRowTest(BaseTestCase):
    """#930: a team whose name is a row id is still a team, and only that.

    A teamless figure's side is its uid, the ``BattleCombatant`` row pk.
    Before #930 a team's side was its bare name, so team "7" and the
    teamless figure in row 7 shared side "7": the engine read them as
    allies, ``classic_team`` read a row-7 member of team "7" as teamless,
    and both sides' wins printed the same line. Every case below is one of
    those, and each must come out naming the right party.
    """

    def setUp(self):
        super().setUp()
        # Row 7 is teamless; team "7" has two members in other rows; team
        # "9" is named like row 9, which is a member of team "Blue"; and row
        # 4 sits on team "4", named like its own row.
        self.state = team_state(
            (7, "Bo", ""),
            (5, "Ada", "7"),
            (6, "Cy", "7"),
            (9, "Dee", "Blue"),
            (3, "Eli", "9"),
            (4, "Fen", "4"),
        )
        self.by_name = {figure.name: figure for figure in self.state.figures}

    def enemy_names(self, name: str) -> set[str]:
        return {one.name for one in self.state.enemies_of(self.by_name[name])}

    def test_a_team_side_can_never_equal_a_row_id(self):
        self.assertEqual(classic_side("7", 7), "team:7")
        self.assertEqual(classic_side("", 7), "7")
        self.assertEqual(self.by_name["Bo"].side, "7")
        self.assertEqual(self.by_name["Ada"].side, "team:7")
        self.assertEqual(len(self.state.sides), 5)

    def test_allies_and_enemies_follow_the_team_not_the_spelling(self):
        self.assertEqual(self.enemy_names("Bo"), {"Ada", "Cy", "Dee", "Eli", "Fen"})
        self.assertEqual(self.enemy_names("Ada"), {"Bo", "Dee", "Eli", "Fen"})
        self.assertEqual(self.enemy_names("Dee"), {"Bo", "Ada", "Cy", "Eli", "Fen"})
        self.assertEqual(self.enemy_names("Eli"), {"Bo", "Ada", "Cy", "Dee", "Fen"})

    def test_classic_team_names_each_figures_own_team(self):
        self.assertEqual(
            {name: classic_team(figure) for name, figure in self.by_name.items()},
            {"Bo": "", "Ada": "7", "Cy": "7", "Dee": "Blue", "Eli": "9", "Fen": "4"},
        )

    def test_the_profile_and_the_view_report_the_teams_name(self):
        profile = profiles.ClassicProfile(battle=None)
        self.assertEqual(
            [profile.actor_team(figure) for figure in self.state.figures],
            ["", "7", "7", "Blue", "9", "4"],
        )
        view = viewmodel.classic_view(self.state, turn=0, next_sequence=0)
        self.assertEqual(
            {entry["name"]: entry["team"] for entry in view["combatants"]},
            {"Bo": "", "Ada": "7", "Cy": "7", "Dee": "Blue", "Eli": "9", "Fen": "4"},
        )

    def test_the_last_side_standing_is_judged_by_kind_not_text(self):
        from battle.services import standing_sides

        profile = profiles.ClassicProfile(battle=None)
        both = [self.by_name["Bo"], self.by_name["Ada"]]
        self.assertEqual(standing_sides(profile, both), {("solo", "7"), ("team", "7")})
        team_only = [self.by_name["Ada"], self.by_name["Cy"]]
        self.assertEqual(standing_sides(profile, team_only), {("team", "7")})

    def test_every_line_and_each_win_names_the_right_party(self):
        from tarmar_engine.classic.narrative import _name, narrate_victory

        figures = self.state.figures
        engine_lines = [
            f"{_name(self.by_name['Ada'])} swings at {_name(self.by_name['Bo'])}.",
            f"{_name(self.by_name['Fen'])} swings at {_name(self.by_name['Eli'])}.",
            f"{_name(self.by_name['Dee'])} falls, slain!",
            narrate_victory(self.by_name["Ada"].side),
            narrate_victory(self.by_name["Bo"].side),
            narrate_victory(self.by_name["Eli"].side),
        ]
        self.assertEqual(
            name_sides_in_lines(engine_lines, figures),
            [
                "Ada (7) swings at Bo.",
                "Fen (4) swings at Eli (9).",
                "Dee (Blue) falls, slain!",
                "🏆 The 7 hold the field — victory!",
                "🏆 Bo holds the field — victory!",
                "🏆 The 9 hold the field — victory!",
            ],
        )

    def test_a_team_side_spelled_before_930_is_refused_not_guessed(self):
        """A bare team name is no longer read as a team (#944).

        Battles saved in that spelling are rewritten by the data migration
        ``battle.0017_classic_team_sides_prefixed``, whose own tests build
        the old form and prove the rewrite both ways; a side it left alone
        (no combatant row to read the team from) is refused here rather
        than read as a team or as no team, either of which could be wrong.
        """
        figures = placed_figures((5, "Ada", "Red"), (9, "Lone", ""))
        figures[0].side = "Red"
        with self.assertRaisesRegex(ValueError, "'5' has side 'Red'"):
            classic_team(figures[0])
        self.assertEqual(classic_team(figures[1]), "")
        with self.assertRaises(ValueError):
            name_sides_in_lines(["Ada (Red) swings at Lone (9)."], figures)
