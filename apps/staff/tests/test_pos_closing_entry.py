from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.tests.helpers import setup_chart_of_accounts
from apps.orders.models import Order
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import Restaurant
from apps.staff.models import ClosingPayment, OpeningPayment, POSClosingEntry, POSOpeningEntry
from apps.staff.services import submit_closing_entry
from apps.users.models import CustomUser

from ..forms import ClosingPaymentForm


class POSClosingEntryTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user(
            username="cashier@test.com", password="testpass123", email="cashier@test.com"
        )
        cls.restaurant = Restaurant.objects.create(company="Closing Co")
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        cls.cash_mode = ModeOfPayment.objects.create(name="Test Cash", type="CASH")
        cls.bank_mode = ModeOfPayment.objects.create(name="Test Bank", type="BANK")
        PaymentGLMapping.objects.create(mode_of_payment=cls.cash_mode, default_account=cls.accounts["cash"])
        PaymentGLMapping.objects.create(mode_of_payment=cls.bank_mode, default_account=cls.accounts["bank"])
        cls.opening = POSOpeningEntry.objects.create(
            cashier=cls.user,
            posting_date="2026-07-24",
        )
        OpeningPayment.objects.create(
            opening_entry=cls.opening,
            mode_of_payment=cls.cash_mode,
            opening_amount=Decimal("50000"),
        )
        OpeningPayment.objects.create(
            opening_entry=cls.opening,
            mode_of_payment=cls.bank_mode,
            opening_amount=Decimal("0"),
        )
        cls.opening.submit()
        cls.closing = POSClosingEntry.objects.create(
            opening_entry=cls.opening,
            cashier=cls.user,
        )
        for op in cls.opening.opening_payments.all():
            ClosingPayment.objects.create(
                closing_entry=cls.closing,
                mode_of_payment=op.mode_of_payment,
                opening_amount=op.opening_amount,
                expected_amount=op.opening_amount,
                closing_amount=Decimal("0"),
                difference=Decimal("0"),
            )


