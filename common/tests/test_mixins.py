"""Tests for the shared DM-tools gate mixin."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.test import SimpleTestCase
from origami_auth.mixins import AdminOrDMRequiredMixin
from origami_common.mixins import ActivePageMixin

from bestiary.views import BeastDetailView, CatalogView, GenerateBeastView
from common.mixins import DMToolsAccessMixin
from encounters.views import EncounterGeneratorView
from ruins.views import GenerateHookView, GenerateRuinView


class DMToolsAccessMixinTest(SimpleTestCase):
    def test_requires_login_then_a_dm_or_admin(self):
        for gate in (ActivePageMixin, LoginRequiredMixin, AdminOrDMRequiredMixin):
            self.assertTrue(issubclass(DMToolsAccessMixin, gate))
        self.assertEqual(DMToolsAccessMixin.login_url, "login")

    def test_every_dm_tool_view_uses_it_with_its_own_active_page(self):
        expected_pages = {
            CatalogView: "bestiary",
            BeastDetailView: "bestiary",
            GenerateBeastView: "bestiary",
            EncounterGeneratorView: "encounters",
            GenerateRuinView: "ruins",
            GenerateHookView: "hooks",
        }
        for view_class, active_page in expected_pages.items():
            with self.subTest(view=view_class.__name__):
                self.assertTrue(issubclass(view_class, DMToolsAccessMixin))
                self.assertEqual(view_class.active_page, active_page)
