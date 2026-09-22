"""Main entry point for the BeerTracker background service."""

import logging
import sys

from beertracker.config import get_settings
from beertracker.logging_setup import configure_logging
from beertracker.scheduler import SyncWorker

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the BeerTracker background sync worker."""
    settings = get_settings()
    configure_logging()
    logging.getLogger().setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    logger.info("BeerTracker service starting...")
    logger.info("Log level: %s", settings.log_level)
    logger.info("Sync interval: %s seconds", settings.sync_interval_seconds)
    logger.info("Token refresh: %s minutes", settings.token_refresh_minutes)

    worker = SyncWorker()
    worker.start()


if __name__ == "__main__":
    main()
