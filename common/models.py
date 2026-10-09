"""Models backing shared, cross-app infrastructure."""

from django.db import models


class RateLimitCounter(models.Model):
    """A DB-atomic fixed-window counter for one rate-limit key.

    ``DatabaseCache.incr()`` has no override of its own, so it runs
    ``BaseCache``'s default implementation: read the current value, add the
    delta in Python, write the result back. Two workers can both read the
    same count and each write back the same incremented value, silently
    losing one increment under concurrent load -- issue #161 part 1.

    Every write this model makes to ``count`` goes through an ``F("count")``
    expression inside a single ``UPDATE`` statement (see
    ``common.rate_limit``), which is atomic at the SQL level: there is no
    Python-side read of the current value for the increment to race
    against, so the database serializes concurrent increments of the same
    row on its own, the same way any ``UPDATE ... SET n = n + 1`` does
    regardless of caller count.

    ``key`` doubles as the cache-style key rate-limit callers already use
    (e.g. ``"rate_limit:gate:127.0.0.1"``), so no separate lookup table is
    needed.
    """

    key = models.CharField(max_length=255, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)

    def __str__(self) -> str:  # pragma: no cover -- debugging aid only
        return f"{self.key} ({self.count}, expires {self.expires_at})"
