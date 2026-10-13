"""APScheduler-based background worker orchestration."""

import logging
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from google.api_core.exceptions import ResourceExhausted

from beertracker.api.client import ZettleClient
from beertracker.api.token_manager import TokenManager
from beertracker.config import get_settings
from beertracker.core.mappers import aggregate_by_card, aggregate_by_card_and_day, parse_purchases
from beertracker.firebase.client import get_firestore_client
from beertracker.firebase.repositories import CardRepository, DailyUsageRepository, StateRepository, StatsRepository
from beertracker.services.daily_leaderboard_cache import DailyLeaderboardCache
from beertracker.services.leaderboard_cache import LeaderboardCache
from beertracker.services.leaderboard_repo import LeaderboardRepository

logger = logging.getLogger(__name__)

SYNC_JOB_ID = "sync_purchases"
ADAPTIVE_CHECK_JOB_ID = "adaptive_check"


class SyncWorker:
    """Background worker that periodically syncs Zettle purchases to Firestore.

    Uses adaptive sync rate: active (fast) when bar is busy, hibernating (slow)
    when idle. Both the lifetime and daily leaderboards are kept in memory and
    updated via Firestore listeners, so the frontend only reads pre-computed docs.
    """

    def __init__(self) -> None:
        self._token_manager = TokenManager()
        self._zettle = ZettleClient(self._token_manager)
        self._db = get_firestore_client()
        self._card_repo = CardRepository(self._db)
        self._daily_usage_repo = DailyUsageRepository(self._db)
        self._state_repo = StateRepository(self._db)
        self._stats_repo = StatsRepository(self._db)
        self._leaderboard_repo = LeaderboardRepository(self._db)
        self._cache = LeaderboardCache(self._db, self._leaderboard_repo)
        self._daily_cache = DailyLeaderboardCache(self._db, self._leaderboard_repo)

        self._scheduler = BackgroundScheduler()
        self._shutdown_event = threading.Event()
        self._sync_lock = threading.Lock()
        self._stats_seed_lock = threading.Lock()
        self._stats_seeded = False

        self._current_interval: int | None = None
        self._last_purchase_at: datetime | None = None

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def _job_sync_purchases(self) -> None:
        """Fetch newer purchases incrementally and commit each page to Firestore.

        After every Zettle API page is processed, the corresponding card sums
        and the page's ``lastPurchaseHash`` are written to Firestore immediately.
        This makes catch-up resilient to hard container kills: on restart,
        the worker resumes from the last successfully committed hash.

        This job is protected by a lock so overlapping invocations (initial
        sync + interval) are skipped rather than running concurrently.

        If any purchases are synced, ``_last_purchase_at`` is updated so the
        adaptive check knows the bar is active.
        """
        from beertracker.core.date_utils import oslo_now_str

        if not self._sync_lock.acquire(blocking=False):
            logger.info("Sync in progress → skipped (%s)", oslo_now_str())
            return

        try:
            # Gate: never sync before info/stats is seeded, otherwise the
            # first in-transaction stats write would create the document
            # from zero and the historical totals would be lost forever.
            if not self._stats_seeded:
                self._try_seed_stats()
                if not self._stats_seeded:
                    logger.warning(
                        "Sync deferred — info/stats not seeded yet (%s)",
                        oslo_now_str(),
                    )
                    return

            current_hash = self._state_repo.get_last_purchase_hash()
            logger.info("Sync starting (%s)", oslo_now_str())

            total_purchases = 0
            total_cards_updated: set[str] = set()
            total_delta_spent = 0.0
            total_new_cards = 0
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
                    # Lifetime totals (existing)
                    card_totals = aggregate_by_card(purchases)
                    _applied, _skipped, delta_spent, new_cards = self._card_repo.batch_increment_sums(
                        card_totals,
                        page_last_purchase_hash=current_hash or "",
                    )

                    # Daily totals (new)
                    daily_totals = aggregate_by_card_and_day(purchases)
                    for bar_day, day_cards in daily_totals.items():
                        self._daily_usage_repo.merge_daily_card_totals(
                            bar_day=bar_day,
                            card_totals=day_cards,
                            page_last_purchase_hash=current_hash or "",
                        )

                    total_delta_spent += delta_spent
                    total_new_cards += new_cards
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

            # Stats are updated atomically inside each card transaction
            # (see _increment_card_transaction_body), so nothing to do
            # here. total_delta_spent / total_new_cards are for logging.

            # Update lastSyncAt even when idle to show the service is alive.
            # Without this, healthcheck fails after 10 minutes of no bar sales.
            self._state_repo.update_sync_state(
                current_hash or "", pages_synced=0
            )

            # Only real Zettle purchases count as bar activity.
            if total_purchases > 0:
                self._last_purchase_at = datetime.now(timezone.utc)
                logger.info(
                    "Bar activity recorded: %d purchase(s) (%s)",
                    total_purchases,
                    oslo_now_str(),
                )

            logger.info(
                "Sync done: %d purchases, %d cards, %d page(s), +%.2f kr, +%d new card(s) (%s)",
                total_purchases,
                len(total_cards_updated),
                page_count - 1,
                total_delta_spent,
                total_new_cards,
                oslo_now_str(),
            )

        except Exception:
            logger.exception("Sync failed (%s)", oslo_now_str())
        finally:
            self._sync_lock.release()

    def _job_refresh_token(self) -> None:
        """Refresh the Zettle OAuth token."""
        from beertracker.core.date_utils import oslo_now_str

        try:
            self._token_manager.refresh()
            logger.info("Token refreshed OK (%s)", oslo_now_str())
        except Exception:
            logger.exception("Token refresh failed (%s)", oslo_now_str())

    def _job_adaptive_check(self) -> None:
        """Adjust sync interval based on recent bar activity.

        Activity is defined as: a sync job found at least one new Zettle
        purchase. Firestore listener changes (user edits, leaderboard writes,
        etc.) do NOT count as activity.

        On startup, the service is always in hibernate until the first
        purchase arrives.
        """
        from beertracker.core.date_utils import oslo_now_str

        settings = get_settings()

        if self._last_purchase_at is None:
            active = False
        else:
            delta_seconds = (datetime.now(timezone.utc) - self._last_purchase_at).total_seconds()
            active = delta_seconds < (settings.sync_hibernate_after_minutes * 60)

        desired = (
            settings.sync_interval_active
            if active
            else settings.sync_interval_hibernate
        )

        if self._current_interval != desired:
            self._current_interval = desired
            logger.info(
                "Adaptive sync: %s → %ds (%s)",
                "ACTIVE" if active else "HIBERNATE",
                desired,
                oslo_now_str(),
            )
            try:
                self._scheduler.reschedule_job(
                    SYNC_JOB_ID,
                    trigger="interval",
                    seconds=desired,
                )
            except Exception:
                logger.exception("Reschedule failed (%s)", oslo_now_str())

    def _try_seed_stats(self) -> None:
        """Seed ``info/stats`` via full recompute if the document is missing.

        Idempotent and thread-safe. On Firestore quota errors the seeding
        is deferred and retried by ``_job_retry_startup`` and the sync
        gate.
        """
        if self._stats_seeded:
            return
        with self._stats_seed_lock:
            if self._stats_seeded:
                return
            try:
                if not self._stats_repo.exists():
                    logger.info("info/stats missing — seeding via full recompute (one-time)")
                    self._stats_repo.update_stats()
                self._stats_seeded = True
                logger.info("info/stats present and verified")
            except ResourceExhausted:
                logger.warning("Stats seeding deferred — Firestore quota exceeded")

    def _job_retry_startup(self) -> None:
        """Retry deferred startup tasks (stats seeding, cache listeners).

        Runs periodically; everything it touches is idempotent, so it is
        a cheap no-op once the service is fully started.
        """
        from beertracker.core.date_utils import oslo_now_str

        try:
            self._try_seed_stats()
            if not self._cache.is_started:
                self._cache.start()
            if not self._daily_cache.is_started:
                self._daily_cache.start()
        except Exception:
            logger.exception("Startup retry failed (%s)", oslo_now_str())

    def _job_reconcile_stats(self) -> None:
        """Daily reconciliation: recompute ``info/stats`` from ``cards``.

        Corrects any drift (float error, out-of-band writes, lost deltas).
        Scheduled at 05:00 UTC (~06/07:00 Oslo) when the bar is closed.
        """
        from beertracker.core.date_utils import oslo_now_str

        try:
            payload = self._stats_repo.update_stats()
            logger.info(
                "Stats reconciled: totalSpent=%.2f, totalCards=%d (%s)",
                payload["totalSpent"],
                payload["totalCards"],
                oslo_now_str(),
            )
        except ResourceExhausted:
            logger.warning("Stats reconciliation skipped — Firestore quota exceeded")
        except Exception:
            logger.exception("Stats reconciliation failed (%s)", oslo_now_str())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Configure and start the background scheduler."""
        settings = get_settings()
        logger.info("Starting SyncWorker...")

        # Register signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._signal_handler)

        # Seed info/stats if missing (idempotent; deferred on quota errors)
        self._try_seed_stats()

        # Start leaderboard caches (degrade gracefully on quota errors)
        self._cache.start()
        self._daily_cache.start()

        # Always start in hibernate — only real Zettle purchases flip to active
        initial_interval = settings.sync_interval_hibernate
        self._current_interval = initial_interval
        self._last_purchase_at = None

        # Schedule jobs
        self._scheduler.add_job(
            self._job_sync_purchases,
            "interval",
            seconds=initial_interval,
            id=SYNC_JOB_ID,
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._job_refresh_token,
            "interval",
            minutes=settings.token_refresh_minutes,
            id="refresh_token",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._job_adaptive_check,
            "interval",
            seconds=60,  # Check mode every minute
            id=ADAPTIVE_CHECK_JOB_ID,
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._job_retry_startup,
            "interval",
            seconds=600,
            id="retry_startup",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._job_reconcile_stats,
            "cron",
            hour=5,
            minute=0,
            id="reconcile_stats",
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
            "Scheduler started (hibernate %ds, token refresh every %dm, "
            "adaptive check every 60s)",
            initial_interval,
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
        self._cache.stop()
        self._daily_cache.stop()
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
