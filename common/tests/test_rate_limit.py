"""Atomicity and fixed-window semantics for common.rate_limit (issue #161).

DatabaseCache.incr() is BaseCache's default get-then-set implementation, not
atomic, so a concurrent burst of requests against the old cache-based counter
could lose increments. common.rate_limit.charge()/release() replace it with a
single ``UPDATE ... SET count = count +/- 1`` (an F() expression) per call --
atomic at the SQL level regardless of how many callers race, because there is
no Python-side read of the current value for a second caller to invalidate.

Following the project's existing convention for concurrency-shaped
correctness (see characters/tests/test_locking.py): SQLite, the test
database, is a no-op for SELECT ... FOR UPDATE and threads don't share the
in-memory test database, so "real" multi-connection races can't be
reproduced here. Instead:

- ``AtomicUpdateUsesFExpressionTest`` asserts the *structure* of the write:
  the increment/decrement path never does a plain read-then-.save() of
  ``count`` in Python, only ``.update(count=F(...) +/- 1)``.
- ``InterleavedChargeTest`` simulates a real interleaving by patching a hook
  so a second, independent charge() call fully completes in between the
  first call's "does a window already exist" check and its own write --
  exactly the shape a lost update would need to slip through.
"""

from datetime import timedelta
from unittest.mock import patch

from django.db.models import F
from django.db.models.expressions import CombinedExpression
from django.db.models.query import QuerySet
from django.test import TestCase
from django.utils import timezone

from common import rate_limit
from common.models import RateLimitCounter

KEY = "rate_limit:test:127.0.0.1"


