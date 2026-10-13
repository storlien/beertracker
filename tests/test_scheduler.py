"""Unit tests for the sync scheduler -- verifies the sync pipeline end-to-end
with mocked repositories and synthetic Zettle pages.

Stats note: ``info/stats`` is updated inside each card transaction (see
``test_repositories.py``), so the scheduler itself must NOT call
``increment_stats``. These tests assert that invariant.
"""

from unittest.mock import MagicMock, patch

from google.api_core.exceptions import ResourceExhausted
import pytest

from beertracker.scheduler import SyncWorker


@pytest.fixture
def mock_worker():
    """Build a SyncWorker with all Firebase/HTTP dependencies mocked.

    Repository methods are replaced with MagicMocks so no Firestore writes
    occur. Stats seeding is marked as done so the sync gate opens.
    """
    with (
        patch("beertracker.scheduler.TokenManager"),
        patch("beertracker.scheduler.get_firestore_client"),
    ):
        worker = SyncWorker()

        # Mock all repository methods so nothing hits Firestore
        worker._card_repo.batch_increment_sums = MagicMock(
            return_value=([], [], 0.0, 0)
        )
        worker._daily_usage_repo.merge_daily_card_totals = MagicMock(return_value=True)
        worker._stats_repo.increment_stats = MagicMock()
        worker._stats_repo.exists = MagicMock(return_value=True)
        worker._stats_repo.update_stats = MagicMock()
        worker._state_repo.get_last_purchase_hash = MagicMock(return_value=None)
        worker._state_repo.update_sync_state = MagicMock()

        # Pretend info/stats is already seeded so the sync gate opens
        worker._stats_seeded = True

        yield worker


def _make_page(purchases, last_purchase_hash="next-hash"):
    """Build a synthetic Zettle API page response."""
    return {"purchases": purchases, "lastPurchaseHash": last_purchase_hash}


def _make_card_txn(amount_ore, masked_pan, hash_, timestamp):
    """Build a raw Zettle purchase dict for an IZETTLE_CARD payment."""
    return {
        "amount": amount_ore,
        "payments": [
            {
                "type": "IZETTLE_CARD",
                "attributes": {"maskedPan": masked_pan},
            }
        ],
        "hash": hash_,
        "timestamp": timestamp,
    }


