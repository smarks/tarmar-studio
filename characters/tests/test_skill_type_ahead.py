"""The add-skill input's type-ahead suggestions (issue #224).

The `#new-skill-name` box binds a native `<datalist>` over the known skill
corpus -- every skill any prior-experience track can grant, plus the
character's own current skills -- while free text stays allowed, because a
`<datalist>` never restricts what an `<input>` can hold.
"""

from characters.models import Character
from common.test_helpers import BaseTestCase

CHARACTER_DEFAULTS = {
    "strength": 12,
    "dexterity": 12,
    "intelligence": 12,
    "wisdom": 12,
    "constitution": 12,
    "charisma": 12,
    "appearance_roll": "3",
    "height_roll": "3",
    "weight_roll": "3",
    "fatigue_roll": 7,
    # The add-skill control (and its datalist) only renders while the
    # character has a free point to spend and is alive -- see
    # character_edit.html's ``{% if character.free_skill_points > 0 and
    # character.is_alive %}`` guard.
    "free_skill_points": 3,
}


class SkillNameSuggestionsContextTest(BaseTestCase):
    """CharacterEditView's context carries the suggestion corpus."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_context_includes_every_granted_skill_name(self):
        from characters.skills import granted_skill_names

        character = Character.objects.create(
            user=self.user, name="Aldwin", **CHARACTER_DEFAULTS
        )
        response = self.client.get(f"/tarmar/characters/{character.pk}/")

        suggestions = set(response.context["skill_name_suggestions"])
        self.assertTrue(set(granted_skill_names()).issubset(suggestions))

    def test_context_excludes_special_roll_results(self):
        character = Character.objects.create(
            user=self.user, name="Aldwin", **CHARACTER_DEFAULTS
        )
        response = self.client.get(f"/tarmar/characters/{character.pk}/")

        suggestions = response.context["skill_name_suggestions"]
        for special in ("+1 str", "+1 dex", "+1 con", "skill point", "spell point"):
            self.assertNotIn(special, suggestions)

    def test_context_includes_the_characters_own_freely_typed_skill(self):
        """A skill not on any track table (typed in free-form) still
        suggests -- so renaming or re-adding it type-ahead completes too."""
        character = Character.objects.create(
            user=self.user,
            name="Aldwin",
            skills={"wolf's bane": 3},
            **CHARACTER_DEFAULTS,
        )
        response = self.client.get(f"/tarmar/characters/{character.pk}/")

        self.assertIn("wolf's bane", response.context["skill_name_suggestions"])


class SkillNameDatalistHtmlTest(BaseTestCase):
    """The rendered page wires the input to a native datalist."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.character = Character.objects.create(
            user=self.user, name="Aldwin", **CHARACTER_DEFAULTS
        )
        self.html = self.client.get(
            f"/tarmar/characters/{self.character.pk}/"
        ).content.decode()

    def test_input_is_bound_to_the_datalist(self):
        self.assertIn('list="skill-name-suggestions"', self.html)
        self.assertIn('id="skill-name-suggestions"', self.html)

    def test_datalist_options_are_title_cased_like_the_skills_list(self):
        """Same display convention as the skills list's
        ``{{ skill.name|title }}``."""
        self.assertIn('<option value="Weapon">', self.html)
        self.assertIn('<option value="Weather/Path">', self.html)
        self.assertIn('<option value="Maker Skill">', self.html)

    def test_datalist_excludes_special_roll_results(self):
        self.assertNotIn('<option value="+1 Str">', self.html)
        self.assertNotIn('<option value="Skill Point">', self.html)

    def test_free_text_input_is_not_restricted_to_a_select(self):
        """A native text input with a datalist -- not a <select> -- so typing
        a skill absent from every list is still possible."""
        self.assertIn('id="new-skill-name"', self.html)
        self.assertNotIn('<select id="new-skill-name"', self.html)
