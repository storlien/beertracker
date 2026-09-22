"""OAuth token manager for Zettle API."""

import logging
import threading
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from beertracker.config import get_settings

logger = logging.getLogger(__name__)

ZETTLE_AUTH_URL = "https://oauth.zettle.com/token"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"


@dataclass
class TokenInfo:
    """OAuth token metadata."""

    access_token: str
    expires_in: int
    token_type: str


class TokenManager:
    """Manages Zettle OAuth tokens with automatic background refresh.

    Thread-safe singleton-like behavior. Obtains an initial token on
    instantiation and exposes :meth:`get_token` for consumers.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client_id = settings.zettle_client_id
        self._assertion_key = settings.zettle_assertion_key
        self._token: str | None = None
        self._lock = threading.RLock()

        if not self._client_id or not self._assertion_key:
            raise RuntimeError("ZETTLE_CLIENT_ID and ZETTLE_ASSERTION_KEY must be set")

        logger.info("TokenManager: fetching initial access token")
        self._fetch_token()

    @property
    def token(self) -> str:
        """Return the current valid access token."""
        with self._lock:
            if self._token is None:
                raise RuntimeError("No token available")
            return self._token

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        reraise=True,
    )
    def _fetch_token(self) -> None:
        """Fetch a new access token from Zettle OAuth endpoint."""
        try:
            response = httpx.post(
                ZETTLE_AUTH_URL,
                data={
                    "grant_type": GRANT_TYPE,
                    "client_id": self._client_id,
                    "assertion": self._assertion_key,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Token fetch failed: HTTP %s - %s",
                exc.response.status_code,
                exc.response.text,
            )
            raise

        data: dict[str, Any] = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise RuntimeError("No access_token in Zettle OAuth response")

        with self._lock:
            self._token = access_token

        expires_in = data.get("expires_in", 7200)
        logger.info(
            "Token refreshed successfully (expires_in=%ds)",
            expires_in,
        )

    def refresh(self) -> None:
        """Explicitly refresh the token (used by scheduler)."""
        self._fetch_token()
