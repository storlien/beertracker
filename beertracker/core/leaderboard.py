"""Leaderboard computation logic, ported from the frontend."""

from beertracker.core.models import Card, LeaderboardEntry, User


def compute_leaderboard(
    cards: dict[str, Card],
    users: dict[str, User],
    limit: int = 100,
) -> list[LeaderboardEntry]:
    """Compute the top-N leaderboard from cards and users.

    Replicates the frontend logic from Leaderboard.tsx:

    1. Build a lookup from card_number -> {user_id, name} using the
       ``cards`` field on each user.
    2. For each card, add its sum to either the owning user or as an
       unlinked ("Unknown") entry.
    3. Sort all entries by total sum descending and slice to ``limit``.

    Args:
        cards: Mapping from card_number to Card model (in-memory cache).
        users: Mapping from user_id to User model (in-memory cache).
        limit: Number of entries to return (default 100).

    Returns:
        Ordered list of LeaderboardEntry, highest sum first.
    """
    # Build card -> {user_id, name} map
    card_to_owner: dict[str, tuple[str, str]] = {}
    for user in users.values():
        owner_name = f"{user.first_name} {user.last_name}".strip() or "Unknown"
        for card_id in user.card_ids:
            card_to_owner[card_id] = (user.user_id, owner_name)

    # Accumulate per-user and collect unlinked
    user_sums: dict[str, tuple[float, list[str]]] = {}
    unlinked: list[LeaderboardEntry] = []

    for card_number, card in cards.items():
        owner = card_to_owner.get(card_number)
        if owner:
            user_id, _name = owner
            existing = user_sums.get(user_id)
            if existing:
                total, card_list = existing
                card_list.append(card_number)
                user_sums[user_id] = (total + card.total_sum, card_list)
            else:
                user_sums[user_id] = (card.total_sum, [card_number])
        else:
            unlinked.append(
                LeaderboardEntry(
                    entry_id=card_number,
                    name="Unknown",
                    is_user=False,
                    total_sum=card.total_sum,
                    card_ids=[card_number],
                )
            )

    # Build user entries
    user_entries: list[LeaderboardEntry] = []
    for user_id, (total, card_list) in user_sums.items():
        user = users.get(user_id)
        name = f"{user.first_name} {user.last_name}".strip() if user else "Unknown"
        user_entries.append(
            LeaderboardEntry(
                entry_id=user_id,
                name=name,
                is_user=True,
                total_sum=round(total, 2),
                card_ids=card_list,
            )
        )

    # Combine, sort descending, slice
    all_entries = [*user_entries, *unlinked]
    all_entries.sort(key=lambda e: e.total_sum, reverse=True)
    return all_entries[:limit]


def compute_daily_leaderboard(
    daily_usage: dict[str, float],
    users: dict[str, User],
) -> list[LeaderboardEntry]:
    """Compute a user-level leaderboard for a single bar day's usage.

    Unlike ``compute_leaderboard``, this takes a *single bar day's*
    card spending (not cumulative lifetime sums) and produces a sorted
    list of entries.  Linked cards are aggregated under their owning user;
    unlinked cards appear as "Unknown" entries.

    There is **no limit** — every card that was used on that bar day
    appears in the result.

    Args:
        daily_usage: Mapping from card_number to amount spent on this bar day.
        users: Mapping from user_id to User model.

    Returns:
        Ordered list of LeaderboardEntry for the bar day, highest sum first.
    """
    # card_number -> (user_id, name)
    card_to_owner: dict[str, tuple[str, str]] = {}
    for user in users.values():
        owner_name = f"{user.first_name} {user.last_name}".strip() or "Unknown"
        for card_id in user.card_ids:
            card_to_owner[card_id] = (user.user_id, owner_name)

    user_sums: dict[str, tuple[float, list[str]]] = {}
    unlinked: list[LeaderboardEntry] = []

    for card_number, amount in daily_usage.items():
        owner = card_to_owner.get(card_number)
        if owner:
            user_id, _name = owner
            existing = user_sums.get(user_id)
            if existing:
                total, card_list = existing
                card_list.append(card_number)
                user_sums[user_id] = (total + amount, card_list)
            else:
                user_sums[user_id] = (amount, [card_number])
        else:
            unlinked.append(
                LeaderboardEntry(
                    entry_id=card_number,
                    name="Unknown",
                    is_user=False,
                    total_sum=round(amount, 2),
                    card_ids=[card_number],
                )
            )

    user_entries: list[LeaderboardEntry] = []
    for user_id, (total, card_list) in user_sums.items():
        user = users.get(user_id)
        name = f"{user.first_name} {user.last_name}".strip() if user else "Unknown"
        user_entries.append(
            LeaderboardEntry(
                entry_id=user_id,
                name=name,
                is_user=True,
                total_sum=round(total, 2),
                card_ids=card_list,
            )
        )

    all_entries = [*user_entries, *unlinked]
    all_entries.sort(key=lambda e: e.total_sum, reverse=True)
    return all_entries
