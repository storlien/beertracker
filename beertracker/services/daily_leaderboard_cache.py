"""In-memory cache for daily usage with Firestore listeners."""

import logging
import threading
from typing import Any

from beertracker.core.leaderboard import compute_daily_leaderboard
from beertracker.core.models import LeaderboardEntry, User

logger = logging.getLogger(__name__)

COLLECTION_DAILY_USAGE = "dailyUsage"
COLLECTION_USERS = "users"


class DailyLeaderboardCache:
    """Holds daily usage and user data in memory, listening to Firestore.

    Watches the ``dailyUsage`` collection so that whenever a bar-day
    document is created or updated (i.e. new purchases arrive), the
    active (latest) bar day's leaderboard is recomputed and written to
    ``leaderboard/daily`` (1 Firestore write).

    Also watches ``users`` so that linking/unlinking cards updates
    the daily leaderboard immediately.
    """

    def __init__(self, db: Any, leaderboard_repo: "LeaderboardRepository") -> None:
        self._db = db
        self._leaderboard_repo = leaderboard_repo
        self._daily_usage: dict[str, dict[str, float]] = {}
        self._users: dict[str, User] = {}
        self._lock = threading.RLock()

        self._daily_watch = None
        self._users_watch = None
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Attach Firestore listeners and warm the cache."""
        if self._started:
            return

        logger.info("DailyLeaderboardCache: starting Firestore listeners...")
        self._warm_cache()

        self._daily_watch = self._db.collection(COLLECTION_DAILY_USAGE).on_snapshot(
            self._on_daily_changed
        )
        self._users_watch = self._db.collection(COLLECTION_USERS).on_snapshot(
            self._on_users_changed
        )

        self._started = True
        logger.info(
            "DailyLeaderboardCache: watching %d daily docs, %d users",
            len(self._daily_usage),
            len(self._users),
        )

    def stop(self) -> None:
        """Detach listeners."""
        if self._daily_watch:
            self._daily_watch.unsubscribe()
            self._daily_watch = None
        if self._users_watch:
            self._users_watch.unsubscribe()
            self._users_watch = None
        self._started = False
        logger.info("DailyLeaderboardCache: listeners stopped")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _warm_cache(self) -> None:
        """One-time bulk read on startup."""
        with self._lock:
            for doc in self._db.collection(COLLECTION_USERS).stream():
                data = doc.to_dict() or {}
                self._users[doc.id] = _user_from_doc(doc.id, data)

            for doc in self._db.collection(COLLECTION_DAILY_USAGE).stream():
                data = doc.to_dict() or {}
                self._daily_usage[doc.id] = _cards_map_from_doc(data)

            self._recompute_and_write()

    def _on_daily_changed(self, _col_snapshot, _changes, _read_time) -> None:
        """Firestore ``on_snapshot`` callback for dailyUsage collection."""
        updated = False
        with self._lock:
            for change in _changes:
                doc = change.document
                bar_day = doc.id
                if change.type.name == "REMOVED":
                    self._daily_usage.pop(bar_day, None)
                    updated = True
                else:
                    data = doc.to_dict() or {}
                    new_map = _cards_map_from_doc(data)
                    old_map = self._daily_usage.get(bar_day)
                    if old_map is None or old_map != new_map:
                        self._daily_usage[bar_day] = new_map
                        updated = True

            if updated:
                self._recompute_and_write()

    def _on_users_changed(self, _col_snapshot, _changes, _read_time) -> None:
        """Firestore ``on_snapshot`` callback for users collection."""
        updated = False
        with self._lock:
            for change in _changes:
                doc = change.document
                user_id = doc.id
                if change.type.name == "REMOVED":
                    self._users.pop(user_id, None)
                    updated = True
                else:
                    data = doc.to_dict() or {}
                    old_user = self._users.get(user_id)
                    new_user = _user_from_doc(user_id, data)
                    if old_user is None or not _users_equal(old_user, new_user):
                        self._users[user_id] = new_user
                        updated = True

            if updated:
                self._recompute_and_write()

    def _recompute_and_write(self) -> None:
        """Recompute the daily leaderboard for the active day and write to Firestore."""
        if not self._daily_usage:
            logger.debug("No daily usage data — skipping daily leaderboard recomputation")
            return

        active_day = max(self._daily_usage.keys())
        daily_cards = self._daily_usage[active_day]

        entries = compute_daily_leaderboard(daily_cards, self._users)
        self._leaderboard_repo.write_daily(entries, active_day)
        logger.debug(
            "Daily leaderboard recomputed for %s (%d entries)",
            active_day,
            len(entries),
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _cards_map_from_doc(data: dict) -> dict[str, float]:
    """Extract card->amount map from a dailyUsage document."""
    cards = data.get("cards")
    if not isinstance(cards, dict):
        return {}
    return {str(k): float(v) for k, v in cards.items()}


def _user_from_doc(user_id: str, data: dict) -> User:
    cards = data.get("cards", [])
    if not isinstance(cards, list):
        cards = []
    return User(
        user_id=user_id,
        first_name=data.get("firstName", ""),
        last_name=data.get("lastName", ""),
        card_ids=[str(c) for c in cards],
    )


def _users_equal(a: User, b: User) -> bool:
    return (
        a.first_name == b.first_name
        and a.last_name == b.last_name
        and a.card_ids == b.card_ids
    )