class TestSyncPurchases:
    """Tests for SyncWorker._job_sync_purchases with mocked Zettle pages."""

    def test_single_page_new_card(self, mock_worker):
        """One page with a single new card transaction; stats updated in-txn."""
        page = _make_page(
            [
                _make_card_txn(
                    amount_ore=5500,
                    masked_pan="123456******7890",
                    hash_="txn-1",
                    timestamp="2024-06-01T12:00:00Z",
                )
            ],
            last_purchase_hash="hash-1",
        )

        # First page then empty page ends the loop
        mock_worker._zettle.get_purchases = MagicMock(
            side_effect=[page, {"purchases": []}]
        )

        # Repo says: 1 card applied, 55 kr spent, 1 new card
        mock_worker._card_repo.batch_increment_sums = MagicMock(
            return_value=(["1234567890"], [], 55.0, 1)
        )

        mock_worker._job_sync_purchases()

        # Card totals aggregated to 55.00 kr (5500 ore / 100)
        mock_worker._card_repo.batch_increment_sums.assert_called_once_with(
            {"1234567890": 55.0},
            page_last_purchase_hash="",
        )

        # Stats are written inside the card transaction, NOT by the scheduler
        mock_worker._stats_repo.increment_stats.assert_not_called()

        # State updated twice: per-page hash commit + final heartbeat
        assert mock_worker._state_repo.update_sync_state.call_count == 2

    def test_multiple_transactions_same_card(self, mock_worker):
        """Two transactions on the same card in one page; aggregated."""
        page = _make_page(
            [
                _make_card_txn(
                    1500, "111111******2222", "txn-a", "2024-06-01T12:00:00Z"
                ),
                _make_card_txn(
                    2500, "111111******2222", "txn-b", "2024-06-01T12:01:00Z"
                ),
            ],
            last_purchase_hash="hash-1",
        )

        mock_worker._zettle.get_purchases = MagicMock(
            side_effect=[page, {"purchases": []}]
        )
        mock_worker._card_repo.batch_increment_sums = MagicMock(
            return_value=(["1111112222"], [], 40.0, 1)
        )

        mock_worker._job_sync_purchases()

        # 15 kr + 25 kr = 40 kr for the same card
        mock_worker._card_repo.batch_increment_sums.assert_called_once_with(
            {"1111112222": 40.0},
            page_last_purchase_hash="",
        )
        mock_worker._stats_repo.increment_stats.assert_not_called()

    def test_multiple_pages_multiple_cards(self, mock_worker):
        """Two pages, each with a different new card; state committed per page."""
        page1 = _make_page(
            [
                _make_card_txn(
                    3000, "111111******2222", "txn-1", "2024-06-01T12:00:00Z"
                )
            ],
            last_purchase_hash="hash-1",
        )
        page2 = _make_page(
            [
                _make_card_txn(
                    5000, "333333******4444", "txn-2", "2024-06-01T12:01:00Z"
                )
            ],
            last_purchase_hash="hash-2",
        )

        mock_worker._zettle.get_purchases = MagicMock(
            side_effect=[page1, page2, {"purchases": []}]
        )
        mock_worker._card_repo.batch_increment_sums = MagicMock(
            side_effect=[
                (["1111112222"], [], 30.0, 1),
                (["3333334444"], [], 50.0, 1),
            ]
        )

        mock_worker._job_sync_purchases()

        # Scheduler must not double-count stats on top of the transactions
        mock_worker._stats_repo.increment_stats.assert_not_called()

        # State updated 3 times: 2 per-page + 1 final heartbeat
        assert mock_worker._state_repo.update_sync_state.call_count == 3

    def test_existing_card_delta_only(self, mock_worker):
        """Existing card (not new) with 20 kr; stats handled in-txn."""
        page = _make_page(
            [
                _make_card_txn(
                    2000, "111111******2222", "txn-1", "2024-06-01T12:00:00Z"
                )
            ],
            last_purchase_hash="hash-1",
        )

        mock_worker._zettle.get_purchases = MagicMock(
            side_effect=[page, {"purchases": []}]
        )
        mock_worker._card_repo.batch_increment_sums = MagicMock(
            return_value=(["1111112222"], [], 20.0, 0)
        )

        mock_worker._job_sync_purchases()

        mock_worker._stats_repo.increment_stats.assert_not_called()

    def test_mixed_new_and_existing_cards(self, mock_worker):
        """Same page has one new card and one existing card."""
        page = _make_page(
            [
                _make_card_txn(
                    1000, "111111******2222", "txn-1", "2024-06-01T12:00:00Z"
                ),
                _make_card_txn(
                    2000, "333333******4444", "txn-2", "2024-06-01T12:01:00Z"
                ),
            ],
            last_purchase_hash="hash-1",
        )

        mock_worker._zettle.get_purchases = MagicMock(
            side_effect=[page, {"purchases": []}]
        )
        mock_worker._card_repo.batch_increment_sums = MagicMock(
            return_value=(["1111112222"], ["3333334444"], 30.0, 1)
        )

        mock_worker._job_sync_purchases()

        mock_worker._stats_repo.increment_stats.assert_not_called()

    def test_empty_page_no_stats_update(self, mock_worker):
        """No purchases at all; no card or stats calls, heartbeat only."""
        mock_worker._zettle.get_purchases = MagicMock(
            side_effect=[{"purchases": []}]
        )

        mock_worker._job_sync_purchases()

        mock_worker._stats_repo.increment_stats.assert_not_called()
        # Only the final heartbeat state update
        mock_worker._state_repo.update_sync_state.assert_called_once()

    def test_vipps_only_no_card_processing(self, mock_worker):
        """Page with only VIPPS payments; no card repo calls, no stats update."""
        page = {
            "purchases": [
                {
                    "amount": 1200,
                    "payments": [
                        {"type": "VIPPS", "attributes": {"maskedPan": ""}}
                    ],
                    "hash": "vipps-1",
                }
            ],
            "lastPurchaseHash": "hash-1",
        }

        mock_worker._zettle.get_purchases = MagicMock(
            side_effect=[page, {"purchases": []}]
        )

        mock_worker._job_sync_purchases()

        mock_worker._card_repo.batch_increment_sums.assert_not_called()
        mock_worker._stats_repo.increment_stats.assert_not_called()

    def test_zero_amount_new_card(self, mock_worker):
        """New card with 0 kr amount still flows through the batch."""
        page = _make_page(
            [
                _make_card_txn(
                    0, "111111******2222", "txn-1", "2024-06-01T12:00:00Z"
                )
            ],
            last_purchase_hash="hash-1",
        )

        mock_worker._zettle.get_purchases = MagicMock(
            side_effect=[page, {"purchases": []}]
        )
        mock_worker._card_repo.batch_increment_sums = MagicMock(
            return_value=(["1111112222"], [], 0.0, 1)
        )

        mock_worker._job_sync_purchases()

        # The in-transaction write still counts the new card (totalCards +1)
        mock_worker._card_repo.batch_increment_sums.assert_called_once_with(
            {"1111112222": 0.0},
            page_last_purchase_hash="",
        )
        mock_worker._stats_repo.increment_stats.assert_not_called()

    def test_already_running_then_new_pages_arrive(self, mock_worker):
        """Service synced earlier, now new transactions arrive on next poll."""
        # -- First sync: no new data (service is idle) --
        mock_worker._state_repo.get_last_purchase_hash = MagicMock(
            return_value="hash-existing"
        )
        mock_worker._zettle.get_purchases = MagicMock(
            side_effect=[{"purchases": []}]
        )

        mock_worker._job_sync_purchases()

        # No card or stats activity on idle
        mock_worker._stats_repo.increment_stats.assert_not_called()
        # Only heartbeat state update
        assert mock_worker._state_repo.update_sync_state.call_count == 1

        # Reset mocks to simulate a new sync cycle
        mock_worker._stats_repo.increment_stats.reset_mock()
        mock_worker._state_repo.update_sync_state.reset_mock()

        # -- Second sync: new transactions arrive --
        new_page = _make_page(
            [
                _make_card_txn(
                    3000, "555555******6666", "txn-3", "2024-06-01T14:30:00Z"
                ),
                _make_card_txn(
                    7000, "555555******6666", "txn-4", "2024-06-01T14:31:00Z"
                ),
            ],
            last_purchase_hash="hash-new",
        )

        mock_worker._zettle.get_purchases = MagicMock(
            side_effect=[new_page, {"purchases": []}]
        )
        mock_worker._card_repo.batch_increment_sums = MagicMock(
            return_value=(["5555556666"], [], 100.0, 1)
        )

        mock_worker._job_sync_purchases()

        # Aggregated: 30 kr + 70 kr = 100 kr for one new card
        mock_worker._card_repo.batch_increment_sums.assert_called_once_with(
            {"5555556666": 100.0},
            page_last_purchase_hash="hash-existing",
        )
        mock_worker._stats_repo.increment_stats.assert_not_called()
        # Per-page hash commit + final heartbeat = 2 state updates
        assert mock_worker._state_repo.update_sync_state.call_count == 2


