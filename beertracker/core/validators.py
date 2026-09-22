"""Validation logic ported from Person.java."""

import re

#: Pattern for card first 6 digits.
CARD_FIRST6_PATTERN = re.compile(r"^\d{6}$")

#: Pattern for card last 4 digits.
CARD_LAST4_PATTERN = re.compile(r"^\d{4}$")

#: Full 10-digit card pattern.
CARD_NUMBER_PATTERN = re.compile(r"^\d{10}$")


class ValidationError(Exception):
    """Raised when validation fails."""

    pass


def validate_name(first_name: str, last_name: str) -> None:
    """Validate that first and last names contain only valid letters.

    Supports Norwegian characters.

    Args:
        first_name: The first name to validate.
        last_name: The last name to validate.

    Raises:
        ValidationError: If either name is empty, contains invalid characters,
            or consists only of whitespace.
    """
    # Allow standard ASCII letters plus Norwegian æøåÆØÅ
    pattern = re.compile(r"^[a-zA-ZæøåÆØÅ]+(?:[\s-][a-zA-ZæøåÆØÅ]+)*$")
    for name, label in ((first_name, "first_name"), (last_name, "last_name")):
        if not name or not name.strip():
            raise ValidationError(f"{label} cannot be empty")
        if not pattern.match(name.strip()):
            raise ValidationError(f"{label} contains invalid characters: {name!r}")


def validate_card_parts(first_six: str, last_four: str) -> str:
    """Validate card number parts and return the combined 10-digit number.

    Args:
        first_six: First 6 digits of the card number.
        last_four: Last 4 digits of the card number.

    Returns:
        The combined 10-digit card number.

    Raises:
        ValidationError: If the parts do not match the required format.
    """
    if not CARD_FIRST6_PATTERN.match(first_six):
        raise ValidationError(
            f"Invalid first 6 digits of card number: {first_six!r} (expected 6 digits)"
        )
    if not CARD_LAST4_PATTERN.match(last_four):
        raise ValidationError(
            f"Invalid last 4 digits of card number: {last_four!r} (expected 4 digits)"
        )
    return first_six + last_four


def validate_card_number(card_number: str) -> None:
    """Validate a full 10-digit card number.

    Args:
        card_number: The 10-digit card number to validate.

    Raises:
        ValidationError: If the card number is not exactly 10 digits.
    """
    if not CARD_NUMBER_PATTERN.match(card_number):
        raise ValidationError(
            f"Invalid card number: {card_number!r} (expected exactly 10 digits)"
        )


def validate_amount(amount: float) -> None:
    """Validate that an amount is non-negative.

    Args:
        amount: The amount to validate.

    Raises:
        ValidationError: If the amount is negative.
    """
    if amount < 0:
        raise ValidationError(f"Amount cannot be negative: {amount}")
