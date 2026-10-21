"""The one way a downloaded file writes an instant (#123)."""

import datetime
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from common.instants import labelled_instant

#: A moment just after UTC midnight, where the calendar date differs by zone.
INSTANT = datetime.datetime(2026, 10, 15, 2, 50, 11, tzinfo=datetime.UTC)


class LabelledInstantTest(SimpleTestCase):
    def test_an_instant_is_written_in_the_active_zone_and_names_it(self):
        self.assertEqual(labelled_instant(INSTANT), "2026-10-15 02:50:11 UTC")

    def test_another_active_zone_moves_the_clock_and_says_so(self):
        with timezone.override("America/New_York"):
            self.assertEqual(labelled_instant(INSTANT), "2026-10-14 22:50:11 EDT")

    def test_no_value_is_now(self):
        with mock.patch("django.utils.timezone.now", return_value=INSTANT):
            self.assertEqual(labelled_instant(), "2026-10-15 02:50:11 UTC")

    def test_a_naive_value_names_no_instant_and_is_refused(self):
        with self.assertRaises(ValueError):
            labelled_instant(INSTANT.replace(tzinfo=None))
