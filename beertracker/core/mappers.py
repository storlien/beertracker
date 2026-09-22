"""Purchase aggregation and mapping logic ported from PurchaseMapper.java."""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from beertracker.core.models import Purchase

logger = logging.getLogger(__name__)

#: Payment type to include (card purchases only, not Vipps).
CARD_PAYMENT_TYPE = "IZETTLE_CARD"


def parse_purchases(json_data: dict[str, Any]) -> list[Purchase]:
    """Parse a Zettle API JSON response into Purchase objects.

    Only includes IZETTLE_CARD payments. Amounts are converted from øre to kroner.

    Args:
        json_data: Parsed JSON dict from Zettle purchases/v2 endpoint.

    Returns:
        List of Purchase objects extracted from the response.
    """
    purchases: list[Purchase] = []
    raw_purchases = json_data.get("purchases", [])

    if not isinstance(raw_purchases, list):
        logger.warning("Unexpected 'purchases' type: %s", type(raw_purchases))
        return purchases

    for raw in raw_purchases:
        try:
            payments = raw.get("payments", [])
            if not payments:
                continue

            payment = payments[0]
            payment_type = payment.get("type", "")

            if payment_type != CARD_PAYMENT_TYPE:
                continue

            attributes = payment.get("attributes", {})
            masked_pan = attributes.get("maskedPan", "")
            if not masked_pan:
                continue

            # Remove asterisks from masked pan -> 10-digit card number
            card_number = masked_pan.replace("*", "")
            if len(card_number) != 10:
                logger.debug("Skipping card with unexpected length: %s", card_number)
                continue

            # Amount is in smallest currency unit (øre); convert to kroner
            raw_amount = raw.get("amount", 0)
            if isinstance(raw_amount, int | float):
                amount = float(raw_amount) / 100.0
            else:
                amount = 0.0

            timestamp_raw = raw.get("timestamp", "")
            ts: datetime | None = None
            if timestamp_raw:
                try:
                    ts = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
                except ValueError:
                    pass

            purchase_hash = raw.get("hash", "")
            if not purchase_hash:
                purchase_hash = raw.get("purchaseUUID", "")

            purchases.append(
                Purchase(
                    card_number=card_number,
                    amount=amount,
                    payment_type=payment_type,
                    purchase_hash=str(purchase_hash),
                    timestamp=ts,
                )
            )

        except Exception:
            logger.exception("Failed to parse purchase item: %s", raw)
            continue

    logger.info("Parsed %d card purchases from %d raw items", len(purchases), len(raw_purchases))
    return purchases


def aggregate_by_card(purchases: list[Purchase]) -> dict[str, float]:
    """Aggregate purchase amounts grouped by card number.

    Args:
        purchases: List of Purchase objects.

    Returns:
        Mapping from 10-digit card number to total amount in kroner.
    """
    totals: defaultdict[str, float] = defaultdict(float)
    for p in purchases:
        totals[p.card_number] += p.amount
    return dict(totals)
