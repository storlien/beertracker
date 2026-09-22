"""Application configuration via Pydantic Settings."""

import logging

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    The Zettle assertion key (API key) and client ID MUST be provided via
    the environment or a .env file. They are never hardcoded in source code.

    Attributes:
        zettle_client_id: OAuth client ID for Zettle API (LaBamba).
        zettle_assertion_key: JWT assertion key for Zettle OAuth (required).
        firebase_service_account_path: Path to Firebase service account JSON key file.
        sync_interval_seconds: Interval between purchase sync jobs (default 60).
        token_refresh_minutes: Interval between token refresh jobs (default 30).
        log_level: Logging level (default INFO).
    """

    zettle_client_id: str = ""
    zettle_assertion_key: str = ""
    firebase_service_account_path: str = "firebase-service-account.json"
    sync_interval_seconds: int = 60
    token_refresh_minutes: int = 30
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_prefix="BEERTRACKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def get_settings() -> Settings:
    """Return validated Settings, failing fast if secrets are missing."""
    try:
        settings = Settings()
        if not settings.zettle_client_id:
            raise RuntimeError(
                "BEERTRACKER_ZETTLE_CLIENT_ID is required but not set.\n"
                "  LaBamba client ID: e8339854-d24e-11ed-bab2-c90128e9ad74\n"
                "  Add it to your .env file:\n"
                "    BEERTRACKER_ZETTLE_CLIENT_ID=e8339854-d24e-11ed-bab2-c90128e9ad74"
            )
        if not settings.zettle_assertion_key:
            raise RuntimeError(
                "BEERTRACKER_ZETTLE_ASSERTION_KEY is required but not set.\n"
                "  1. Copy .env.example to .env\n"
                "  2. Add your Zettle assertion key to .env\n"
                "  3. Ensure .env has file permissions 600 (chmod 600 .env)\n"
                "  4. The .env file is gitignored and must never be committed."
            )
        return settings
    except ValidationError as exc:
        missing = [
            err["loc"][0] for err in exc.errors() if err["type"] == "missing"
        ]
        if "zettle_assertion_key" in missing:
            raise RuntimeError(
                "BEERTRACKER_ZETTLE_ASSERTION_KEY is required but not set.\n"
                "  1. Copy .env.example to .env\n"
                "  2. Add your Zettle assertion key to .env\n"
                "  3. Ensure .env has file permissions 600 (chmod 600 .env)\n"
                "  4. The .env file is gitignored and must never be committed."
            ) from exc
        raise
