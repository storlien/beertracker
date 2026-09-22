"""Unit tests for purchase parsing and aggregation logic."""

from beertracker.core.mappers import aggregate_by_card, parse_purchases


class TestParsePurchases:
    """Tests for parse_purchases — filters IZETTLE_CARD and converts øre to kroner."""

    def test_empty_purchases(self):
        result = parse_purchases({"purchases": []})
        assert result == []

    def test_single_card_payment(self):
        data = {
            "purchases": [
                {
                    "amount": 5500,  # 55.00 kr
                    "payments": [
                        {
                            "type": "IZETTLE_CARD",
                            "attributes": {"maskedPan": "123456******7890"},
                        }
                    ],
                    "hash": "abc123",
                    "timestamp": "2024-06-01T12:00:00Z",
                }
            ]
        }
        result = parse_purchases(data)
        assert len(result) == 1
        assert result[0].card_number == "1234567890"
        assert result[0].amount == 55.0
        assert result[0].payment_type == "IZETTLE_CARD"

    def test_ignores_vipps(self):
        data = {
            "purchases": [
                {
                    "amount": 1200,
                    "payments": [
                        {"type": "VIPPS", "attributes": {"maskedPan": ""}}
                    ],
                    "hash": "vipps123",
                }
            ]
        }
        result = parse_purchases(data)
        assert result == []

    def test_multiple_payments_same_card(self):
        data = {
            "purchases": [
                {
                    "amount": 1500,
                    "payments": [
                        {
                            "type": "IZETTLE_CARD",
                            "attributes": {"maskedPan": "111111******2222"},
                        }
                    ],
                    "hash": "h1",
                },
                {
                    "amount": 2500,
                    "payments": [
                        {
                            "type": "IZETTLE_CARD",
                            "attributes": {"maskedPan": "111111******2222"},
                        }
                    ],
                    "hash": "h2",
                },
            ]
        }
        result = parse_purchases(data)
        assert len(result) == 2
        assert result[0].amount == 15.0
        assert result[1].amount == 25.0
        assert all(r.card_number == "1111112222" for r in result)

    def test_skips_bad_masked_pan(self):
        data = {
            "purchases": [
                {
                    "amount": 1000,
                    "payments": [
                        {
                            "type": "IZETTLE_CARD",
                            "attributes": {"maskedPan": "too-short"},
                        }
                    ],
                    "hash": "bad",
                }
            ]
        }
        result = parse_purchases(data)
        assert result == []


class TestAggregateByCard:
    """Tests for aggregate_by_card."""

    def test_empty_list(self):
        assert aggregate_by_card([]) == {}

    def test_single_purchase(self):
        from beertracker.core.models import Purchase

        p = Purchase(
            card_number="1234567890",
            amount=55.0,
            payment_type="IZETTLE_CARD",
            purchase_hash="abc",
        )
        result = aggregate_by_card([p])
        assert result == {"1234567890": 55.0}

    def test_multiple_cards(self):
        from beertracker.core.models import Purchase

        purchases = [
            Purchase("1111111111", 10.0, "IZETTLE_CARD", "a"),
            Purchase("2222222222", 20.0, "IZETTLE_CARD", "b"),
            Purchase("1111111111", 5.0, "IZETTLE_CARD", "c"),
        ]
        result = aggregate_by_card(purchases)
        assert result == {
            "1111111111": 15.0,
            "2222222222": 20.0,
        }
