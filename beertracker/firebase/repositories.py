"""Firestore repository layer for cards and state."""

import logging
from datetime import datetime, timezone
from typing import Any

from firebase_admin import firestore as _firestore_mod

from beertracker.core.models import Card

logger = logging.getLogger(__name__)

COLLECTION_CARDS = "cards"
COLLECTION_INFO = "info"
COLLECTION_DAILY_USAGE = "dailyUsage"
DOC_STATE = "state"
FIELD_LAST_PURCHASE_HASH = "lastPurchaseHash"
FIELD_LAST_SYNC_AT = "lastSyncAt"
FIELD_TOTAL_PAGES = "totalPagesSynced"


def _increment_card_transaction(transaction, doc_ref, amount: float, page_hash: str) -> bool:
    """Transaction body: read card, check page hash, increment if new.

    Defined at module level so the ``@transactional`` decorator works.
    """
    snapshot = doc_ref.get(transaction=transaction)

    if snapshot.exists:
        stored_hash = snapshot.to_dict().get(FIELD_LAST_PURCHASE_HASH)
        if stored_hash == page_hash:
            logger.debug(
                "Skipping card %s — already processed page %s",
                doc_ref.id,
                page_hash,
            )
            return False
        new_sum = (snapshot.to_dict().get("sum") or 0.0) + amount
    else:
        new_sum = amount

    now = datetime.now(timezone.utc)
    transaction.set(
        doc_ref,
        {
            "sum": round(new_sum, 2),
            FIELD_LAST_PURCHASE_HASH: page_hash,
            "updatedAt": now,
            "createdAt": now if not snapshot.exists else snapshot.to_dict().get("createdAt", now),
        },
        merge=True,
    )
    logger.debug(
        "Incremented card %s by +%.2f (page_hash=%s, new_sum=%.2f)",
        doc_ref.id,
        amount,
        page_hash,
        new_sum,
    )
    return True


# Make it a proper Firestore transaction wrapper
_increment_card_transaction = _firestore_mod.transactional(_increment_card_transaction)


class CardRepository:
    """Repository for card spending data in Firestore.

    Updates are transaction-safe: each card tracks the ``lastPurchaseHash``
    that last incremented it. If a page is re-processed, the ``lastPurchaseHash``
    check skips already-processed cards to prevent double-counting.
    """

    def __init__(self, db) -> None:
        self._db = db
        self._col = db.collection(COLLECTION_CARDS)

    def get(self, card_number: str) -> Card | None:
        """Fetch a single card by its 10-digit card number."""
        doc = self._col.document(card_number).get()
        if not doc.exists:
            return None
        data = doc.to_dict()
        return Card(
            card_number=card_number,
            total_sum=data.get("sum", 0.0),
            linked_user_id=data.get("linkedUserId"),
            created_at=_parse_firestore_timestamp(data.get("createdAt")),
            updated_at=_parse_firestore_timestamp(data.get("updatedAt")),
        )

    def increment_sum(
        self,
        card_number: str,
        amount: float,
        page_last_purchase_hash: str,
    ) -> bool:
        """Atomically increment a card's sum, idempotent by page hash.

        Uses a Firestore transaction to read the card's current
        ``lastPurchaseHash``, skip if already processed, otherwise add
        the amount and persist the new hash.

        Args:
            card_number: 10-digit card identifier.
            amount: Amount in kroner to add.
            page_last_purchase_hash: The ``lastPurchaseHash`` of the
                Zettle API page that produced this increment.

        Returns:
            ``True`` if the increment was applied; ``False`` if skipped
            because this page was already processed for this card.
        """
        transaction = self._db.transaction()
        doc_ref = self._col.document(card_number)
        return _increment_card_transaction(transaction, doc_ref, amount, page_last_purchase_hash)

    def batch_increment_sums(
        self,
        card_totals: dict[str, float],
        page_last_purchase_hash: str,
    ) -> tuple[list[str], list[str]]:
        """Increment sums for multiple cards, tracking idempotency per page.

        Args:
            card_totals: Mapping from card_number -> amount to increment.
            page_last_purchase_hash: The Zettle page hash for this batch.

        Returns:
            Tuple of (applied_card_ids, skipped_card_ids).
        """
        applied: list[str] = []
        skipped: list[str] = []

        for card_number, amount in card_totals.items():
            if self.increment_sum(card_number, amount, page_last_purchase_hash):
                applied.append(card_number)
            else:
                skipped.append(card_number)

        if applied:
            logger.info(
                "Applied %d cards, skipped %d cards for page %s",
                len(applied),
                len(skipped),
                page_last_purchase_hash,
            )
        return applied, skipped

    def upsert_sum(self, card_number: str, total: float) -> None:
        """Set or overwrite the sum for a card (used by CLI seeding).

        Args:
            card_number: 10-digit card identifier.
            total: Absolute total amount in kroner.
        """
        now = datetime.now(timezone.utc)
        self._col.document(card_number).set(
            {"sum": round(total, 2), "updatedAt": now},
            merge=True,
        )
        logger.info("Upserted card %s sum=%.2f", card_number, total)

    def get_all(self):
        """Iterate over all cards (generator)."""
        for doc in self._col.stream():
            data = doc.to_dict()
            if data is None:
                continue
            yield Card(
                card_number=doc.id,
                total_sum=data.get("sum", 0.0),
                linked_user_id=data.get("linkedUserId"),
                created_at=_parse_firestore_timestamp(data.get("createdAt")),
                updated_at=_parse_firestore_timestamp(data.get("updatedAt")),
            )


