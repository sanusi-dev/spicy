"""Rounding helper modes: half-even for money, half-up for whole-naira totals."""

from decimal import Decimal

from django.test import SimpleTestCase

from apps.utils.rounding import cash_round, money, percent


class RoundingTest(SimpleTestCase):
    def test_money_rounds_half_even(self):
        self.assertEqual(money(Decimal("0.125")), Decimal("0.12"))
        self.assertEqual(money(Decimal("0.135")), Decimal("0.14"))
        self.assertEqual(money(Decimal("170.085")), Decimal("170.08"))

    def test_cash_round_rounds_half_up(self):
        self.assertEqual(cash_round(Decimal("0.5")), Decimal("1"))
        self.assertEqual(cash_round(Decimal("1.5")), Decimal("2"))
        self.assertEqual(cash_round(Decimal("5500.5")), Decimal("5501"))

    def test_percent_rounds_half_even(self):
        self.assertEqual(percent(Decimal("0.0125")), Decimal("0.012"))
        self.assertEqual(percent(Decimal("0.0135")), Decimal("0.014"))
