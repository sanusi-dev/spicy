"""Explicit rounding modes for money, cash totals, and percentages."""

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

TWO_PLACES = Decimal("0.01")
THREE_PLACES = Decimal("0.001")
WHOLE = Decimal("1")


def money(value: Decimal) -> Decimal:
    """Round a naira amount to 2 dp using half-even."""
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)


def cash_round(value: Decimal) -> Decimal:
    """Round a naira total to the nearest whole naira using half-up."""
    return Decimal(value).quantize(WHOLE, rounding=ROUND_HALF_UP)


def percent(value: Decimal) -> Decimal:
    """Round a percentage to 3 dp using half-even."""
    return Decimal(value).quantize(THREE_PLACES, rounding=ROUND_HALF_EVEN)