class POSClosingEntryModelTest(POSClosingEntryTestBase):
    def test_save_auto_fills_from_opening(self):
        # Close the first shift so the global one-open-shift rule allows a second opening.
        submit_closing_entry(self.closing)
        new_opening = POSOpeningEntry.objects.create(cashier=self.user, posting_date="2026-07-25")
        OpeningPayment.objects.create(
            opening_entry=new_opening,
            mode_of_payment=self.cash_mode,
            opening_amount=Decimal("1000"),
        )
        new_opening.submit()
        new_closing = POSClosingEntry(opening_entry=new_opening)
        new_closing.save()
        self.assertEqual(new_closing.cashier, self.user)
        self.assertIsNotNone(new_closing.period_start_date)
        self.assertEqual(new_closing.period_start_date, new_opening.period_start_date)

    def test_clean_rejects_draft_opening(self):
        submit_closing_entry(self.closing)
        new_opening = POSOpeningEntry.objects.create(cashier=self.user, posting_date="2026-07-25")
        new_closing = POSClosingEntry(opening_entry=new_opening)
        with self.assertRaises(ValidationError) as ctx:
            new_closing.full_clean()
        self.assertIn("opening_entry", ctx.exception.message_dict)

    def test_submit_computes_expected_and_difference(self):
        # Set the cash closing_amount to 49800 (200 short)
        cash_closing = self.closing.closing_payments.get(mode_of_payment=self.cash_mode)
        cash_closing.closing_amount = Decimal("49800")
        cash_closing.save()
        submit_closing_entry(self.closing)
        cash_closing.refresh_from_db()
        self.assertEqual(cash_closing.expected_amount, Decimal("50000"))
        self.assertEqual(cash_closing.difference, Decimal("-200"))
        self.assertEqual(self.closing.status, POSClosingEntry.SUBMITTED)
        self.assertEqual(self.closing.total_short_excess, Decimal("-200"))
        self.opening.refresh_from_db()
        self.assertTrue(self.opening.is_closed)
        self.assertEqual(self.opening.closing_entry, self.closing)
        self.assertIsNotNone(self.opening.period_end_date)

    def test_submit_rejects_open_draft_orders(self):
        Order.objects.create(opening_entry=self.opening)

        with self.assertRaisesMessage(ValidationError, "Close or settle 1 open order"):
            submit_closing_entry(self.closing)

        self.closing.refresh_from_db()
        self.assertEqual(self.closing.status, POSClosingEntry.DRAFT)

    def test_submit_rejects_non_cash_counted_above_expected(self):
        # Netting attack: 9500 pocketed from cash, fake bank surplus nets the shortage to zero.
        cash_closing = self.closing.closing_payments.get(mode_of_payment=self.cash_mode)
        cash_closing.closing_amount = Decimal("40500")
        cash_closing.save()
        bank_closing = self.closing.closing_payments.get(mode_of_payment=self.bank_mode)
        bank_closing.closing_amount = Decimal("9500")
        bank_closing.save()

        with self.assertRaisesMessage(ValidationError, "Counted Test Bank amount is above the expected"):
            submit_closing_entry(self.closing)

        self.closing.refresh_from_db()
        self.assertEqual(self.closing.status, POSClosingEntry.DRAFT)
        self.opening.refresh_from_db()
        self.assertTrue(self.opening.is_open)

    def test_submit_allows_cash_counted_above_expected(self):
        cash_closing = self.closing.closing_payments.get(mode_of_payment=self.cash_mode)
        cash_closing.closing_amount = Decimal("50100")
        cash_closing.save()

        submit_closing_entry(self.closing)

        cash_closing.refresh_from_db()
        self.assertEqual(cash_closing.difference, Decimal("100"))
        self.assertEqual(self.closing.status, POSClosingEntry.SUBMITTED)

    def test_other_cashier_cannot_close_shift(self):
        other = CustomUser.objects.create_user(username="other@test.com", password="testpass123")

        with self.assertRaisesMessage(ValidationError, "Only the cashier who opened this shift"):
            submit_closing_entry(self.closing, actor=other)

        self.closing.refresh_from_db()
        self.assertEqual(self.closing.status, POSClosingEntry.DRAFT)

    def test_opener_can_close_own_shift(self):
        submit_closing_entry(self.closing, actor=self.user)
        self.closing.refresh_from_db()
        self.assertEqual(self.closing.status, POSClosingEntry.SUBMITTED)

    def test_manager_can_close_another_cashiers_shift(self):
        manager = CustomUser.objects.create_user(username="manager@test.com", password="testpass123")
        manager_group, _ = Group.objects.get_or_create(name="Spicy Manager")
        manager.groups.add(manager_group)

        submit_closing_entry(self.closing, actor=manager)

        self.closing.refresh_from_db()
        self.assertEqual(self.closing.status, POSClosingEntry.SUBMITTED)

    def test_submit_raises_if_mode_not_in_opening(self):
        other_mode = ModeOfPayment.objects.create(name="Stranger", type="GENERAL")
        ClosingPayment.objects.create(
            closing_entry=self.closing,
            mode_of_payment=other_mode,
            opening_amount=Decimal("0"),
            expected_amount=Decimal("0"),
            closing_amount=Decimal("100"),
        )
        with self.assertRaises(ValidationError) as ctx:
            submit_closing_entry(self.closing)
        self.assertIn("mode_of_payment", ctx.exception.message_dict)
        self.closing.refresh_from_db()
        self.assertEqual(self.closing.status, POSClosingEntry.DRAFT)

    def test_submit_idempotent(self):
        submit_closing_entry(self.closing)
        prev_status = self.closing.status
        submit_closing_entry(self.closing)
        self.closing.refresh_from_db()
        self.assertEqual(self.closing.status, prev_status)

    def test_cancel_blocks_if_new_open_shift(self):
        cash_closing = self.closing.closing_payments.get(mode_of_payment=self.cash_mode)
        cash_closing.closing_amount = Decimal("50000")
        cash_closing.save()
        submit_closing_entry(self.closing)
        new_opening = POSOpeningEntry.objects.create(cashier=self.user, posting_date="2026-07-25")
        OpeningPayment.objects.create(
            opening_entry=new_opening,
            mode_of_payment=self.cash_mode,
            opening_amount=Decimal("0"),
        )
        new_opening.submit()
        with self.assertRaises(ValidationError):
            self.closing.cancel(by_user=self.user)

    def test_cancel_succeeds_when_no_new_open_shift(self):
        cash_closing = self.closing.closing_payments.get(mode_of_payment=self.cash_mode)
        cash_closing.closing_amount = Decimal("50000")
        cash_closing.save()
        submit_closing_entry(self.closing)
        self.closing.cancel(by_user=self.user)
        self.closing.refresh_from_db()
        self.assertEqual(self.closing.status, POSClosingEntry.CANCELLED)
        # The opening entry is NOT reopened
        self.opening.refresh_from_db()
        self.assertTrue(self.opening.is_closed)
        self.assertEqual(self.opening.closing_entry, self.closing)


class ClosingPaymentModelTest(POSClosingEntryTestBase):
    def test_clean_rejects_undeclared_mode(self):
        other_mode = ModeOfPayment.objects.create(name="Stranger", type="GENERAL")
        cp = ClosingPayment(
            closing_entry=self.closing,
            mode_of_payment=other_mode,
            opening_amount=Decimal("0"),
            expected_amount=Decimal("0"),
            closing_amount=Decimal("0"),
        )
        with self.assertRaises(ValidationError):
            cp.full_clean()

    def test_clean_rejects_negative_closing_amount(self):
        cp = self.closing.closing_payments.get(mode_of_payment=self.cash_mode)
        cp.closing_amount = Decimal("-5000")

        with self.assertRaises(ValidationError) as ctx:
            cp.full_clean()

        self.assertIn("closing_amount", ctx.exception.message_dict)


class ClosingPaymentFormTest(POSClosingEntryTestBase):
    def test_form_rejects_negative_closing_amount(self):
        cp = self.closing.closing_payments.get(mode_of_payment=self.cash_mode)

        form = ClosingPaymentForm({"closing_amount": "-5000"}, instance=cp)

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["closing_amount"], ["Counted amounts can't be negative."])

    def test_form_accepts_zero_closing_amount(self):
        cp = self.closing.closing_payments.get(mode_of_payment=self.cash_mode)

        form = ClosingPaymentForm({"closing_amount": "0"}, instance=cp)

        self.assertTrue(form.is_valid())
