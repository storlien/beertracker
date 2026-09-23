"""Core domain models."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Purchase:
    """A single Zettle purchase transaction.

    Attributes:
        card_number: 10-digit card number (first 6 + last 4, no asterisks).
        amount: Amount in kroner (øre / 100).
        payment_type: Payment method type (e.g., "IZETTLE_CARD").
        purchase_hash: Unique purchase hash from Zettle.
        timestamp: ISO timestamp of the purchase.
    """

    card_number: str
    amount: float
    payment_type: str
    purchase_hash: str
    timestamp: datetime | None = None


@dataclass
class Card:
    """A payment card tracked in Firestore.

    Attributes:
        card_number: 10-digit card identifier (first 6 + last 4).
        total_sum: Cumulative amount spent on this card in kroner.
        linked_user_id: Optional Firestore user document ID who owns this card.
        created_at: UTC timestamp when first recorded.
        updated_at: UTC timestamp of last update.
    """

    card_number: str
    total_sum: float
    linked_user_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class LeaderboardEntry:
    """A single entry in the top-100 leaderboard.

    Attributes:
        entry_id: User ID for linked entries, card number for unlinked.
        name: Display name (first + last name, or "Unknown").
        is_user: Whether this entry represents a registered user.
        total_sum: Cumulative amount in kroner.
        card_ids: List of linked card numbers.
    """

    entry_id: str
    name: str
    is_user: bool
    total_sum: float
    card_ids: list[str] = field(default_factory=list)


@dataclass
class User:
    """A tracked person (managed by the website).

    Attributes:
        user_id: Firestore document ID.
        first_name: User's first name.
        last_name: User's last name.
        card_ids: List of linked 10-digit card numbers.
    """

    user_id: str
    first_name: str
    last_name: str
    card_ids: list[str] = field(default_factory=list)
