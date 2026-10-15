"""The prior-experience workbench on the character editor (issues #217, #221).

The floating aging-confirm, term-result and death panels are gone; this is
what replaced them server-side: a persistent workbench card showing what the
next run of years costs, an Experience History column beside it (newest first,
newest entry expanded), and a server-rendered death card in the workbench's
place for a character who is already dead. static/js/character-edit.js keeps
these in sync after an AJAX roll -- see static/js/__tests__/character-edit.
test.js for that half.

Experience is bought a year at a time since issue #221; the fixtures below
still include legacy fixed-term entries, because those are in players' logs
and must keep rendering.
"""

from common.test_helpers import BaseTestCase, create_character


def _survival_check(dice, modifier, target, success):
    return {
        "dice": list(dice),
        "roll": sum(dice),
        "modifier": modifier,
        "total": sum(dice) + modifier,
        "target": target,
        "success": success,
        "auto_success": False,
        "auto_death": sum(dice) == 2,
    }


def _year(year, age, skill, roll, survival_check):
    return {
        "year": year,
        "age": age,
        "skill": skill,
        "skill_normalized": skill.lower(),
        "roll": roll,
        "xp_awarded": 250,
        "money_earned": 50,
        "survival_check": survival_check,
    }


def _term(years, *, term, track="merchant", alive=True, aging=None):
    """A legacy stored term entry -- still carries ``term``, as old rows do."""
    return {
        "term": term,
        "track": track,
        "years": years,
        "skills_gained": {},
        "xp_awarded": 250 * len(years),
        "money_earned": 50 * len(years),
        "free_points_awarded": 1,
        "survival_check": years[-1]["survival_check"],
        "aging": aging,
        "alive": alive,
    }


