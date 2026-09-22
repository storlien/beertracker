"""APScheduler-based background worker orchestration."""

import logging
import signal
import threading
import time
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from beertracker.api.client import ZettleClient
from beertracker.api.token_manager import TokenManager
from beertracker.config import get_settings
from beertracker.core.mappers import aggregate_by_card, parse_purchases
from beertracker.firebase.client import get_firestore_client
from beertracker.firebase.repositories import CardRepository, StateRepository

logger = logging.getLogger(__name__)


class SyncWorker:
    """Background worker that periodically syncs Zettle purchases to Firestore."""

    def __init__(self) -> None:
        self._token_manager = TokenManager()
        self._zettle = ZettleClient(self._token_manager)
        self._db = get_firestore_client()
        self._card_repo = CardRepository(self._db)
        self._state_repo = StateRepository(self._db)
        self._scheduler = BackgroundScheduler()
        self._shutdown_event = threading.Event()
        self._sync_lock = threading.Lock()

    def _job_sync_purchases(self) -> None:
        """Fetch newer purchases incrementally and commit each page to Firestore.

        After every Zettle API page is processed, the corresponding card sums
        and the page's ``lastPurchaseHash`` are written to Firestore immediately.
        This makes catch-up resilient to hard container kills: on restart,
        the worker resumes from the last successfully committed hash.

        This job is protected by a lock so overlapping invocations (initial
        sync + interval) are skipped rather than running concurrently.
        """
        if not self._sync_lock.acquire(blocking=False):
            logger.info("Sync already in progress — skipping overlapping job")
            return

        try:
            current_hash = self._state_repo.get_last_purchase_hash()
            logger.info("Starting incremental sync. lastPurchaseHash=%s", current_hash)

            total_purchases = 0
            total_cards_updated: set[str] = set()
            page_count = 0

            while True:
                page_count += 1
                data = self._zettle.get_purchases(
                    last_purchase_hash=current_hash
                )
                raw_purchases = data.get("purchases", [])

                if not raw_purchases:
                    logger.info("No more pages after %d page(s). Sync complete.", page_count - 1)
                    break

                # Parse and filter to IZETTLE_CARD only for this page
                purchases = parse_purchases({"purchases": raw_purchases})
                if purchases:
                    card_totals = aggregate_by_card(purchases)
                    self._card_repo.batch_increment_sums(
                        card_totals,
                        page_last_purchase_hash=current_hash or "",
                    )
                    total_cards_updated.update(card_totals.keys())
                    total_purchases += len(purchases)

                # Persist lastPurchaseHash for THIS page immediately.
                # If the container is killed now, the next restart resumes here
                # and cards skip already-processed pages via per-card hash tracking.
                new_hash = data.get("lastPurchaseHash")
                if new_hash and new_hash != current_hash:
                    self._state_repo.update_sync_state(new_hash)
                    current_hash = new_hash
                    logger.info(
                        "Page %d committed: %d raw txn, %d card txn, hash=%s",
                        page_count,
                        len(raw_purchases),
                        len(purchases),
                        new_hash,
                    )
                else:
                    logger.warning(
                        "Page %d has no new lastPurchaseHash; stopping to avoid loop",
                        page_count,
                    )
                    break

            # Update lastSyncAt even when idle to show the service is alive.
            # Without this, healthcheck fails after 10 minutes of no bar sales.
            self._state_repo.update_sync_state(
                current_hash or "", total_purchases_synced=0
            )

            logger.info(
                "Sync complete: %d purchases, %d unique cards updated across %d page(s)",
                total_purchases,
                len(total_cards_updated),
                page_count - 1,
            )

        except Exception:
            logger.exception("Purchase sync job failed")
        finally:
            self._sync_lock.release()

    def _job_refresh_token(self) -> None:
        """Refresh the Zettle OAuth token."""
        try:
            self._token_manager.refresh()
            logger.info("Token refreshed successfully")
        except Exception:
            logger.exception("Token refresh job failed")

    def start(self) -> None:
        """Configure and start the background scheduler."""
        settings = get_settings()
        logger.info("Starting SyncWorker...")

        # Register signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._signal_handler)

        # Schedule jobs
        self._scheduler.add_job(
            self._job_sync_purchases,
            "interval",
            seconds=settings.sync_interval_seconds,
            id="sync_purchases",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._job_refresh_token,
            "interval",
            minutes=settings.token_refresh_minutes,
            id="refresh_token",
            replace_existing=True,
        )

        # Run sync immediately on startup
        self._scheduler.add_job(
            self._job_sync_purchases,
            id="initial_sync",
            replace_existing=True,
        )

        self._scheduler.start()
        logger.info(
            "Scheduler started (sync every %ds, token refresh every %dm)",
            settings.sync_interval_seconds,
            settings.token_refresh_minutes,
        )

        # Block until shutdown signal
        try:
            while not self._shutdown_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received")
        finally:
            self.shutdown()

    def shutdown(self, *_args: Any) -> None:
        """Gracefully stop the scheduler and close resources."""
        if self._shutdown_event.is_set():
            return  # Already shutting down

        logger.info("Shutting down SyncWorker...")
        self._shutdown_event.set()
        self._scheduler.shutdown(wait=True)
        self._zettle.close()
        logger.info("SyncWorker shutdown complete")

    def _signal_handler(self, signum: int, _frame: Any) -> None:
        """Handle OS signals for graceful shutdown.

        Signals can arrive while logging is flushing stdout, causing
        reentrancy errors. We use a simple flag to avoid recursion.
        """
        if self._shutdown_event.is_set():
            return
        self._shutdown_event.set()
        self.shutdown()
