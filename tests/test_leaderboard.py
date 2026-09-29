"""Unit tests for leaderboard computation."""

import pytest

from beertracker.core.leaderboard import compute_daily_leaderboard, compute_leaderboard
from beertracker.core.models import Card, LeaderboardEntry, User


class TestComputeLeaderboard:
    """Tests for the top-100 leaderboard computation logic."""

    def test_empty_inputs(self):
        assert compute_leaderboard({}, {}) == []

    def test_single_unlinked_card(self):
        cards = {"1111111111": Card(card_number="1111111111", total_sum=100.0)}
        result = compute_leaderboard(cards, {})
        assert len(result) == 1
        assert result[0] == LeaderboardEntry(
            entry_id="1111111111",
            name="Unknown",
            is_user=False,
            total_sum=100.0,
            card_ids=["1111111111"],
        )

    def test_single_user_single_card(self):
        cards = {"1111111111": Card(card_number="1111111111", total_sum=150.0)}
        users = {
            "user1": User(
                user_id="user1", first_name="Alice", last_name="Smith", card_ids=["1111111111"]
            ),
        }
        result = compute_leaderboard(cards, users)
        assert len(result) == 1
        assert result[0] == LeaderboardEntry(
            entry_id="user1",
            name="Alice Smith",
            is_user=True,
            total_sum=150.0,
            card_ids=["1111111111"],
        )

    def test_user_with_multiple_cards(self):
        cards = {
            "1111111111": Card(card_number="1111111111", total_sum=50.0),
            "2222222222": Card(card_number="2222222222", total_sum=75.0),
        }
        users = {
            "user1": User(
                user_id="user1",
                first_name="Bob",
                last_name="Jones",
                card_ids=["1111111111", "2222222222"],
            ),
        }
        result = compute_leaderboard(cards, users)
        assert len(result) == 1
        assert result[0].total_sum == 125.0
        assert sorted(result[0].card_ids) == ["1111111111", "2222222222"]

    def test_mixed_linked_and_unlinked(self):
        cards = {
            "1111111111": Card(card_number="1111111111", total_sum=200.0),
            "2222222222": Card(card_number="2222222222", total_sum=50.0),
        }
        users = {
            "user1": User(
                user_id="user1",
                first_name="Alice",
                last_name="Smith",
                card_ids=["1111111111"],
            ),
        }
        result = compute_leaderboard(cards, users)
        assert len(result) == 2
        # Alice should rank first
        assert result[0].entry_id == "user1"
        assert result[0].total_sum == 200.0
        # Unlinked card second
        assert result[1].entry_id == "2222222222"
        assert result[1].name == "Unknown"
        assert result[1].total_sum == 50.0

    def test_sorting_descending(self):
        cards = {
            "1111111111": Card(card_number="1111111111", total_sum=10.0),
            "2222222222": Card(card_number="2222222222", total_sum=500.0),
            "3333333333": Card(card_number="3333333333", total_sum=100.0),
        }
        result = compute_leaderboard(cards, {}, limit=100)
        sums = [e.total_sum for e in result]
        assert sums == [500.0, 100.0, 10.0]

    def test_limit_100(self):
        cards = {f"{i:010d}": Card(card_number=f"{i:010d}", total_sum=float(i)) for i in range(150)}
        result = compute_leaderboard(cards, {}, limit=100)
        assert len(result) == 100
        # Highest sums first
        assert result[0].total_sum == 149.0
        assert result[-1].total_sum == 50.0

    def test_multiple_users_tie_breaking(self):
        cards = {
            "1111111111": Card(card_number="1111111111", total_sum=100.0),
            "2222222222": Card(card_number="2222222222", total_sum=200.0),
        }
        users = {
            "u1": User(user_id="u1", first_name="A", last_name="A", card_ids=["1111111111"]),
            "u2": User(user_id="u2", first_name="B", last_name="B", card_ids=["2222222222"]),
        }
        result = compute_leaderboard(cards, users)
        assert result[0].entry_id == "u2"
        assert result[0].total_sum == 200.0
        assert result[1].entry_id == "u1"
        assert result[1].total_sum == 100.0

    def test_user_with_no_cards_in_data(self):
        """A user whose cards aren't in the cards dict should not appear."""
        cards = {"1111111111": Card(card_number="1111111111", total_sum=100.0)}
        users = {
            "u1": User(user_id="u1", first_name="Has", last_name="Card", card_ids=["1111111111"]),
            "u2": User(
                user_id="u2", first_name="No", last_name="Card", card_ids=["9999999999"]
            ),
        }
        result = compute_leaderboard(cards, users)
        assert len(result) == 1
        assert result[0].entry_id == "u1"


