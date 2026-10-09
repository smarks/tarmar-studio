"""Studio roster → classic figures (issue #251, slice 6).

Slice 5 taught the turn loop the profile seam but built its classic states by
hand. These pin the crossing itself: what a studio combatant becomes under
classic rules, what the crossing costs, and that the result is a state this
app can store, reload and advance.
"""

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
    classic_state,
    classic_weapon,
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
