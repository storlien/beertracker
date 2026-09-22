"""Unit tests for core domain validators, ported from PersonTest.java."""

from datetime import datetime

import pytest

from beertracker.core.models import Card, Purchase
from beertracker.core.validators import (
    ValidationError,
    validate_amount,
    validate_card_number,
    validate_card_parts,
    validate_name,
)


class TestValidateName:
    """Ported from PersonTest.testValidateName."""

    def test_valid_names(self):
        validate_name("Carl", "Storlien")
        validate_name("Paul Andre Johanson", "Storlien")
        validate_name("Annika", "Johansen")
        validate_name("Louise", "Wiken")

    def test_invalid_characters(self):
        with pytest.raises(ValidationError):
            validate_name("Edw4rd", "Storlien")

    def test_empty_name(self):
        with pytest.raises(ValidationError):
            validate_name("", "Storlien")
        with pytest.raises(ValidationError):
            validate_name("Edward", "")

    def test_whitespace_only(self):
        with pytest.raises(ValidationError):
            validate_name("   ", "Storlien")


class TestValidateCardParts:
    """Ported from PersonTest.testValidateCard and constructor logic."""

    def test_valid_card(self):
        result = validate_card_parts("123456", "1234")
        assert result == "1234561234"

    def test_invalid_first_six_too_short(self):
        with pytest.raises(ValidationError):
            validate_card_parts("12345", "1234")

    def test_invalid_first_six_non_numeric(self):
        with pytest.raises(ValidationError):
            validate_card_parts("12345a", "1234")

    def test_invalid_last_four_non_numeric(self):
        with pytest.raises(ValidationError):
            validate_card_parts("123456", "123a")


class TestValidateCardNumber:
    def test_valid_10_digit(self):
        validate_card_number("1234567890")

    def test_too_short(self):
        with pytest.raises(ValidationError):
            validate_card_number("123456789")

    def test_too_long(self):
        with pytest.raises(ValidationError):
            validate_card_number("12345678901")

    def test_non_numeric(self):
        with pytest.raises(ValidationError):
            validate_card_number("123456789a")


class TestValidateAmount:
    """Ported from PersonTest.testValidateAmount."""

    def test_positive_amount(self):
        validate_amount(10.5)

    def test_zero_amount(self):
        validate_amount(0.0)

    def test_negative_amount(self):
        with pytest.raises(ValidationError):
            validate_amount(-10.5)


class TestModels:
    """Smoke tests for dataclasses."""

    def test_purchase_creation(self):
        p = Purchase(
            card_number="1234567890",
            amount=55.0,
            payment_type="IZETTLE_CARD",
            purchase_hash="abc123",
            timestamp=datetime.now(),
        )
        assert p.card_number == "1234567890"
        assert p.amount == 55.0

    def test_card_creation(self):
        c = Card(card_number="1234567890", total_sum=120.0)
        assert c.total_sum == 120.0
        assert c.linked_user_id is None