class TestStatsSeeding:
    """Tests for the info/stats bootstrap gate in the sync job."""

    def test_sync_deferred_until_stats_seeded(self, mock_worker):
        """If seeding fails (quota), the sync must not touch anything."""
        mock_worker._stats_seeded = False
        mock_worker._stats_repo.exists = MagicMock(return_value=False)
        mock_worker._stats_repo.update_stats = MagicMock(
            side_effect=ResourceExhausted("429 Quota exceeded.")
        )
        mock_worker._zettle.get_purchases = MagicMock()

        mock_worker._job_sync_purchases()

        # Sync never reached the Zettle API, cards, or state
        mock_worker._zettle.get_purchases.assert_not_called()
        mock_worker._card_repo.batch_increment_sums.assert_not_called()
        mock_worker._state_repo.update_sync_state.assert_not_called()
        assert mock_worker._stats_seeded is False

    def test_sync_seeds_stats_inline_then_proceeds(self, mock_worker):
        """Seeding succeeds during the sync job, which then continues."""
        mock_worker._stats_seeded = False
        mock_worker._stats_repo.exists = MagicMock(return_value=False)
        mock_worker._stats_repo.update_stats = MagicMock()

        page = _make_page(
            [
                _make_card_txn(
                    5500, "123456******7890", "txn-1", "2024-06-01T12:00:00Z"
                )
            ],
            last_purchase_hash="hash-1",
        )
        mock_worker._zettle.get_purchases = MagicMock(
            side_effect=[page, {"purchases": []}]
        )
        mock_worker._card_repo.batch_increment_sums = MagicMock(
            return_value=(["1234567890"], [], 55.0, 1)
        )

        mock_worker._job_sync_purchases()

        mock_worker._stats_repo.update_stats.assert_called_once()
        assert mock_worker._stats_seeded is True
        mock_worker._card_repo.batch_increment_sums.assert_called_once()

    def test_seed_skipped_when_doc_exists(self, mock_worker):
        """Existing info/stats doc means no recompute is needed."""
        mock_worker._stats_seeded = False
        mock_worker._stats_repo.exists = MagicMock(return_value=True)
        mock_worker._stats_repo.update_stats = MagicMock()

        mock_worker._try_seed_stats()

        mock_worker._stats_repo.update_stats.assert_not_called()
        assert mock_worker._stats_seeded is True

    def test_seed_runs_recompute_when_doc_missing(self, mock_worker):
        """Missing info/stats doc triggers a full recompute."""
        mock_worker._stats_seeded = False
        mock_worker._stats_repo.exists = MagicMock(return_value=False)
        mock_worker._stats_repo.update_stats = MagicMock()

        mock_worker._try_seed_stats()

        mock_worker._stats_repo.update_stats.assert_called_once()
        assert mock_worker._stats_seeded is True

    def test_seed_retries_on_quota_error(self, mock_worker):
        """Quota error defers seeding; flag stays False for retry."""
        mock_worker._stats_seeded = False
        mock_worker._stats_repo.exists = MagicMock(
            side_effect=ResourceExhausted("429 Quota exceeded.")
        )
        mock_worker._stats_repo.update_stats = MagicMock()

        mock_worker._try_seed_stats()

        mock_worker._stats_repo.update_stats.assert_not_called()
        assert mock_worker._stats_seeded is False

    def test_seed_is_idempotent(self, mock_worker):
        """Second call is a no-op once seeded."""
        mock_worker._stats_seeded = True
        mock_worker._stats_repo.exists = MagicMock()
        mock_worker._stats_repo.update_stats = MagicMock()

        mock_worker._try_seed_stats()

        mock_worker._stats_repo.exists.assert_not_called()
        mock_worker._stats_repo.update_stats.assert_not_called()
