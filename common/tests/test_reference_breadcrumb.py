"""The reference trail names the page being read (issue #339, item 28).

Every reference page's breadcrumb said only "Reference": BreadcrumbMixin
built the trail before the view had looked the page up.
"""

from django.urls import reverse

from common.test_helpers import BaseTestCase

SKILL_TRACKS = "public-rules/characters/prior-experience/skill-tracks"


class ReferenceBreadcrumbTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user("rules_reader"))

    def test_the_overview_is_just_reference(self):
        response = self.client.get(reverse("reference:index"))

        self.assertEqual(
            response.context["breadcrumb"][-1], {"label": "Reference", "url": ""}
        )

    def test_a_section_page_is_reference_then_the_page(self):
        response = self.client.get(reverse("reference:view", args=[SKILL_TRACKS]))

        self.assertEqual(response.status_code, 200)
        trail = response.context["breadcrumb"]
        self.assertEqual(trail[-1], {"label": "Skill Tracks", "url": ""})
        self.assertIn({"label": "Reference", "url": reverse("reference:index")}, trail)