class TestComputeDailyLeaderboard:
    """Tests for compute_daily_leaderboard — single-day user-level leaderboard."""

    def test_empty_inputs(self):
        assert compute_daily_leaderboard({}, {}) == []

    def test_single_unlinked_card(self):
        usage = {"1111111111": 55.0}
        result = compute_daily_leaderboard(usage, {})
        assert len(result) == 1
        assert result[0] == LeaderboardEntry(
            entry_id="1111111111",
            name="Unknown",
            is_user=False,
            total_sum=55.0,
            card_ids=["1111111111"],
        )

    def test_single_user_single_card(self):
        usage = {"1111111111": 80.0}
        users = {
            "user1": User(
                user_id="user1", first_name="Alice", last_name="Smith", card_ids=["1111111111"]
            ),
        }
        result = compute_daily_leaderboard(usage, users)
        assert len(result) == 1
        assert result[0] == LeaderboardEntry(
            entry_id="user1",
            name="Alice Smith",
            is_user=True,
            total_sum=80.0,
            card_ids=["1111111111"],
        )

    def test_user_with_multiple_cards_same_day(self):
        usage = {
            "1111111111": 50.0,
            "2222222222": 30.0,
        }
        users = {
            "user1": User(
                user_id="user1",
                first_name="Bob",
                last_name="Jones",
                card_ids=["1111111111", "2222222222"],
            ),
        }
        result = compute_daily_leaderboard(usage, users)
        assert len(result) == 1
        assert result[0].entry_id == "user1"
        assert result[0].total_sum == 80.0
        assert sorted(result[0].card_ids) == ["1111111111", "2222222222"]

    def test_mixed_linked_and_unlinked(self):
        usage = {
            "1111111111": 200.0,
            "2222222222": 50.0,
        }
        users = {
            "user1": User(
                user_id="user1",
                first_name="Alice",
                last_name="Smith",
                card_ids=["1111111111"],
            ),
        }
        result = compute_daily_leaderboard(usage, users)
        assert len(result) == 2
        # Alice should rank first
        assert result[0].entry_id == "user1"
        assert result[0].total_sum == 200.0
        # Unlinked card second
        assert result[1].entry_id == "2222222222"
        assert result[1].name == "Unknown"
        assert result[1].total_sum == 50.0

    def test_sorting_descending(self):
        usage = {
            "1111111111": 10.0,
            "2222222222": 500.0,
            "3333333333": 100.0,
        }
        result = compute_daily_leaderboard(usage, {})
        sums = [e.total_sum for e in result]
        assert sums == [500.0, 100.0, 10.0]

    def test_no_limit(self):
        """Daily leaderboard has no entry limit."""
        usage = {f"{i:010d}": float(i) for i in range(150)}
        result = compute_daily_leaderboard(usage, {})
        assert len(result) == 150
        assert result[0].total_sum == 149.0
        assert result[1].total_sum == 148.0

    def test_multiple_users_tie_breaking(self):
        usage = {
            "1111111111": 100.0,
            "2222222222": 200.0,
        }
        users = {
            "u1": User(user_id="u1", first_name="A", last_name="A", card_ids=["1111111111"]),
            "u2": User(user_id="u2", first_name="B", last_name="B", card_ids=["2222222222"]),
        }
        result = compute_daily_leaderboard(usage, users)
        assert result[0].entry_id == "u2"
        assert result[0].total_sum == 200.0
        assert result[1].entry_id == "u1"
        assert result[1].total_sum == 100.0

    def test_unused_cards_not_present(self):
        """Cards with zero (or not in the usage dict) should not appear."""
        usage = {"1111111111": 50.0}
        users = {
            "u1": User(
                user_id="u1",
                first_name="Alice",
                last_name="Smith",
                card_ids=["1111111111", "2222222222"],
            ),
        }
        result = compute_daily_leaderboard(usage, users)
        assert len(result) == 1
        assert result[0].entry_id == "u1"
        assert result[0].total_sum == 50.0
        assert result[0].card_ids == ["1111111111"]
