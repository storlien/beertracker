"""Unit tests for leaderboard cache graceful degradation (P4).

Verifies that Firestore quota errors during cache startup do NOT crash
the service: the cache stays unstarted and can be retried later.
"""

from unittest.mock import MagicMock

from google.api_core.exceptions import ResourceExhausted

from beertracker.services.daily_leaderboard_cache import DailyLeaderboardCache
from beertracker.services.leaderboard_cache import LeaderboardCache


def _make_db():
    """Build a mock Firestore client with empty collections."""
    db = MagicMock()
    db.collection.return_value.stream.return_value = iter([])
    db.collection.return_value.on_snapshot.return_value = MagicMock()
    return db


def _make_quota_db():
    """Build a mock Firestore client whose reads always hit 429."""
    db = MagicMock()
    db.collection.return_value.stream.side_effect = ResourceExhausted(
        "429 Quota exceeded."
    )
    return db


class TestLeaderboardCacheDegradation:
    """Tests for LeaderboardCache.start() quota resilience."""

    def test_starts_degraded_on_quota_error(self):
        cache = LeaderboardCache(_make_quota_db(), MagicMock())

        cache.start()  # must NOT raise

        assert cache.is_started is False

    def test_starts_healthy_when_quota_ok(self):
        db = _make_db()
        cache = LeaderboardCache(db, MagicMock())

        cache.start()

        assert cache.is_started is True
        # Two watches attached: cards + users
        assert db.collection.return_value.on_snapshot.call_count == 2

    def test_start_is_idempotent(self):
        db = _make_db()
        cache = LeaderboardCache(db, MagicMock())

        cache.start()
        cache.start()  # second call must be a no-op

        assert db.collection.return_value.on_snapshot.call_count == 2

    def test_degraded_cache_can_recover_on_retry(self):
        """After a quota failure, a later start() succeeds (retry path)."""
        db = _make_quota_db()
        cache = LeaderboardCache(db, MagicMock())

        cache.start()
        assert cache.is_started is False

        # Quota recovers: reads now return empty collections
        db.collection.return_value.stream.side_effect = None
        db.collection.return_value.stream.return_value = iter([])
        db.collection.return_value.on_snapshot.return_value = MagicMock()

        cache.start()
        assert cache.is_started is True


class TestDailyLeaderboardCacheDegradation:
    """Tests for DailyLeaderboardCache.start() quota resilience."""

    def test_starts_degraded_on_quota_error(self):
        cache = DailyLeaderboardCache(_make_quota_db(), MagicMock())

        cache.start()  # must NOT raise

        assert cache.is_started is False

    def test_starts_healthy_when_quota_ok(self):
        db = _make_db()
        cache = DailyLeaderboardCache(db, MagicMock())

        cache.start()

        assert cache.is_started is True
        # Two watches attached: dailyUsage + users
        assert db.collection.return_value.on_snapshot.call_count == 2

    def test_degraded_cache_can_recover_on_retry(self):
        """After a quota failure, a later start() succeeds (retry path)."""
        db = _make_quota_db()
        cache = DailyLeaderboardCache(db, MagicMock())

        cache.start()
        assert cache.is_started is False

        db.collection.return_value.stream.side_effect = None
        db.collection.return_value.stream.return_value = iter([])
        db.collection.return_value.on_snapshot.return_value = MagicMock()

        cache.start()
        assert cache.is_started is True