class WorkbenchCardTest(BaseTestCase):
    """The workbench card's eyebrow, consequence preview and roll button."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def _html(self, **overrides):
        character = create_character(self.user, **overrides)
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_fresh_characters_workbench_previews_one_year(self):
        # age defaults to 16, and the default run is a single year.
        html = self._html()
        self.assertIn("NEXT &mdash; YEAR 1", html)
        self.assertIn('id="workbench-age-preview">16 &rarr; 17<', html)
        self.assertIn(
            'id="workbench-survival-preview">3d6 + best attribute modifier '
            "(target varies by track)<",
            html,
        )
        self.assertIn(">Roll 1 year<", html)
        self.assertIn('id="track-select"', html)
        self.assertIn('id="years-input"', html)

    def test_the_years_input_carries_the_bounds_the_endpoint_enforces(self):
        from characters.skills import DEFAULT_RUN_YEARS, MAX_EXPERIENCE_YEARS_PER_RUN

        html = self._html()
        self.assertIn(f'value="{DEFAULT_RUN_YEARS}"', html)
        self.assertIn(f'max="{MAX_EXPERIENCE_YEARS_PER_RUN}"', html)
        self.assertIn('min="1"', html)

    def test_the_preview_advances_with_the_characters_progress(self):
        html = self._html(
            age=30,
            experience_log=[
                _term(
                    [
                        _year(
                            index,
                            26 + index,
                            "Haggling",
                            4,
                            _survival_check([3, 4], 1, 4, True),
                        )
                        for index in range(1, 5)
                    ],
                    term=1,
                )
            ],
        )
        self.assertIn("NEXT &mdash; YEAR 5", html)
        self.assertIn('id="workbench-age-preview">30 &rarr; 31<', html)

    def test_the_aging_preview_names_the_age_the_next_hit_lands_at(self):
        # Age 36 is the first age carrying a non-zero entry: strength -1,
        # dexterity -1 (intelligence/wisdom/constitution are 0 and dropped).
        html = self._html(age=34)
        self.assertIn('id="workbench-aging-preview">STR -1, DEX -1 at age 36<', html)

    def test_the_aging_preview_looks_past_the_next_year_to_the_real_hit(self):
        """A young character is told when aging starts, not just "none"."""
        html = self._html(age=16)
        self.assertIn('id="workbench-aging-preview">STR -1, DEX -1 at age 36<', html)

    def test_past_the_horizon_the_preview_shows_the_escalation_note(self):
        from characters.views.characters import aging_preview_horizon_age

        html = self._html(age=aging_preview_horizon_age() + 4)
        self.assertIn("Continues to escalate past age", html)

    def test_the_card_copy_explains_what_rolling_years_does(self):
        html = self._html()
        self.assertIn(
            "Each year rolls its own skill and its own survival check.",
            html,
        )

    def test_a_dead_character_gets_no_workbench_card(self):
        html = self._html(is_alive=False, death_cause="Eaten by a dragon")
        self.assertNotIn("workbench-eyebrow", html)
        self.assertNotIn(">Roll 1 year<", html)
        self.assertNotIn('id="track-select"', html)
        self.assertNotIn('id="years-input"', html)


class ExperienceHistoryColumnTest(BaseTestCase):
    """The Experience History column beside the workbench card."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def _html(self, experience_log):
        character = create_character(self.user, experience_log=experience_log)
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_an_empty_log_shows_the_empty_state_message(self):
        html = self._html([])
        self.assertIn(
            "No years yet. Roll the first year to build this character's past.",
            html,
        )
        self.assertNotIn("term-entry", html)

    def test_a_run_is_headed_by_its_track_and_the_ages_it_covers(self):
        html = self._html(
            [
                _term(
                    [
                        _year(
                            1, 17, "Haggling", 4, _survival_check([3, 4], 1, 4, True)
                        ),
                        _year(2, 18, "Sword", 6, _survival_check([5, 5], 1, 4, True)),
                    ],
                    term=1,
                ),
            ]
        )
        self.assertIn("Merchant, Age 17&ndash;18", html)

    def test_a_single_year_run_is_headed_by_one_age(self):
        html = self._html(
            [
                _term(
                    [_year(1, 17, "Haggling", 4, _survival_check([3, 4], 1, 4, True))],
                    term=1,
                )
            ]
        )
        self.assertIn("Merchant, Age 17<", html)

    def test_runs_render_newest_first(self):
        html = self._html(
            [
                _term(
                    [_year(1, 21, "Haggling", 4, _survival_check([3, 4], 1, 4, True))],
                    term=1,
                ),
                _term(
                    [_year(1, 25, "Sword", 6, _survival_check([5, 5], 1, 4, True))],
                    term=2,
                ),
            ]
        )
        self.assertLess(html.index("Age 25"), html.index("Age 21"))

    def test_only_the_newest_entry_is_open(self):
        html = self._html(
            [
                _term(
                    [_year(1, 21, "Haggling", 4, _survival_check([3, 4], 1, 4, True))],
                    term=1,
                ),
                _term(
                    [_year(1, 25, "Sword", 6, _survival_check([5, 5], 1, 4, True))],
                    term=2,
                ),
            ]
        )
        term_two_start = html.index("Age 25")
        term_one_start = html.index("Age 21")
        # Each term's <summary> is immediately preceded by its own <details>
        # tag; only the newest one (term 2) carries `open`.
        details_tag_for_term_two = html.rfind("<details", 0, term_two_start)
        details_tag_for_term_one = html.rfind("<details", 0, term_one_start)
        self.assertIn("open", html[details_tag_for_term_two:term_two_start])
        self.assertNotIn("open", html[details_tag_for_term_one:term_one_start])

    def test_there_is_exactly_one_experience_log_element(self):
        # The standalone bottom-of-page Experience History section is gone
        # (issue #217) -- the column beside the workbench is the only one.
        html = self._html(
            [
                _term(
                    [_year(1, 21, "Haggling", 4, _survival_check([3, 4], 1, 4, True))],
                    term=1,
                )
            ]
        )
        self.assertEqual(html.count('id="experience-log"'), 1)
        self.assertEqual(html.count('id="experience-history-section"'), 1)


