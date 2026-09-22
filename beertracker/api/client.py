"""Zettle API client with pagination and retry logic."""

import logging
from typing import Any

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from beertracker.api.token_manager import TokenManager
from beertracker.config import get_settings

logger = logging.getLogger(__name__)

ZETTLE_BASE_URL = "https://purchase.izettle.com"
PURCHASES_ENDPOINT = "/purchases/v2"
DEFAULT_PAGE_SIZE = 1000


class ZettleAPIError(RuntimeError):
    """Raised for unrecoverable Zettle API errors."""

    pass


class ZettleClient:
    """HTTP client for the Zettle Purchases API.

    Handles Bearer token injection, pagination via ``lastPurchaseHash``,
    and retry logic with exponential backoff.
    """

    def __init__(self, token_manager: TokenManager) -> None:
        self._token_manager = token_manager
        self._client = httpx.Client(
            base_url=ZETTLE_BASE_URL,
            timeout=httpx.Timeout(60.0, connect=15.0),
            http2=False,  # Zettle does not support HTTP/2
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, ZettleAPIError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Make a single GET request with auth and error handling."""
        headers = {
            "Authorization": f"Bearer {self._token_manager.token}",
            "Content-Type": "application/json",
        }

        try:
            response = self._client.get(PURCHASES_ENDPOINT, headers=headers, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # Token expired? Force refresh and raise to trigger retry.
            if status in (401, 403):
                logger.warning("Token expired (HTTP %s), will retry after refresh", status)
                self._token_manager.refresh()
                raise ZettleAPIError(f"HTTP {status}") from exc
            raise

        return response.json()

    def get_purchases(
        self,
        *,
        start_date: str | None = None,
        last_purchase_hash: str | None = None,
        descending: bool = False,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Fetch a single page of purchases from Zettle.

        Args:
            start_date: ISO date string (YYYY-MM-DD) to start from.
            last_purchase_hash: Pagination cursor; fetch purchases after this hash.
            descending: Whether to return newest first.
            limit: Max items per page (default 1000).

        Returns:
            Raw JSON dict from Zettle API.
        """
        params: dict[str, Any] = {"limit": limit}
        if start_date:
            params["startDate"] = start_date
        if last_purchase_hash:
            params["lastPurchaseHash"] = last_purchase_hash
        if descending:
            params["descending"] = "true"

        return self._get(params)

    def get_all_newer_purchases(
        self,
        last_purchase_hash: str | None = None,
        start_date: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch **all** purchases starting from a hash or date.

        Automatically paginates through Zettle's cursor-based API until
        ``purchases`` is empty. Returns the raw purchase dict list plus
        the *new* ``lastPurchaseHash``.

        Args:
            last_purchase_hash: The hash to start after (most recent known).
            start_date: Fallback ISO date if no hash is provided.

        Returns:
            Tuple of (list of raw purchase dicts, final lastPurchaseHash).
        """
        all_purchases: list[dict[str, Any]] = []
        current_hash = last_purchase_hash
        final_hash: str | None = current_hash
        page = 0

        while True:
            page += 1
            logger.debug("Fetching page %d (lastPurchaseHash=%s)", page, current_hash)

            data = self.get_purchases(
                start_date=start_date if page == 1 and not current_hash else None,
                last_purchase_hash=current_hash,
            )

            purchases = data.get("purchases", [])
            if not purchases:
                logger.info("No more purchases after page %d", page)
                break

            all_purchases.extend(purchases)
            final_hash = data.get("lastPurchaseHash") or current_hash
            current_hash = final_hash

        logger.info(
            "Fetched %d total purchases across %d page(s)",
            len(all_purchases),
            page,
        )
        return all_purchases, final_hash
