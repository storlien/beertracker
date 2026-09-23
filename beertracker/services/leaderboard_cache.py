"""In-memory cache for cards and users with Firestore listeners."""

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from beertracker.core.leaderboard import compute_leaderboard
from beertracker.core.models import Card, User

logger = logging.getLogger(__name__)

COLLECTION_CARDS = "cards"
COLLECTION_USERS = "users"
COLLECTION_LEADERBOARD = "leaderboard"
DOC_TOP100 = "top100"


class LeaderboardCache:
    """Holds the full card and user collections in memory.

    Listens to ``on_snapshot`` on both ``cards`` and ``users`` collections,
    so the in-memory representation is always up to date with Firestore.
    This eliminates the need for bulk reads on every sync.

    After any change, the leaderboard is recomputed **in memory** and
    written to ``leaderboard/top100`` (1 Firestore write).
    """

    def __init__(self, db: Any, leaderboard_repo: "LeaderboardRepository") -> None:
        self._db = db
        self._leaderboard_repo = leaderboard_repo
        self._cards: dict[str, Card] = {}
        self._users: dict[str, User] = {}
        self._lock = threading.RLock()

        self._cards_watch = None
        self._users_watch = None
        self._started = False

        self._last_activity_at: datetime | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Attach Firestore listeners and warm the cache."""
        if self._started:
            return

        logger.info("LeaderboardCache: starting Firestore listeners...")

        # Initial population (one-time bulk read on startup)
        self._warm_cache()

        # Real-time listeners
        self._cards_watch = self._db.collection(COLLECTION_CARDS).on_snapshot(
            self._on_cards_changed
        )
        self._users_watch = self._db.collection(COLLECTION_USERS).on_snapshot(
            self._on_users_changed
        )

        self._started = True
        logger.info(
            "LeaderboardCache: watching %d cards, %d users",
            len(self._cards),
            len(self._users),
        )

    def stop(self) -> None:
        """Detach listeners."""
        if self._cards_watch:
            self._cards_watch.unsubscribe()
            self._cards_watch = None
        if self._users_watch:
            self._users_watch.unsubscribe()
            self._users_watch = None
        self._started = False
        logger.info("LeaderboardCache: listeners stopped")

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def has_activity(self, since_minutes: int) -> bool:
        """Return True if any card was updated within ``since_minutes``."""
        with self._lock:
            if self._last_activity_at is None:
                return False
            delta = (datetime.now(timezone.utc) - self._last_activity_at).total_seconds()
            return delta < (since_minutes * 60)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _warm_cache(self) -> None:
        """One-time bulk read on startup."""
        with self._lock:
            for doc in self._db.collection(COLLECTION_CARDS).stream():
                data = doc.to_dict() or {}
                self._cards[doc.id] = _card_from_doc(doc.id, data)

            for doc in self._db.collection(COLLECTION_USERS).stream():
                data = doc.to_dict() or {}
                self._users[doc.id] = _user_from_doc(doc.id, data)

            # Treat startup as activity so we don't immediately hibernate
            self._last_activity_at = datetime.now(timezone.utc)
            self._recompute_and_write()

    def _on_cards_changed(self, col_snapshot, _changes, _read_time) -> None:
        """Firestore ``on_snapshot`` callback for cards collection."""
        updated = False
        with self._lock:
            for change in _changes:
                doc = change.document
                card_id = doc.id
                if change.type.name == "REMOVED":
                    self._cards.pop(card_id, None)
                    updated = True
                else:
                    data = doc.to_dict() or {}
                    old_card = self._cards.get(card_id)
                    new_card = _card_from_doc(card_id, data)
                    # Only treat a real change as activity (avoids noise)
                    if old_card is None or not _cards_equal(old_card, new_card):
                        self._cards[card_id] = new_card
                        updated = True

            if updated:
                self._last_activity_at = datetime.now(timezone.utc)
                self._recompute_and_write()

    def _on_users_changed(self, col_snapshot, _changes, _read_time) -> None:
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
        """Recompute leaderboard in memory and write to Firestore."""
        entries = compute_leaderboard(self._cards, self._users, limit=100)
        self._leaderboard_repo.write_top100(entries)
        logger.debug(
            "Leaderboard recomputed and written (%d entries)", len(entries)
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _card_from_doc(card_id: str, data: dict) -> Card:
    return Card(
        card_number=card_id,
        total_sum=float(data.get("sum", 0.0)),
        linked_user_id=data.get("linkedUserId") or None,
        created_at=None,
        updated_at=None,
    )


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


def _cards_equal(a: Card, b: Card) -> bool:
    return (
        a.total_sum == b.total_sum
        and a.linked_user_id == b.linked_user_id
    )


def _users_equal(a: User, b: User) -> bool:
    return (
        a.first_name == b.first_name
        and a.last_name == b.last_name
        and a.card_ids == b.card_ids
    )
