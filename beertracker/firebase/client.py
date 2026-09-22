"""Firebase Admin SDK client initialization."""

import logging

import firebase_admin
from firebase_admin import credentials, initialize_app

from beertracker.config import get_settings

logger = logging.getLogger(__name__)

_db = None


def get_firestore_client():
    """Return the Firestore client singleton.

    Lazily initializes Firebase if not already done.
    """
    global _db

    if _db is None:
        settings = get_settings()
        logger.info(
            "Initializing Firebase with service account: %s",
            settings.firebase_service_account_path,
        )
        cred = credentials.Certificate(settings.firebase_service_account_path)
        initialize_app(cred)
        _db = firebase_admin.firestore.client()
        logger.info("Firebase initialized successfully")

    return _db
