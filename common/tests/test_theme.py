"""Tests for host-driven presentation themes (issue #265)."""

from django.test import Client, TestCase, override_settings

from common.test_helpers import mark_gate_passed
from common.theme import (
    CLASSIC_ARENA_THEME,
    DEFAULT_THEME,
    TARMAR_THEME,
    THEMES,
    normalize_host,
    theme_for_host,
)

THEMED = {"melee.example.com": "classic-arena"}


class NormalizeHostTest(TestCase):
    def test_strips_port_and_lowercases(self):
        self.assertEqual(normalize_host("Melee.Example.com:8000"), "melee.example.com")

    def test_keeps_ipv6_literal_intact(self):
        """The port follows the closing bracket, so a naive split would cut
        the address itself in half at its first colon."""
        self.assertEqual(normalize_host("[::1]:8000"), "[::1]")
        self.assertEqual(normalize_host("[::1]"), "[::1]")


class ThemeForHostTest(TestCase):
    def test_themed_host_selects_its_theme(self):
        self.assertEqual(
            theme_for_host("melee.example.com", THEMED), CLASSIC_ARENA_THEME
        )

    def test_themed_host_selects_its_theme_through_a_port(self):
        self.assertEqual(
            theme_for_host("melee.example.com:8000", THEMED), CLASSIC_ARENA_THEME
        )

    def test_any_other_host_gets_the_default(self):
        self.assertEqual(theme_for_host("tarmar.example.com", THEMED), DEFAULT_THEME)
        self.assertEqual(DEFAULT_THEME, TARMAR_THEME)

    def test_unknown_theme_key_falls_back_rather_than_raising(self):
        """The mapping comes from deployment config, so a typo in it must
        serve the ordinary site rather than 500 every page."""
        self.assertEqual(
            theme_for_host("melee.example.com", {"melee.example.com": "no-such-theme"}),
            DEFAULT_THEME,
        )


@override_settings(
    ALLOWED_HOSTS=["testserver", "melee.example.com"], THEME_HOSTS=THEMED
)
class ThemedRenderingTest(TestCase):
    """What a page actually says and links when a themed host asks for it."""

    def setUp(self):
        self.client = Client()
        # Past the passphrase gate: this suite is about what a themed page
        # says, and the gate has its own coverage below.
        mark_gate_passed(self.client)

    def test_themed_host_gets_the_arena_wordmark_and_sheet(self):
        response = self.client.get("/", follow=True, HTTP_HOST="melee.example.com")
        body = response.content.decode()
        self.assertIn('data-theme="classic-arena"', body)
        self.assertIn("The classic arena", body)
        self.assertIn("theme-classic-arena.css", body)

    def test_themed_host_credits_the_game_it_evokes(self):
        response = self.client.get("/", follow=True, HTTP_HOST="melee.example.com")
        self.assertIn("Steve Jackson Games", response.content.decode())

    def test_themed_host_shows_no_tarmar_wordmark_image(self):
        """The arena theme is text-only on purpose; serving Tarmar's mark
        under someone else's name is the failure this pins."""
        response = self.client.get("/", follow=True, HTTP_HOST="melee.example.com")
        self.assertNotIn("tarmar_gold.svg", response.content.decode())

    def test_default_host_is_untouched(self):
        response = self.client.get("/", follow=True, HTTP_HOST="testserver")
        body = response.content.decode()
        self.assertIn('data-theme="tarmar"', body)
        self.assertIn("Tarmar Studio", body)
        self.assertIn("tarmar_gold.svg", body)
        self.assertNotIn("theme-classic-arena.css", body)
        self.assertNotIn("Steve Jackson Games", body)


class ThemeSeedsRulesButDoesNotDecideThemTest(TestCase):
    """Where the line moved, and where it did not (#265, then #332).

    A theme now names the profile a *new* draft opened at its door starts
    on -- the Melee door proposes classic Melee -- and that is the whole of
    its say in the rules. It carries no field naming the profile of a
    battle that already exists; if one ever appears here, a hostname has
    become a rules input for resolved play and this is the test that should
    fail first.
    """

    def test_theme_carries_a_default_and_no_resolved_rules_vocabulary(self):
        for theme in (TARMAR_THEME, CLASSIC_ARENA_THEME):
            with self.subTest(theme=theme.key):
                self.assertTrue(theme.default_rules_profile)
                self.assertFalse(hasattr(theme, "rules_profile"))
                self.assertFalse(hasattr(theme, "profile"))

    def test_each_door_proposes_the_rules_it_advertises(self):
        self.assertEqual(TARMAR_THEME.default_rules_profile, "tarmar")
        self.assertEqual(CLASSIC_ARENA_THEME.default_rules_profile, "classic-melee")

    def test_every_theme_names_a_profile_the_app_can_persist(self):
        """The names are plain strings because ``common`` may not import
        ``battle``; ``battle.persistence.storable_profile`` falls back to
        Tarmar for a name it does not know. That fallback is insurance, not
        the working path -- no shipped theme should ever reach it. Checked
        from this side of the boundary: a test under ``battle`` that read
        the theme would trip ``ThemeIsNotReadByTheRulesTest``.
        """
        from battle.persistence import PROFILE_CHOICES

        for theme in THEMES.values():
            with self.subTest(theme=theme.key):
                self.assertIn(theme.default_rules_profile, dict(PROFILE_CHOICES))


@override_settings(
    ALLOWED_HOSTS=["testserver", "melee.example.com"], THEME_HOSTS=THEMED
)
class ThemedGateTest(TestCase):
    """The gate is the first page an arriving visitor sees and it does not
    extend base.html, so it is the one page a theme can silently miss."""

    def setUp(self):
        self.client = Client()

    def test_gate_is_themed_on_a_themed_host(self):
        response = self.client.get("/", follow=True, HTTP_HOST="melee.example.com")
        body = response.content.decode()
        self.assertIn('data-theme="classic-arena"', body)
        self.assertIn("Melee", body)
        self.assertIn("Steve Jackson Games", body)
        self.assertNotIn("Tarmar Studio", body)

    def test_gate_is_unchanged_on_the_default_host(self):
        response = self.client.get("/", follow=True, HTTP_HOST="testserver")
        body = response.content.decode()
        self.assertIn('data-theme="tarmar"', body)
        self.assertIn("Tarmar Studio", body)
        self.assertIn("Speak, friend, and enter.", body)
        self.assertNotIn("Steve Jackson Games", body)


class ThemeIsNotReadByTheRulesTest(TestCase):
    """A structural guard, not a behavioural one.

    ``ThemeIsNotARulesSwitchTest`` pins that a theme carries no rules
    vocabulary; this pins the other direction -- that no rules code reaches
    for a theme. The moment a battle, its engine adapter or its profile
    imports ``common.theme``, the hostname has become a rules input and the
    one-deploy-two-front-doors story stops being true.
    """

    RULES_PACKAGES = ("battle", "characters", "bestiary", "encounters")

    def test_no_rules_package_imports_the_theme(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        offenders = []
        for package in self.RULES_PACKAGES:
            for module in (root / package).rglob("*.py"):
                if "common.theme" in module.read_text():
                    offenders.append(str(module.relative_to(root)))
        self.assertEqual(offenders, [], f"rules code reading the theme: {offenders}")