class DailyUsageRepository:
    """Repository for per-bar-day card spending in Firestore.

    Each document at ``dailyUsage/{bar-day}`` tracks how much each card
    spent on that bar day. Page hashes are stored to make repeated processing
    idempotent.
    """

    def __init__(self, db) -> None:
        self._db = db
        self._col = db.collection(COLLECTION_DAILY_USAGE)

    def merge_daily_card_totals(
        self,
        bar_day: str,
        card_totals: dict[str, float],
        page_last_purchase_hash: str,
    ) -> bool:
        """Merge card totals into a bar-day doc, idempotent by page hash.

        Uses an atomic transaction to read the existing document, check
        the processed page hashes, and skip if already processed.

        Args:
            bar_day: Bar-day string (``YYYY-MM-DD``).
            card_totals: Mapping from card_number -> amount for this day.
            page_last_purchase_hash: The Zettle page hash for this batch.

        Returns:
            ``True`` if merged, ``False`` if the page was already recorded.
        """
        doc_ref = self._col.document(bar_day)

        @_firestore_mod.transactional
        def _txn(transaction, doc_ref, card_totals, page_last_purchase_hash):
            snapshot = doc_ref.get(transaction=transaction)
            processed = set()
            if snapshot.exists:
                raw = snapshot.to_dict().get("processedPageHashes", [])
                if isinstance(raw, list):
                    processed = set(raw)

            if page_last_purchase_hash in processed:
                return False

            # Build merge payload with nested cards map
            existing_cards: dict[str, float] = {}
            if snapshot.exists:
                raw_cards = snapshot.to_dict().get("cards")
                if isinstance(raw_cards, dict):
                    existing_cards = {
                        str(k): float(v) for k, v in raw_cards.items()
                    }

            update_cards: dict[str, float] = {}
            for card, amount in card_totals.items():
                current = existing_cards.get(card, 0.0)
                update_cards[card] = round(current + amount, 2)

            update: dict[str, Any] = {
                "processedPageHashes": _firestore_mod.ArrayUnion([page_last_purchase_hash]),
                "cards": update_cards,
            }

            transaction.set(doc_ref, update, merge=True)
            return True

        merged = _txn(self._db.transaction(), doc_ref, card_totals, page_last_purchase_hash)
        if merged:
            logger.info(
                "Merged %d cards into dailyUsage/%s (hash=%s)",
                len(card_totals),
                bar_day,
                page_last_purchase_hash,
            )
        return merged

    def get(self, bar_day: str) -> dict[str, Any] | None:
        """Return the raw dailyUsage document for a bar day, or None."""
        doc = self._col.document(bar_day).get()
        return doc.to_dict() if doc.exists else None

    def get_bar_days(self) -> list[str]:
        """Return all stored bar-day document IDs, sorted ascending."""
        return sorted(d.id for d in self._col.stream())


class StateRepository:
    """Repository for global sync state in Firestore."""

    def __init__(self, db) -> None:
        self._db = db
        self._doc_ref = db.collection(COLLECTION_INFO).document(DOC_STATE)

    def get_last_purchase_hash(self) -> str | None:
        """Return the stored lastPurchaseHash, or None if not set."""
        doc = self._doc_ref.get()
        if doc.exists:
            return doc.to_dict().get(FIELD_LAST_PURCHASE_HASH)
        return None

    def set_last_purchase_hash(self, hash_value: str) -> None:
        """Update the lastPurchaseHash in Firestore."""
        self._doc_ref.update({FIELD_LAST_PURCHASE_HASH: hash_value})
        logger.info("Updated lastPurchaseHash to %s", hash_value)

    def get_last_sync_at(self) -> datetime | None:
        """Return the timestamp of the last successful sync."""
        doc = self._doc_ref.get()
        if doc.exists:
            return _parse_firestore_timestamp(doc.to_dict().get(FIELD_LAST_SYNC_AT))
        return None

    def update_sync_state(
        self,
        last_purchase_hash: str,
        pages_synced: int | None = None,
    ) -> None:
        """Update all sync state fields atomically."""
        update_data: dict[str, Any] = {
            FIELD_LAST_PURCHASE_HASH: last_purchase_hash,
            FIELD_LAST_SYNC_AT: datetime.now(timezone.utc),
            FIELD_TOTAL_PAGES: _firestore_mod.Increment(1),
        }
        if pages_synced is not None:
            update_data[FIELD_TOTAL_PAGES] = _firestore_mod.Increment(pages_synced)
        self._doc_ref.update(update_data)
        logger.info("Sync state updated: hash=%s", last_purchase_hash)

    def get_stats(self) -> dict[str, Any]:
        """Return the current state document as a dict."""
        doc = self._doc_ref.get()
        return doc.to_dict() or {}


def _parse_firestore_timestamp(value: Any) -> datetime | None:
    """Convert a Firestore Timestamp or datetime to Python datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    # Firestore returns google.api_core.datetime_helpers.DatetimeWithNanoseconds
    try:
        return value.astimezone(timezone.utc)
    except Exception:
        return None
