"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from common.test_helpers import BaseTestCase


class ProjectRunningTest(BaseTestCase):
    def test_project_is_configured(self):
        """Verify Django settings are properly configured."""
        from django.conf import settings

        self.assertTrue(settings.configured)
        self.assertIn("tarmar_studio", settings.ROOT_URLCONF)

    def test_server_responds(self):
        """Verify the server responds to requests."""
        response = self.client.get("/")
        self.assertRedirects(response, "/tarmar/")

    def test_admin_accessible(self):
        """Verify admin URL is accessible."""
        response = self.client.get("/admin/login/")
        self.assertEqual(response.status_code, 200)