class DeathCardTest(BaseTestCase):
    """The server-rendered death card, in the workbench's place (issue #217)."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_a_dead_character_gets_the_death_card_with_no_roll_button(self):
        character = create_character(
            self.user, is_alive=False, death_cause="Eaten by a dragon"
        )
        html = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()
        # The name is marked for a rename to repaint it (issue #903).
        self.assertIn(f"<span data-name-chrome>{character.name}</span> died", html)
        self.assertIn("Eaten by a dragon", html)
        self.assertIn("A DM can resurrect this character.", html)
        self.assertNotIn("Roll 1 year", html)
        self.assertNotIn('id="roll-years-btn"', html)

    def test_the_death_card_shows_the_fatal_roll_when_there_is_one(self):
        character = create_character(
            self.user,
            is_alive=False,
            death_cause="Failed survival check during merchant term 1",
            experience_log=[
                _term(
                    [_year(1, 21, "Haggling", 4, _survival_check([1, 2], 0, 6, False))],
                    term=1,
                    alive=False,
                )
            ],
        )
        html = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()
        self.assertIn("Fatal roll: 2d6 [1, 2] + 0 = 3 vs 6", html)

    def test_the_death_card_omits_the_fatal_roll_line_for_a_dm_marked_death(self):
        character = create_character(
            self.user, is_alive=False, death_cause="Eaten by a dragon"
        )
        html = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()
        self.assertNotIn("Fatal roll:", html)

    def test_the_death_card_template_fragment_is_always_present(self):
        """static/js/character-edit.js clones #death-card-template on an AJAX
        death, so it must exist even while the character is alive."""
        character = create_character(self.user)
        html = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()
        self.assertIn('id="death-card-template"', html)


class OldFloatingPanelMarkupIsGoneTest(BaseTestCase):
    """The redesign deletes the panel markup it replaced (issue #217)."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_a_living_characters_page_has_no_death_banner_or_old_controls(self):
        character = create_character(self.user)
        html = self.client.get(f"/tarmar/characters/{character.pk}/").content.decode()
        self.assertNotIn("death-banner", html)
        self.assertNotIn("experience-controls", html)
        self.assertNotIn("Add 4-Year Term", html)
        self.assertNotIn("Roll Term", html)


class WorkbenchRefinementTest(BaseTestCase):
    """The review-pass refinements (issue #217): the wide main column and the
    always-visible natural-roll note beside the survival preview."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.character = create_character(self.user)

    def test_editor_uses_the_wide_main_column(self):
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertContains(response, 'class="character-edit-main page-width-1240"')

    def test_workbench_states_the_natural_roll_overrides(self):
        response = self.client.get(f"/tarmar/characters/{self.character.pk}/")
        self.assertContains(
            response, "Triple ones always kill; triple sixes always survive."
        )


class TrackLabelTest(BaseTestCase):
    """Underscore track values must render as their human labels everywhere
    (issue #221 review): civil_service is 'Civil Service', hunter_gatherer is
    'Hunter/Gatherer' — never 'Civil_service'."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_history_run_summaries_use_the_human_track_label(self):
        run = {
            "term": 1,
            "track": "civil_service",
            "years": [
                {"year": 1, "age": 17, "skill": "ledger", "roll": 4},
            ],
            "xp_awarded": 750,
            "free_points_awarded": 1,
            "money_earned": 75,
        }
        character = create_character(self.user, experience_log=[run])
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertContains(response, "Civil Service, Age 17")
        self.assertNotContains(response, "Civil_service")

    def test_the_track_select_offers_the_same_labels(self):
        character = create_character(self.user)
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertContains(response, "Hunter/Gatherer")
        self.assertContains(response, "Civil Service")
