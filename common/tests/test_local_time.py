"""The {% local_time %} tag and the progressive-enhancement markup it emits
site-wide (issue #225).

Two layers: ``LocalTimeTagTest`` exercises ``common.templatetags.local_time.
local_time`` directly (no DB, no rendering pipeline) for the tag's own
contract -- None handling, naive-vs-aware datetimes, the ISO-8601
``datetime`` attribute, the UTC fallback text, and the ``data-local-time-
style`` attribute that static/js/local-time.js reads. ``RenderedSurfacesTest``
then confirms every page the issue named actually emits the
``<time class="local-time">`` markup, not just the raw ``|date``-filtered
text it replaced.
"""

import datetime

from django.template import Context, Template
from django.test import SimpleTestCase
from django.utils import timezone

from battle.services import create_battle
from characters.models import Character
from common.templatetags.local_time import local_time
from common.test_helpers import BaseTestCase, create_character


class LocalTimeTagTest(SimpleTestCase):
    """Direct, DB-free tests of the ``local_time`` simple tag."""

    def test_none_renders_as_empty_string(self):
        """Mirrors ``{{ value|date:"..." }}`` on ``None``: nothing at all."""
        self.assertEqual(local_time(None), "")

    def test_an_aware_datetime_renders_a_time_element(self):
        value = datetime.datetime(2026, 2, 3, 14, 5, tzinfo=datetime.UTC)
        rendered = local_time(value, "M d, Y H:i")
        self.assertIn('<time class="local-time"', rendered)
        self.assertIn('datetime="2026-02-03T14:05:00+00:00"', rendered)
        self.assertIn('data-local-time-style="datetime"', rendered)
        self.assertIn(">Feb 03, 2026 14:05<", rendered)

    def test_a_naive_datetime_is_treated_as_utc(self):
        """USE_TZ is on everywhere in this app, so this is a defensive path,
        not the expected one -- but a naive value must still resolve to UTC
        rather than raising or silently using the server's local wall clock.
        """
        naive_value = datetime.datetime(2026, 2, 3, 14, 5)
        rendered = local_time(naive_value, "M d, Y H:i")
        self.assertIn('datetime="2026-02-03T14:05:00+00:00"', rendered)

    def test_style_defaults_to_datetime(self):
        value = datetime.datetime(2026, 2, 3, 14, 5, tzinfo=datetime.UTC)
        self.assertIn('data-local-time-style="datetime"', local_time(value))

    def test_style_date_is_carried_through(self):
        value = datetime.datetime(2026, 2, 3, 14, 5, tzinfo=datetime.UTC)
        rendered = local_time(value, "M d, Y", style="date")
        self.assertIn('data-local-time-style="date"', rendered)
        self.assertIn(">Feb 03, 2026<", rendered)

    def test_format_string_drives_only_the_fallback_text(self):
        """The ISO datetime attribute is format-independent; only the human
        -readable fallback follows ``format_string``."""
        value = datetime.datetime(2026, 2, 3, 14, 5, tzinfo=datetime.UTC)
        rendered = local_time(value, "Y-m-d H:i")
        self.assertIn('datetime="2026-02-03T14:05:00+00:00"', rendered)
        self.assertIn(">2026-02-03 14:05<", rendered)

    def test_output_is_marked_safe_for_direct_template_use(self):
        value = datetime.datetime(2026, 2, 3, 14, 5, tzinfo=datetime.UTC)
        template = Template("{% load local_time %}{% local_time value %}")
        rendered = template.render(Context({"value": value}))
        self.assertIn('<time class="local-time"', rendered)


class RenderedSurfacesTest(BaseTestCase):
    """Every timestamp surface named in issue #225 emits the shared markup."""

    def assertLocalTimeMarkup(self, response, style="datetime"):
        self.assertContains(response, '<time class="local-time"')
        self.assertContains(response, f'data-local-time-style="{style}"')

    def test_header_signed_in_since(self):
        player = self.create_player()
        self.login_as(player)
        response = self.client.get("/tarmar/accounts/profile/")
        self.assertContains(response, "Signed in since")
        self.assertLocalTimeMarkup(response)

    def test_users_list_last_login(self):
        admin = self.create_admin()
        self.login_as(admin)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertLocalTimeMarkup(response)

    def test_battle_list_created_at(self):
        dm = self.create_dm()
        create_battle(dm, name="Timezone Sweep", count=2, seed=1)
        self.login_as(dm)
        response = self.client.get("/tarmar/battles/")
        self.assertLocalTimeMarkup(response)

    def test_character_list_created_at_uses_date_style(self):
        player = self.create_player()
        create_character(player)
        self.login_as(player)
        response = self.client.get("/tarmar/characters/")
        self.assertLocalTimeMarkup(response, style="date")

    def test_character_edit_created_at_uses_date_style(self):
        player = self.create_player()
        character = create_character(player)
        self.login_as(player)
        response = self.client.get(f"/tarmar/characters/{character.pk}/")
        self.assertContains(response, "Created")
        self.assertLocalTimeMarkup(response, style="date")

    def test_deleted_characters_deleted_at(self):
        admin = self.create_admin()
        player = self.create_player()
        character = create_character(player)
        character.is_deleted = True
        character.deleted_at = timezone.now()
        character.save()
        self.login_as(admin)
        response = self.client.get("/tarmar/characters/deleted/")
        self.assertLocalTimeMarkup(response)

    def test_users_list_with_no_last_login_shows_the_placeholder_not_markup(self):
        """A user who has never logged in gets the "—" placeholder; nothing
        should try to render a <time> element for a None value."""
        admin = self.create_admin()
        # create_user never logs the user in, so last_login stays None.
        self.create_user("never_logged_in")
        self.login_as(admin)
        response = self.client.get("/tarmar/accounts/users/")
        self.assertContains(response, "—")


class RenderedSurfacesUseCharacterModel(BaseTestCase):
    """Guards the fixture assumption the tests above rely on: Character.
    created_at is a real DateTimeField, not a DateField -- the reason every
    surface above (even the date-only displays) routes through local_time
    instead of being left as a bare UTC date."""

    def test_created_at_is_a_datetime_field(self):
        field = Character._meta.get_field("created_at")
        self.assertEqual(field.get_internal_type(), "DateTimeField")
