from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.tests.helpers import setup_chart_of_accounts
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import Restaurant
from apps.staff.models import OpeningPayment, POSOpeningEntry
from apps.staff.services import submit_closing_entry
from apps.users.models import CustomUser


class POSOpeningEntryTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user(
            username="cashier@test.com", password="testpass123", email="cashier@test.com"
        )
        cls.restaurant = Restaurant.objects.create(company="Opening Co")
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        cls.cash_mode = ModeOfPayment.objects.create(name="Test Cash", type="CASH")
        cls.bank_mode = ModeOfPayment.objects.create(name="Test Bank", type="BANK")
        PaymentGLMapping.objects.create(mode_of_payment=cls.cash_mode, default_account=cls.accounts["cash"])
        PaymentGLMapping.objects.create(mode_of_payment=cls.bank_mode, default_account=cls.accounts["bank"])
        cls.entry = POSOpeningEntry.objects.create(
            cashier=cls.user,
            posting_date="2026-07-24",
        )
        OpeningPayment.objects.create(
            opening_entry=cls.entry, mode_of_payment=cls.cash_mode, opening_amount=Decimal("50000")
        )
        OpeningPayment.objects.create(
            opening_entry=cls.entry, mode_of_payment=cls.bank_mode, opening_amount=Decimal("0")
        )


class POSOpeningEntryModelTest(POSOpeningEntryTestBase):
    def test_submit_rejects_second_cash_mode(self):
        second_cash = ModeOfPayment.objects.create(name="Test Safe", type="CASH")
        PaymentGLMapping.objects.create(mode_of_payment=second_cash, default_account=self.accounts["cash"])
        OpeningPayment.objects.create(
            opening_entry=self.entry, mode_of_payment=second_cash, opening_amount=Decimal("1000")
        )
        with self.assertRaisesMessage(ValidationError, "at most one cash payment mode"):
            self.entry.submit()
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, POSOpeningEntry.DRAFT)

    def test_submit_flips_status(self):
        self.entry.submit()
        self.assertEqual(self.entry.status, POSOpeningEntry.SUBMITTED)
        self.assertTrue(self.entry.is_open)
        self.assertFalse(self.entry.is_closed)

    def test_submit_idempotent_when_already_submitted(self):
        self.entry.submit()
        self.entry.submit()
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, POSOpeningEntry.SUBMITTED)

    def test_submit_does_nothing_when_cancelled(self):
        self.entry.status = POSOpeningEntry.CANCELLED
        self.entry.save()
        self.entry.submit()
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, POSOpeningEntry.CANCELLED)

    def test_cancel_flips_to_cancelled(self):
        self.entry.cancel(by_user=self.user)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, POSOpeningEntry.CANCELLED)
        self.assertEqual(self.entry.cancelled_by, self.user)
        self.assertIsNotNone(self.entry.cancelled_at)

    def test_cancel_idempotent_when_already_cancelled(self):
        self.entry.cancel(by_user=self.user)
        cancelled_at = self.entry.cancelled_at
        self.entry.cancel(by_user=self.user)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.cancelled_at, cancelled_at)

    def test_is_closed_when_closing_entry_set(self):
        self.entry.submit()
        from apps.staff.models import ClosingPayment, POSClosingEntry

        closing = POSClosingEntry.objects.create(
            opening_entry=self.entry,
            cashier=self.user,
        )
        for op in self.entry.opening_payments.all():
            ClosingPayment.objects.create(
                closing_entry=closing,
                mode_of_payment=op.mode_of_payment,
                opening_amount=op.opening_amount,
                expected_amount=op.opening_amount,
                closing_amount=op.opening_amount,
            )
        submit_closing_entry(closing)
        self.entry.refresh_from_db()
        self.assertTrue(self.entry.is_closed)
        self.assertIsNotNone(self.entry.closing_entry)

    def test_cannot_have_two_open_shifts(self):
        """Regression: clean() must also fire on DRAFT — full_clean() runs before submit() flips the status."""
        self.entry.submit()
        new_entry = POSOpeningEntry(
            cashier=self.user,
            posting_date="2026-07-24",
        )
        with self.assertRaises(ValidationError):
            new_entry.full_clean()

    def test_submit_blocks_second_open_shift(self):
        """Regression: submit() must re-check the unique-Open rule inside the transaction."""
        self.entry.submit()
        new_entry = POSOpeningEntry.objects.create(
            cashier=self.user,
            posting_date="2026-07-24",
        )
        with self.assertRaises(ValidationError):
            new_entry.submit()
        new_entry.refresh_from_db()
        self.assertEqual(new_entry.status, POSOpeningEntry.DRAFT)
        self.assertEqual(
            POSOpeningEntry.objects.filter(status=POSOpeningEntry.SUBMITTED, closing_entry__isnull=True).count(),
            1,
        )


class OpeningPaymentModelTest(POSOpeningEntryTestBase):
    def test_unique_mode_per_entry(self):
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            OpeningPayment.objects.create(
                opening_entry=self.entry,
                mode_of_payment=self.cash_mode,
                opening_amount=Decimal("0"),
            )
