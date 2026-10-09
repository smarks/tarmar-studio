"""The skills list and the Experience History render once, on the server.

character-edit.js used to rebuild both itself -- a skill row with
buildSkillRow/buildAllocateButton, a history entry with an HTML string in
prependRunToLog -- mirroring the page's own markup, so a change to one copy
could leave the live update and a reload saying different things (issue #338
items 3 and 4). The page and the add-years and allocate responses now render
the same partials, and the browser inserts what it is sent.

What the Jest tests used to pin about the JavaScript builders (the + button's
rules, escaping, the rename affordances, title case, the survival line's
wording, a single age versus a range, the aging line) is pinned here against
the partials, where those rules now live.
"""

import json
from unittest import mock

from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse

from characters.skills import format_skill_level, skill_rows
from characters.views.characters import can_grant_skill_points
from characters.weapon_skill import needs_weapon_tie, weapon_choices
from common.test_helpers import BaseTestCase, create_character


class _Character:
    """The two attributes the row partial reads, without a database."""

    def __init__(self, free_skill_points=2, is_alive=True):
        self.free_skill_points = free_skill_points
        self.is_alive = is_alive


def render_row(name="sword", display="II", points=3, can_edit=True, **character):
    return render_to_string(
        "characters/_skill_row.html",
        {
            "skill": {"name": name, "display": display, "points": points},
            "character": _Character(**character),
            "can_edit": can_edit,
        },
    )


def check(dice, modifier, target, success, **extra):
    return {
        "dice": dice,
        "roll": sum(dice),
        "modifier": modifier,
        "total": sum(dice) + modifier,
        "target": target,
        "success": success,
        **extra,
    }


def render_entry(run, open_entry=True):
    html = render_to_string(
        "characters/_term_entry.html", {"run": run, "open": open_entry}
    )
    return " ".join(html.split())


RUN = {
    "track": "merchant",
    "years": [
        {
            "year": 5,
            "age": 21,
            "skill": "haggling",
            "roll": 4,
            "survival_check": check([2, 3, 1], 1, 4, True),
        },
        {
            "year": 8,
            "age": 24,
            "skill": "haggling",
            "roll": 5,
            "survival_check": check([3, 4, 6], -2, 4, True),
        },
    ],
    "aging": {"str": -1, "dex": 0},
    "xp_awarded": 1000,
    "free_points_awarded": 2,
}


class SkillRowPartialTest(SimpleTestCase):
    def test_an_editor_gets_the_rename_affordances_and_title_case(self):
        html = render_row(name="shield parry")

        self.assertIn('contenteditable="true"', html)
        self.assertIn('role="textbox"', html)
        self.assertIn('aria-label="Rename skill"', html)
        self.assertIn('data-original="shield parry"', html)
        self.assertIn(">Shield Parry</span>", html)

    def test_a_reader_gets_the_name_without_the_editor(self):
        html = render_row(can_edit=False)

        self.assertNotIn("contenteditable", html)
        self.assertNotIn("allocate-btn", html)

    def test_the_plus_button_is_offered_while_points_remain(self):
        self.assertIn(
            '<button class="allocate-btn button secondary" data-skill="sword">+</button>',
            render_row(),
        )

    def test_no_plus_button_once_the_points_are_spent(self):
        self.assertNotIn("allocate-btn", render_row(free_skill_points=0))

    def test_no_plus_button_for_a_dead_character(self):
        self.assertNotIn("allocate-btn", render_row(is_alive=False))

    def test_never_a_max_badge_because_no_level_is_terminal(self):
        self.assertNotIn("max-badge", render_row(points=40))

    def test_the_level_string_is_the_one_given(self):
        html = render_row(display="II (+1)", points=5)

        self.assertIn('<span class="skill-level">Level II (+1)</span>', html)
        self.assertIn('<span class="skill-points">(5 pts)</span>', html)

    def test_a_hostile_skill_name_is_text_in_the_body_and_the_attributes(self):
        html = render_row(name='"><img src=x onerror=alert(1)>', display="<b>I</b>")

        self.assertNotIn("<img", html)
        self.assertNotIn("<b>", html)
        self.assertIn('data-skill="&quot;&gt;&lt;img', html)


class SkillsListPartialTest(SimpleTestCase):
    def render(self, skills):
        return render_to_string(
            "characters/_skills_list.html",
            {
                "skills_display": skill_rows(skills),
                "character": _Character(),
                "can_edit": True,
            },
        )

    def test_rows_keep_the_stored_order(self):
        html = self.render({"sword": 5, "arcane": 4})

        self.assertLess(
            html.index('data-skill="sword"'), html.index('data-skill="arcane"')
        )

    def test_no_skills_says_so(self):
        self.assertIn(
            '<p class="no-skills">No skills yet. Roll experience years to gain skills.</p>',
            self.render({}),
        )

    def test_levels_come_from_the_ladder(self):
        self.assertIn(
            f"Level {format_skill_level(4, 'arcane')}", self.render({"arcane": 4})
        )


