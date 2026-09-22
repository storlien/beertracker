#!/usr/bin/env python3
"""Healthcheck: verify last sync was within last 10 minutes."""

import os
import sys
from datetime import datetime, timedelta, timezone

from firebase_admin import credentials, initialize_app, firestore


def main() -> int:
    try:
        cred = credentials.Certificate(
            os.environ.get("BEERTRACKER_FIREBASE_SERVICE_ACCOUNT_PATH", "/app/firebase-service-account.json")
        )
        initialize_app(cred)
        db = firestore.client()
        doc = db.collection("info").document("state").get()
        if not doc.exists:
            return 1
        ts = doc.to_dict().get("lastSyncAt")
        if ts is None:
            return 1
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        if ts < cutoff:
            return 1
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
