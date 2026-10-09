"""Tests extracted from monolithic root tests.py (issue #17).

Imports kept broad to match the original module — the legacy file did
most of its imports inline inside test methods.
"""

from characters.models import Character
from common.test_helpers import BaseTestCase


class RateLimitTest(BaseTestCase):
    """Tests for rate limiting functionality."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user("player")

    def test_rate_limit_allows_normal_requests(self):
        """Normal request rate is allowed."""
        from django.core.cache import cache

        cache.clear()  # Clear any existing rate limit data

        self.login_as(self.user)

        # Create a character to update
        char = Character.objects.create(
            user=self.user,
            name="Test",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
        )

        # First request should succeed
        response = self.post_json(
            f"/tarmar/characters/{char.pk}/update-field/",
            {"field": "name", "value": "NewName"},
        )
        self.assertEqual(response.status_code, 200)

    def test_rate_limit_blocks_excessive_requests(self):
        """Excessive requests are blocked with 429."""
        from django.core.cache import cache

        cache.clear()

        self.login_as(self.user)

        char = Character.objects.create(
            user=self.user,
            name="Test",
            strength=10,
            dexterity=10,
            intelligence=10,
            wisdom=10,
            constitution=10,
            charisma=10,
            appearance_roll="3",
            height_roll="3",
            weight_roll="3",
            fatigue_roll=7,
        )

        # Manually set cache to simulate rate limit reached
        cache_key = f"rate_limit:character_update:user_{self.user.pk}"
        cache.set(cache_key, 60, 60)  # Set to max

        # Next request should be rate limited
        response = self.post_json(
            f"/tarmar/characters/{char.pk}/update-field/",
            {"field": "name", "value": "AnotherName"},
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn("Rate limit", response.json().get("error", ""))

    def test_rate_limit_decorator_increments_then_blocks(self):
        """The decorator allows up to max_requests, then 429s with Retry-After.

        Drives the real increment logic (rather than pre-seeding the counter) so
        a broken increment/window would be caught.
        """
        from django.core.cache import cache
        from django.http import HttpResponse
        from django.test import RequestFactory

        from common.decorators import rate_limit

        cache.clear()

        @rate_limit("unit_test_rl", max_requests=3, window_seconds=60)
        def view(request):
            return HttpResponse("ok")

        factory = RequestFactory()

        def call():
            request = factory.post("/unit-test-rl/")
            request.user = self.user
            return view(request)

        for _ in range(3):
            self.assertEqual(call().status_code, 200)
        blocked = call()
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked["Retry-After"], "60")
