"""Leaderboard repository for writing pre-computed leaderboard data."""

import logging
from datetime import datetime, timezone
from typing import Any

from beertracker.core.models import LeaderboardEntry

logger = logging.getLogger(__name__)

COLLECTION_LEADERBOARD = "leaderboard"
DOC_TOP100 = "top100"
DOC_DAILY = "daily"


class LeaderboardRepository:
    """Repository for the pre-computed leaderboard documents."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._doc_top100 = db.collection(COLLECTION_LEADERBOARD).document(DOC_TOP100)
        self._doc_daily = db.collection(COLLECTION_LEADERBOARD).document(DOC_DAILY)

    def write_top100(self, entries: list[LeaderboardEntry]) -> None:
        """Overwrite the leaderboard/top100 document.

        Args:
            entries: Ordered list of LeaderboardEntry, highest sum first.
        """
        payload = {
            "entries": [
                {
                    "id": e.entry_id,
                    "name": e.name,
                    "isUser": e.is_user,
                    "sum": e.total_sum,
                    "cards": e.card_ids,
                }
                for e in entries
            ],
            "updatedAt": datetime.now(timezone.utc),
        }
        self._doc_top100.set(payload)
        logger.info("Leaderboard top100 written with %d entries", len(entries))

    def get_top100(self) -> dict[str, Any] | None:
        """Return the raw top100 document, or None if not set."""
        doc = self._doc_top100.get()
        return doc.to_dict() if doc.exists else None

    def write_daily(
        self,
        entries: list[LeaderboardEntry],
        bar_day: str,
    ) -> None:
        """Overwrite the leaderboard/daily document for a bar day.

        Args:
            entries: Ordered list of LeaderboardEntry for the active bar day.
            bar_day: The bar day (YYYY-MM-DD) these data belong to.
        """
        payload = {
            "barDay": bar_day,
            "entries": [
                {
                    "id": e.entry_id,
                    "name": e.name,
                    "isUser": e.is_user,
                    "sum": e.total_sum,
                    "cards": e.card_ids,
                }
                for e in entries
            ],
            "updatedAt": datetime.now(timezone.utc),
        }
        self._doc_daily.set(payload)
        logger.info("Leaderboard daily written for %s with %d entries", bar_day, len(entries))

    def get_daily(self) -> dict[str, Any] | None:
        """Return the raw daily leaderboard document, or None if not set."""
        doc = self._doc_daily.get()
        return doc.to_dict() if doc.exists else None
