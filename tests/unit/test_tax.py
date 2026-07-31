from decimal import Decimal

import pytest

from larder.services.tax import _sales_rate, calculate_price


def test_calculate_price_uses_currency_half_up_rounding() -> None:
    subtotal, tax, total = calculate_price("4.99", "0.08875")

    assert subtotal == Decimal("4.99")
    assert tax == Decimal("0.44")
    assert total == Decimal("5.43")


def test_calculate_price_supports_explicit_zero_tax() -> None:
    assert calculate_price("12.50", 0) == (
        Decimal("12.50"),
        Decimal("0.00"),
        Decimal("12.50"),
    )


def test_sales_rate_reads_ziptax_sales_summary() -> None:
    payload = {
        "metadata": {"response": {"code": 100}},
        "taxSummaries": [
            {"taxType": "USE_TAX", "rate": 0.09},
            {"taxType": "SALES_TAX", "rate": 0.08875},
        ],
    }

    assert _sales_rate(payload) == Decimal("0.088750")


def test_sales_rate_rejects_provider_error() -> None:
    with pytest.raises(ValueError, match="plan does not include product rates"):
        _sales_rate(
            {
                "metadata": {
                    "response": {
                        "code": 113,
                        "message": "plan does not include product rates",
                    }
                }
            }
        )
