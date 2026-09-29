"""Unit tests for bar-day calculation with Oslo timezone."""

from datetime import datetime, timezone

import pytest

from beertracker.core.date_utils import OSLO_TZ, purchase_to_bar_day


class TestPurchaseToBarDay:
    """Tests for purchase_to_bar_day — bar day runs 05:00-05:00 Oslo time."""

    def test_midday_saturday(self):
        """12:00 Saturday -> same bar day."""
        # 2024-06-01 12:00 local Oslo = 2024-06-01 10:00 UTC (summer time)
        ts = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)
        assert purchase_to_bar_day(ts) == "2024-06-01"

    def test_late_night_saturday(self):
        """23:00 Saturday -> same bar day."""
        ts = datetime(2024, 6, 1, 21, 0, tzinfo=timezone.utc)  # Oslo is UTC+2 in Jun
        assert purchase_to_bar_day(ts) == "2024-06-01"

    def test_early_morning_still_saturday(self):
        """05:30 Saturday (just after cutoff) -> same bar day."""
        # Oslo local 2024-06-01 05:30 = UTC 2024-06-01 03:30 (summer +2)
        ts = datetime(2024, 6, 1, 3, 30, tzinfo=timezone.utc)
        assert purchase_to_bar_day(ts) == "2024-06-01"

    def test_early_morning_rolls_to_friday(self):
        """03:30 Saturday (before 05:00 cutoff) -> Friday bar day."""
        # Oslo local 2024-06-01 03:30 = UTC 2024-06-01 01:30
        ts = datetime(2024, 6, 1, 1, 30, tzinfo=timezone.utc)
        assert purchase_to_bar_day(ts) == "2024-05-31"

    def test_utc_midnight_rolls_to_friday(self):
        """00:00 UTC Saturday = 02:00 Oslo Saturday (summer time), still before cutoff."""
        ts = datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc)
        # Oslo: 2024-06-01 02:00 -> before 05:00, so rolls to previous day
        assert purchase_to_bar_day(ts) == "2024-05-31"

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetimes are treated as UTC before converting to Oslo."""
        # Naive 2024-06-01 10:00 means 10:00 UTC = 12:00 Oslo
        ts = datetime(2024, 6, 1, 10, 0)
        assert purchase_to_bar_day(ts) == "2024-06-01"

    def test_exactly_at_cutoff(self):
        """05:00 on the dot stays on current day."""
        # Oslo 05:00 = UTC 03:00 (summer)
        ts = datetime(2024, 6, 1, 3, 0, tzinfo=timezone.utc)
        assert purchase_to_bar_day(ts) == "2024-06-01"

    def test_winter_time_offset(self):
        """Winter time: Oslo is UTC+1, verify cutoff still works."""
        # Oslo 2024-01-15 03:30 = UTC 2024-01-15 02:30 (winter +1)
        ts = datetime(2024, 1, 15, 2, 30, tzinfo=timezone.utc)
        # Oslo time = 03:30, before 05:00 -> rolls to previous day
        assert purchase_to_bar_day(ts) == "2024-01-14"

    def test_winter_time_midday(self):
        """Winter midday stays on same day."""
        # Oslo 2024-01-15 14:00 = UTC 2024-01-15 13:00
        ts = datetime(2024, 1, 15, 13, 0, tzinfo=timezone.utc)
        assert purchase_to_bar_day(ts) == "2024-01-15"