class TermEntryPartialTest(SimpleTestCase):
    def test_the_newest_entry_opens_on_its_track_and_age_range(self):
        text = render_entry(RUN)

        self.assertIn('<details class="term-entry" open>', text)
        self.assertIn("<summary>Merchant, Age 21&ndash;24</summary>", text)

    def test_an_older_entry_is_closed(self):
        self.assertIn(
            '<details class="term-entry">', render_entry(RUN, open_entry=False)
        )

    def test_a_one_year_run_shows_its_single_age(self):
        text = render_entry({**RUN, "years": [RUN["years"][0]]})

        self.assertIn("<summary>Merchant, Age 21</summary>", text)

    def test_an_underscore_track_reads_as_words(self):
        self.assertIn(
            "Civil Service, Age", render_entry({**RUN, "track": "civil_service"})
        )

    def test_each_year_carries_its_survival_roll_and_a_malus_subtracts(self):
        text = render_entry(RUN)

        self.assertIn(
            "Year 5: haggling (rolled 4) &mdash; survival 3d6 [2, 3, 1] + 1 = 7 vs 4: survived",
            text,
        )
        self.assertIn("3d6 [3, 4, 6] - 2 = 11 vs 4: survived", text)

    def test_a_triple_ones_year_is_always_fatal(self):
        year = {
            "year": 5,
            "age": 21,
            "skill": "haggling",
            "roll": 4,
            "survival_check": check([1, 1, 1], 3, 4, False, auto_death=True),
        }

        self.assertIn(
            "(triple ones — always fatal): died", render_entry({**RUN, "years": [year]})
        )

    def test_a_stored_2d6_check_still_reads_as_2d6(self):
        year = {
            "year": 5,
            "age": 21,
            "skill": "haggling",
            "roll": 4,
            "survival_check": check([6, 6], 0, 14, True, auto_success=True),
        }

        self.assertIn(
            "2d6 [6, 6] + 0 = 12 vs 14 (boxcars — always survives)",
            render_entry({**RUN, "years": [year]}),
        )

    def test_a_year_without_a_check_has_no_survival_tail(self):
        year = {"year": 1, "age": 21, "skill": "haggling", "roll": 4}
        text = render_entry({**RUN, "years": [year]})

        self.assertIn("Year 1: haggling (rolled 4)</li>", text)
        self.assertNotIn("survival", text)

    def test_the_aging_line_lists_only_nonzero_penalties(self):
        self.assertIn("Aging penalties applied: STR -1", render_entry(RUN))

    def test_no_aging_line_without_aging_or_with_only_zeroes(self):
        without = {key: value for key, value in RUN.items() if key != "aging"}
        self.assertNotIn("Aging penalties applied", render_entry(without))
        self.assertNotIn(
            "Aging penalties applied: STR", render_entry({**RUN, "aging": {"str": 0}})
        )

    def test_the_awards_line(self):
        self.assertIn("<p>+1000 XP, +2 free skill points</p>", render_entry(RUN))


class EndpointsSendThePageMarkupTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user("partial-owner")
        self.character = create_character(self.owner, free_skill_points=2)
        self.login_as(self.owner)

    def post(self, name, body):
        return self.client.post(
            reverse(name, args=[self.character.pk]),
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_add_years_sends_the_new_entry_and_the_list_as_the_page_renders_them(self):
        """The run's entry and the skills list, as the sheet renders them.

        Issue #891: the run was rolled with live dice, and the expected list
        was rendered without the page's ``weapon_choices``, so the test
        failed only on a run that granted a Weapon skill (its "Taken for"
        select is filled on the page and was empty here). The dice are now
        pinned to each case, the Weapon skill among them, and the list is
        rendered with the context the page gives it (``mechanics_context``
        and ``can_grant_skill_points``).
        """
        # merchant: a d20 of 1 is the Weapon skill; 20 is past the table's
        # Weapon row. Survival is pinned so the run always lives.
        for skill_roll, grants_weapon in ((1, True), (20, False)):
            with self.subTest(skill_roll=skill_roll):
                self.character.experience_log = []
                self.character.skills = {}
                self.character.save()
                with (
                    mock.patch(
                        "characters.skills.roll_survival_dice",
                        return_value=(6, 6, 6, 18),
                    ),
                    mock.patch("characters.skills.roll_d20", return_value=skill_roll),
                ):
                    data = self.post(
                        "characters:add_years", {"track": "merchant", "years": 1}
                    ).json()
                self.character.refresh_from_db()
                self.assertEqual(
                    any(needs_weapon_tie(name) for name in self.character.skills),
                    grants_weapon,
                )

                self.assertEqual(
                    data["run_html"],
                    render_to_string(
                        "characters/_term_entry.html",
                        {"run": self.character.experience_log[-1], "open": True},
                    ),
                )
                self.assertEqual(
                    data["character"]["skills_html"],
                    render_to_string(
                        "characters/_skills_list.html",
                        {
                            "skills_display": skill_rows(self.character.skills),
                            "character": self.character,
                            "can_edit": True,
                            "can_grant_skill_points": can_grant_skill_points(
                                is_game_master=False
                            ),
                            "weapon_choices": weapon_choices(),
                        },
                    ),
                )

    def test_allocate_sends_the_row_as_the_page_renders_it(self):
        # Stealth, a glossary skill: "Sword" names no rule, and a player may
        # no longer start one (#862).
        data = self.post("characters:allocate_skill", {"skill": "Stealth"}).json()
        self.character.refresh_from_db()
        row = skill_rows(self.character.skills)[0]

        self.assertEqual(
            data["row_html"],
            render_to_string(
                "characters/_skill_row.html",
                {"skill": row, "character": self.character, "can_edit": True},
            ),
        )
