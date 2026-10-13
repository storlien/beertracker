"""Unit tests for repository transaction logic.

Verifies that card increments and info/stats updates happen in the SAME
transaction (atomicity) and that skipped pages touch neither.
"""

from unittest.mock import MagicMock

from beertracker.firebase.repositories import (
    CardRepository,
    StatsRepository,
    _increment_card_transaction_body,
)


def _snapshot(exists, data=None):
    """Build a fake Firestore document snapshot."""
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = data or {}
    return snap


class TestIncrementCardTransactionBody:
    """Tests for the raw transaction body (no Firestore involved)."""

    def test_new_card_writes_card_and_stats_atomically(self):
        txn, doc_ref, stats_ref = MagicMock(), MagicMock(), MagicMock()
        doc_ref.get.return_value = _snapshot(False)

        applied, is_new = _increment_card_transaction_body(
            txn, doc_ref, stats_ref, 55.0, "h1"
        )

        assert (applied, is_new) == (True, True)
        # Both the card doc and the stats doc are written in one transaction
        assert txn.set.call_count == 2

        card_ref_arg, card_payload = txn.set.call_args_list[0].args
        assert card_ref_arg is doc_ref
        assert card_payload["sum"] == 55.0
        assert card_payload["lastPurchaseHash"] == "h1"
        assert txn.set.call_args_list[0].kwargs["merge"] is True

        stats_ref_arg, stats_payload = txn.set.call_args_list[1].args
        assert stats_ref_arg is stats_ref
        assert stats_payload["totalSpent"]._value == 55.0
        assert stats_payload["totalCards"]._value == 1
        assert stats_payload["updatedAt"] is not None
        assert txn.set.call_args_list[1].kwargs["merge"] is True

    def test_existing_card_increments_spent_but_not_count(self):
        txn, doc_ref, stats_ref = MagicMock(), MagicMock(), MagicMock()
        doc_ref.get.return_value = _snapshot(
            True, {"sum": 20.0, "lastPurchaseHash": "old"}
        )

        applied, is_new = _increment_card_transaction_body(
            txn, doc_ref, stats_ref, 10.0, "h1"
        )

        assert (applied, is_new) == (True, False)
        card_payload = txn.set.call_args_list[0].args[1]
        assert card_payload["sum"] == 30.0
        stats_payload = txn.set.call_args_list[1].args[1]
        assert stats_payload["totalSpent"]._value == 10.0
        assert stats_payload["totalCards"]._value == 0

    def test_skipped_page_touches_neither_card_nor_stats(self):
        """Already-processed page hash means no writes at all (idempotent)."""
        txn, doc_ref, stats_ref = MagicMock(), MagicMock(), MagicMock()
        doc_ref.get.return_value = _snapshot(
            True, {"sum": 20.0, "lastPurchaseHash": "h1"}
        )

        applied, is_new = _increment_card_transaction_body(
            txn, doc_ref, stats_ref, 10.0, "h1"
        )

        assert (applied, is_new) == (False, False)
        txn.set.assert_not_called()

    def test_existing_card_preserves_created_at(self):
        """createdAt is kept from the existing document."""
        txn, doc_ref, stats_ref = MagicMock(), MagicMock(), MagicMock()
        doc_ref.get.return_value = _snapshot(
            True, {"sum": 5.0, "lastPurchaseHash": "old", "createdAt": "2020-01-01"}
        )

        _increment_card_transaction_body(txn, doc_ref, stats_ref, 5.0, "h1")

        card_payload = txn.set.call_args_list[0].args[1]
        assert card_payload["createdAt"] == "2020-01-01"


class TestUpsertSumReconcilesStats:
    """P2: manual upsert must recompute info/stats (out-of-band write)."""

    def test_upsert_triggers_full_stats_recompute(self):
        db = MagicMock()
        collections = {}

        def _col(name):
            if name not in collections:
                collections[name] = MagicMock()
            return collections[name]

        db.collection = MagicMock(side_effect=_col)
        collections["cards"] = _col("cards")  # warm the side_effect cache
        collections["cards"].stream.return_value = iter([])

        repo = CardRepository(db)
        repo.upsert_sum("1234567890", 42.0)

        # The card document was written
        collections["cards"].document.assert_any_call("1234567890")
        card_doc = collections["cards"].document.return_value
        card_set_calls = [
            c for c in card_doc.set.call_args_list
        ]
        assert any(c.args[0]["sum"] == 42.0 for c in card_set_calls)

        # The stats document was recomputed (empty scan: zeros)
        collections["info"].document.assert_called_once_with("stats")
        stats_doc = collections["info"].document.return_value
        stats_doc.set.assert_called_once()
        payload = stats_doc.set.call_args.args[0]
        assert payload["totalSpent"] == 0.0
        assert payload["totalCards"] == 0
        assert stats_doc.set.call_args.kwargs["merge"] is True


class TestStatsRepositoryExists:
    """P0: exists() powers the scheduler's bootstrap gate."""

    def test_exists_true_when_doc_present(self):
        db = MagicMock()
        db.collection.return_value.document.return_value.get.return_value = _snapshot(
            True
        )
        assert StatsRepository(db).exists() is True

    def test_exists_false_when_doc_missing(self):
        db = MagicMock()
        db.collection.return_value.document.return_value.get.return_value = _snapshot(
            False
        )
        assert StatsRepository(db).exists() is False