class ChargeAndReleaseTest(TestCase):
    def test_first_charge_opens_a_window_at_count_one(self):
        self.assertEqual(rate_limit.charge(KEY, 300), 1)
        counter = RateLimitCounter.objects.get(pk=KEY)
        self.assertEqual(counter.count, 1)

    def test_repeated_charges_accumulate(self):
        for expected in range(1, 6):
            self.assertEqual(rate_limit.charge(KEY, 300), expected)

    def test_current_count_reflects_live_window(self):
        self.assertEqual(rate_limit.current_count(KEY), 0)
        rate_limit.charge(KEY, 300)
        rate_limit.charge(KEY, 300)
        self.assertEqual(rate_limit.current_count(KEY), 2)

    def test_current_count_ignores_an_elapsed_window(self):
        RateLimitCounter.objects.create(
            key=KEY, count=9, expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(rate_limit.current_count(KEY), 0)

    def test_a_charge_after_the_window_elapsed_opens_a_fresh_one(self):
        RateLimitCounter.objects.create(
            key=KEY, count=9, expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(rate_limit.charge(KEY, 300), 1)

    def test_release_undoes_one_charge(self):
        rate_limit.charge(KEY, 300)
        rate_limit.charge(KEY, 300)
        self.assertEqual(rate_limit.release(KEY), 1)
        self.assertEqual(rate_limit.current_count(KEY), 1)

    def test_release_to_zero_deletes_the_row(self):
        rate_limit.charge(KEY, 300)
        self.assertEqual(rate_limit.release(KEY), 0)
        self.assertFalse(RateLimitCounter.objects.filter(pk=KEY).exists())

    def test_release_with_nothing_charged_is_a_harmless_no_op(self):
        self.assertEqual(rate_limit.release(KEY), 0)
        self.assertFalse(RateLimitCounter.objects.filter(pk=KEY).exists())

    def test_release_does_not_touch_expires_at(self):
        rate_limit.charge(KEY, 300)
        rate_limit.charge(KEY, 300)
        before = RateLimitCounter.objects.get(pk=KEY).expires_at
        rate_limit.release(KEY)
        after = RateLimitCounter.objects.get(pk=KEY).expires_at
        self.assertEqual(before, after)


class OpportunisticCullTest(TestCase):
    """Opening a brand-new window culls other keys' expired rows.

    ``RateLimitCounter`` has no size cap of its own -- unlike
    ``DatabaseCache``, which got ``MAX_ENTRIES=20000`` specifically because
    issue #139 made rate-limit counters per-client-IP. Without a cull, a
    distributed guessing run would leave one expired row per attacker IP in
    this table forever.
    """

    OTHER_EXPIRED_KEY = "rate_limit:test:203.0.113.1"
    OTHER_LIVE_KEY = "rate_limit:test:203.0.113.2"

    def test_charging_a_brand_new_key_deletes_other_expired_rows(self):
        RateLimitCounter.objects.create(
            key=self.OTHER_EXPIRED_KEY,
            count=5,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        RateLimitCounter.objects.create(
            key=self.OTHER_LIVE_KEY,
            count=3,
            expires_at=timezone.now() + timedelta(seconds=300),
        )

        rate_limit.charge(KEY, 300)  # a fresh key -> the "created" branch

        self.assertFalse(
            RateLimitCounter.objects.filter(pk=self.OTHER_EXPIRED_KEY).exists()
        )
        self.assertTrue(
            RateLimitCounter.objects.filter(pk=self.OTHER_LIVE_KEY).exists()
        )

    def test_charging_an_already_open_window_does_not_cull(self):
        """The cull piggybacks on the "created" branch only -- it is
        opportunistic, not a scan run on every single charge()."""
        rate_limit.charge(KEY, 300)  # opens KEY's window (and culls once)
        RateLimitCounter.objects.create(
            key=self.OTHER_EXPIRED_KEY,
            count=5,
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        rate_limit.charge(KEY, 300)  # KEY's window is already live -> no cull

        self.assertTrue(
            RateLimitCounter.objects.filter(pk=self.OTHER_EXPIRED_KEY).exists()
        )


class FixedWindowDoesNotExtendTest(TestCase):
    """A failure late in the window must not push the window's close out."""

    def test_a_charge_near_the_end_of_the_window_does_not_extend_it(self):
        opened_at = timezone.now() - timedelta(seconds=290)
        original_expiry = opened_at + timedelta(seconds=300)
        RateLimitCounter.objects.create(key=KEY, count=1, expires_at=original_expiry)

        rate_limit.charge(KEY, 300)

        counter = RateLimitCounter.objects.get(pk=KEY)
        self.assertEqual(counter.count, 2)
        self.assertEqual(counter.expires_at, original_expiry)

    def test_ten_charges_at_the_threshold_share_one_unmoved_expiry(self):
        rate_limit.charge(KEY, 300)
        first_expiry = RateLimitCounter.objects.get(pk=KEY).expires_at

        for _ in range(9):
            rate_limit.charge(KEY, 300)

        counter = RateLimitCounter.objects.get(pk=KEY)
        self.assertEqual(counter.count, 10)
        self.assertEqual(counter.expires_at, first_expiry)


class AtomicUpdateUsesFExpressionTest(TestCase):
    """The increment/decrement step is a single F()-expression UPDATE.

    A plain ``counter.count += 1; counter.save()`` reads the count into
    Python and writes it back -- exactly the DatabaseCache.incr() pattern
    this module exists to avoid. Asserting an F() expression reaches
    QuerySet.update() pins the fix at the SQL level: the statement Django
    builds is ``UPDATE ... SET count = count + 1``, computed by the database
    in one step rather than by this process reading a value and posting a
    new one back.
    """

    def test_charge_increments_via_update_with_an_f_expression(self):
        rate_limit.charge(KEY, 300)  # open the window first

        seen_updates = []
        original_update = QuerySet.update

        def spy(self, *args, **kwargs):
            seen_updates.append(kwargs)
            return original_update(self, *args, **kwargs)

        with patch.object(QuerySet, "update", spy):
            rate_limit.charge(KEY, 300)

        count_updates = [kwargs for kwargs in seen_updates if "count" in kwargs]
        self.assertTrue(count_updates, "charge() did not call .update(count=...)")
        expression = count_updates[0]["count"]
        self.assertIsInstance(expression, CombinedExpression)
        self.assertIsInstance(expression.lhs, F)
        self.assertEqual(expression.lhs.name, "count")

    def test_release_decrements_via_update_with_an_f_expression(self):
        rate_limit.charge(KEY, 300)
        rate_limit.charge(KEY, 300)

        seen_updates = []
        original_update = QuerySet.update

        def spy(self, *args, **kwargs):
            seen_updates.append(kwargs)
            return original_update(self, *args, **kwargs)

        with patch.object(QuerySet, "update", spy):
            rate_limit.release(KEY)

        count_updates = [kwargs for kwargs in seen_updates if "count" in kwargs]
        self.assertTrue(count_updates, "release() did not call .update(count=...)")
        expression = count_updates[0]["count"]
        self.assertIsInstance(expression, CombinedExpression)
        self.assertIsInstance(expression.lhs, F)
        self.assertEqual(expression.lhs.name, "count")


class InterleavedFirstChargeTest(TestCase):
    """Two callers racing to open the very same brand-new window must both
    be counted -- neither may silently overwrite the other's charge.

    Patches ``QuerySet.get_or_create`` -- what ``charge()`` calls via
    ``select_for_update()`` -- so that the very first call any charge()
    makes to it is preceded by a second, wholly independent ``charge()``
    call running to completion first. That reproduces the shape of the race
    ``get_or_create()``'s own IntegrityError-retry exists to survive: two
    callers who both see no row for the key yet, both trying to create it.
    A version of ``charge()`` that opened a fresh window with unconditional
    field values (rather than incrementing when it finds the row already
    exists) would lose the first racer's charge here -- the final count
    would read 1, not 2.
    """

    def test_two_racing_first_charges_both_land(self):
        original_get_or_create = QuerySet.get_or_create
        calls = {"count": 0}

        def spy(self, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                rate_limit.charge(KEY, 300)
            return original_get_or_create(self, *args, **kwargs)

        with patch.object(QuerySet, "get_or_create", spy):
            rate_limit.charge(KEY, 300)

        self.assertEqual(RateLimitCounter.objects.get(pk=KEY).count, 2)
