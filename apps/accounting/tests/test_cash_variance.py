"""Cash variance posting tests — legs, unconfigured skip, threshold gate, cancel reversal."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import GLEntry, JournalEntry, LedgerAccount
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import Restaurant
from apps.staff.models import OpeningPayment, POSClosingEntry, POSOpeningEntry
from apps.staff.services import ensure_closing_draft, submit_closing_entry
from apps.users.models import CustomUser

from .helpers import setup_chart_of_accounts


class CashVarianceTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user(username="cashier", password="x")
        cls.manager = CustomUser.objects.create_user(username="manager", password="x", is_superuser=True)
        cls.restaurant = Restaurant.objects.create(company="Variance Co")
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        # Point every CASH-type mode at the cash account so variance posting
        # resolves one regardless of which mode it picks.
        for mode in ModeOfPayment.objects.filter(type=ModeOfPayment.TYPE_CASH):
            PaymentGLMapping.objects.get_or_create(
                mode_of_payment=mode, defaults={"default_account": cls.accounts["cash"]}
            )

    def _open_shift(self):
        cash_mode = ModeOfPayment.objects.filter(type=ModeOfPayment.TYPE_CASH).first()
        opening = POSOpeningEntry.objects.create(cashier=self.user)
        OpeningPayment.objects.create(opening_entry=opening, mode_of_payment=cash_mode, opening_amount=Decimal("50000"))
        opening.submit()
        closing = ensure_closing_draft(opening, self.user)
        return closing

    def _set_counted(self, closing, amount):
        cp = closing.closing_payments.first()
        cp.closing_amount = amount
        cp.save()


class ShortagePostingTest(CashVarianceTestBase):
    def test_shortage_posts_dr_shortage_cr_cash(self):
        closing = self._open_shift()
        self._set_counted(closing, Decimal("49800"))
        submit_closing_entry(closing, actor=self.manager)
        closing.refresh_from_db()
        self.assertEqual(closing.total_short_excess, Decimal("-200"))
        self.assertIsNotNone(closing.variance_journal_entry_id)
        je = closing.variance_journal_entry
        self.assertEqual(je.status, JournalEntry.SUBMITTED)
        rows = list(je.accounts.all())
        self.assertEqual(rows[0].account, self.accounts["cogs"])  # shortage account
        self.assertEqual(rows[0].debit, Decimal("200"))
        self.assertEqual(rows[1].account, self.accounts["cash"])
        self.assertEqual(rows[1].credit, Decimal("200"))


class ExcessPostingTest(CashVarianceTestBase):
    def test_excess_posts_dr_cash_cr_over_short(self):
        closing = self._open_shift()
        self._set_counted(closing, Decimal("50200"))
        submit_closing_entry(closing, actor=self.manager)
        closing.refresh_from_db()
        self.assertEqual(closing.total_short_excess, Decimal("200"))
        je = closing.variance_journal_entry
        rows = list(je.accounts.all())
        self.assertEqual(rows[0].account, self.accounts["cash"])
        self.assertEqual(rows[0].debit, Decimal("200"))
        self.assertEqual(rows[1].account, self.accounts["round_off"])  # over-short account
        self.assertEqual(rows[1].credit, Decimal("200"))


class PerDrawerVarianceTest(CashVarianceTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.till = ModeOfPayment.objects.create(name="AAA Till", type=ModeOfPayment.TYPE_CASH)
        cls.safe = ModeOfPayment.objects.create(name="ZZZ Safe", type=ModeOfPayment.TYPE_CASH)
        cls.safe_account = LedgerAccount.objects.create(
            name="Safe Account",
            parent=cls.accounts["assets"],
            account_type=LedgerAccount.ASSET,
            report_type=LedgerAccount.BALANCE_SHEET,
        )
        PaymentGLMapping.objects.create(mode_of_payment=cls.till, default_account=cls.accounts["cash"])
        PaymentGLMapping.objects.create(mode_of_payment=cls.safe, default_account=cls.safe_account)

    def _open_two_drawers(self):
        opening = POSOpeningEntry.objects.create(cashier=self.user)
        OpeningPayment.objects.create(opening_entry=opening, mode_of_payment=self.till, opening_amount=Decimal("50000"))
        OpeningPayment.objects.create(opening_entry=opening, mode_of_payment=self.safe, opening_amount=Decimal("10000"))
        opening.submit()
        return ensure_closing_draft(opening, self.user)

    def test_variance_lands_on_the_drawer_that_has_it(self):
        closing = self._open_two_drawers()
        till_row = closing.closing_payments.get(mode_of_payment=self.till)
        safe_row = closing.closing_payments.get(mode_of_payment=self.safe)
        till_row.closing_amount = Decimal("50000")
        till_row.save()
        safe_row.closing_amount = Decimal("9500")
        safe_row.save()
        submit_closing_entry(closing, actor=self.manager)
        closing.refresh_from_db()
        self.assertEqual(closing.total_short_excess, Decimal("-500"))
        je = closing.variance_journal_entry
        rows = list(je.accounts.all())
        # The SAFE drawer is short — its own account takes the leg, never the till's.
        self.assertEqual([r.account for r in rows], [self.accounts["cogs"], self.safe_account])
        self.assertEqual(rows[0].debit, Decimal("500"))
        self.assertEqual(rows[1].credit, Decimal("500"))
        self.assertFalse(any(r.account == self.accounts["cash"] for r in rows))

    def test_wash_between_drawers_still_posts(self):
        closing = self._open_two_drawers()
        till_row = closing.closing_payments.get(mode_of_payment=self.till)
        safe_row = closing.closing_payments.get(mode_of_payment=self.safe)
        till_row.closing_amount = Decimal("49800")
        till_row.save()
        safe_row.closing_amount = Decimal("10200")
        safe_row.save()
        submit_closing_entry(closing, actor=self.manager)
        closing.refresh_from_db()
        self.assertEqual(closing.total_short_excess, Decimal("0"))
        je = closing.variance_journal_entry
        self.assertIsNotNone(je)
        by_account = {r.account: (r.debit, r.credit) for r in je.accounts.all()}
        self.assertEqual(by_account[self.accounts["cogs"]], (Decimal("200"), Decimal("0")))
        self.assertEqual(by_account[self.accounts["cash"]], (Decimal("0"), Decimal("200")))
        self.assertEqual(by_account[self.safe_account], (Decimal("200"), Decimal("0")))
        self.assertEqual(by_account[self.accounts["round_off"]], (Decimal("0"), Decimal("200")))


class NonCashModeVarianceTest(CashVarianceTestBase):
    def test_bank_shortage_posts_against_bank_mode_account(self):
        bank = ModeOfPayment.objects.create(name="AAA Bank", type=ModeOfPayment.TYPE_BANK)
        PaymentGLMapping.objects.create(mode_of_payment=bank, default_account=self.accounts["bank"])
        cash_mode = ModeOfPayment.objects.filter(type=ModeOfPayment.TYPE_CASH).first()
        opening = POSOpeningEntry.objects.create(cashier=self.user)
        OpeningPayment.objects.create(opening_entry=opening, mode_of_payment=cash_mode, opening_amount=Decimal("50000"))
        OpeningPayment.objects.create(opening_entry=opening, mode_of_payment=bank, opening_amount=Decimal("1000"))
        opening.submit()
        closing = ensure_closing_draft(opening, self.user)
        bank_row = closing.closing_payments.get(mode_of_payment=bank)
        bank_row.closing_amount = Decimal("800")
        bank_row.save()
        submit_closing_entry(closing, actor=self.manager)
        closing.refresh_from_db()
        self.assertEqual(closing.total_short_excess, Decimal("-200"))
        je = closing.variance_journal_entry
        rows = list(je.accounts.all())
        # The bank mode's own account takes the shortage — no cash drawer leg.
        self.assertEqual([r.account for r in rows], [self.accounts["cogs"], self.accounts["bank"]])
        self.assertEqual(rows[1].credit, Decimal("200"))


class UnconfiguredAccountTest(CashVarianceTestBase):
    def test_skip_posting_when_account_missing(self):
        self.restaurant.cash_shortage_account = None
        self.restaurant.save()
        closing = self._open_shift()
        self._set_counted(closing, Decimal("49800"))
        submit_closing_entry(closing, actor=self.manager)
        closing.refresh_from_db()
        self.assertIsNone(closing.variance_journal_entry_id)
        self.assertEqual(closing.total_short_excess, Decimal("-200"))


class ThresholdGateTest(CashVarianceTestBase):
    def test_threshold_exceeded_without_note_rejected(self):
        self.restaurant.variance_approval_threshold = Decimal("100")
        self.restaurant.save()
        closing = self._open_shift()
        self._set_counted(closing, Decimal("49000"))  # 1000 short > 100
        with self.assertRaisesMessage(ValidationError, "approval threshold"):
            submit_closing_entry(closing, actor=self.manager)
        closing.refresh_from_db()
        self.assertEqual(closing.status, POSClosingEntry.DRAFT)

    def test_threshold_exceeded_with_note_and_manager_ok(self):
        self.restaurant.variance_approval_threshold = Decimal("100")
        self.restaurant.save()
        closing = self._open_shift()
        self._set_counted(closing, Decimal("49000"))
        closing.variance_note = "Cashier miscounted; corrected."
        closing.save(update_fields=["variance_note", "updated_at"])
        submit_closing_entry(closing, actor=self.manager)
        closing.refresh_from_db()
        self.assertEqual(closing.status, POSClosingEntry.SUBMITTED)

    def test_threshold_exceeded_without_manager_role_rejected(self):
        self.restaurant.variance_approval_threshold = Decimal("100")
        self.restaurant.save()
        closing = self._open_shift()
        self._set_counted(closing, Decimal("49000"))
        closing.variance_note = "note"
        closing.save(update_fields=["variance_note", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "approval threshold"):
            submit_closing_entry(closing, actor=self.user)
        closing.refresh_from_db()
        self.assertEqual(closing.status, POSClosingEntry.DRAFT)


class VarianceCancelTest(CashVarianceTestBase):
    def test_cancel_reverses_variance_je(self):
        closing = self._open_shift()
        self._set_counted(closing, Decimal("49800"))
        submit_closing_entry(closing, actor=self.manager)
        closing.refresh_from_db()
        je = closing.variance_journal_entry
        journal_no = str(je.pk)
        closing.cancel(by_user=self.manager)
        je.refresh_from_db()
        self.assertEqual(je.status, JournalEntry.CANCELLED)
        gl_rows = GLEntry.objects.filter(voucher_type="Journal Entry", voucher_no=journal_no)
        self.assertEqual(gl_rows.filter(is_cancelled=True).count(), 2)
        self.assertEqual(gl_rows.filter(is_cancelled=False).count(), 2)
