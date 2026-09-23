"""Journal entry tests — balanced submit, rejections, cancel reversal, amend."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import FiscalYear, GLEntry, JournalEntry, JournalEntryAccount, LedgerAccount


class JournalEntryTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.assets = LedgerAccount.objects.create(
            name="Assets", is_group=True, account_type=LedgerAccount.ASSET, report_type=LedgerAccount.BALANCE_SHEET
        )
        cls.cash = LedgerAccount.objects.create(name="Cash", parent=cls.assets)
        cls.income = LedgerAccount.objects.create(
            name="Income", is_group=True, account_type=LedgerAccount.INCOME, report_type=LedgerAccount.PROFIT_AND_LOSS
        )
        cls.sales = LedgerAccount.objects.create(name="Sales", parent=cls.income)
        cls.expenses = LedgerAccount.objects.create(
            name="Expenses",
            is_group=True,
            account_type=LedgerAccount.EXPENSE,
            report_type=LedgerAccount.PROFIT_AND_LOSS,
        )
        cls.cogs = LedgerAccount.objects.create(name="COGS", parent=cls.expenses)
        cls.year = FiscalYear.objects.create(
            name="2026", year_start_date=date(2026, 1, 1), year_end_date=date(2026, 12, 31)
        )

    def _journal(self, **kwargs):
        defaults = {"voucher_type": JournalEntry.JOURNAL, "posting_date": date(2026, 5, 1)}
        defaults.update(kwargs)
        return JournalEntry.objects.create(**defaults)

    def _row(self, journal, account, debit=None, credit=None, remarks=""):
        return JournalEntryAccount.objects.create(
            journal_entry=journal,
            account=account,
            debit=debit or Decimal("0"),
            credit=credit or Decimal("0"),
            remarks=remarks,
        )


class JournalEntrySubmitTest(JournalEntryTestBase):
    def test_direct_non_draft_create_rejected(self):
        with self.assertRaisesMessage(ValidationError, "Use submit()"):
            JournalEntry.objects.create(
                voucher_type=JournalEntry.JOURNAL, posting_date=date(2026, 5, 1), status=JournalEntry.SUBMITTED
            )

    def test_direct_cancelled_create_rejected(self):
        with self.assertRaisesMessage(ValidationError, "Use submit()"):
            JournalEntry.objects.create(
                voucher_type=JournalEntry.JOURNAL, posting_date=date(2026, 5, 1), status=JournalEntry.CANCELLED
            )

    def test_balanced_submit_posts_gl(self):
        journal = self._journal()
        self._row(journal, self.cash, debit=Decimal("100"))
        self._row(journal, self.sales, credit=Decimal("100"))
        journal.submit()
        journal.refresh_from_db()
        self.assertEqual(journal.status, JournalEntry.SUBMITTED)
        self.assertEqual(journal.total_debit, Decimal("100"))
        self.assertEqual(journal.total_credit, Decimal("100"))
        self.assertEqual(journal.difference, Decimal("0"))
        gl = GLEntry.objects.filter(voucher_type="Journal Entry", voucher_no=str(journal.pk), is_cancelled=False)
        self.assertEqual(gl.count(), 2)
        self.assertEqual(gl.filter(account=self.cash).first().debit, Decimal("100"))

    def test_unbalanced_rejected(self):
        journal = self._journal()
        self._row(journal, self.cash, debit=Decimal("100"))
        self._row(journal, self.sales, credit=Decimal("90"))
        with self.assertRaisesMessage(ValidationError, "must balance"):
            journal.submit()

    def test_mixed_row_rejected(self):
        journal = self._journal()
        with self.assertRaises(ValidationError):
            JournalEntryAccount.objects.create(
                journal_entry=journal, account=self.cash, debit=Decimal("10"), credit=Decimal("10")
            )

    def test_duplicate_account_row_rejected(self):
        journal = self._journal()
        self._row(journal, self.cash, debit=Decimal("50"))
        self._row(journal, self.cash, debit=Decimal("50"))
        self._row(journal, self.sales, credit=Decimal("100"))
        with self.assertRaisesMessage(ValidationError, "Duplicate account rows are not allowed."):
            journal.submit()

    def test_frozen_or_group_account_rejected(self):
        journal = self._journal()
        with self.assertRaises(ValidationError):
            self._row(journal, self.assets, debit=Decimal("100"))
        frozen = LedgerAccount.objects.create(name="Frozen", parent=self.assets, freeze_account=True)
        journal2 = self._journal()
        with self.assertRaises(ValidationError):
            self._row(journal2, frozen, debit=Decimal("100"))

    def test_cancel_posts_reversal_and_marks_originals(self):
        journal = self._journal()
        self._row(journal, self.cash, debit=Decimal("100"))
        self._row(journal, self.sales, credit=Decimal("100"))
        journal.submit()
        journal.cancel()
        journal.refresh_from_db()
        self.assertEqual(journal.status, JournalEntry.CANCELLED)
        originals = GLEntry.objects.filter(voucher_type="Journal Entry", voucher_no=str(journal.pk))
        self.assertEqual(originals.filter(is_cancelled=True).count(), 2)
        self.assertEqual(originals.filter(is_cancelled=False).count(), 2)
        reversal = originals.filter(is_cancelled=False, account=self.cash).first()
        self.assertEqual(reversal.credit, Decimal("100"))
        self.assertEqual(reversal.posting_date, timezone.localdate())

    def test_amend_from_cancelled(self):
        journal = self._journal()
        self._row(journal, self.cash, debit=Decimal("100"))
        self._row(journal, self.sales, credit=Decimal("100"))
        journal.submit()
        journal.cancel()
        copy = journal.amend()
        self.assertEqual(copy.status, JournalEntry.DRAFT)
        self.assertEqual(copy.amended_from, journal)
        self.assertEqual(copy.posting_date, timezone.localdate())
        self.assertEqual(copy.accounts.count(), 2)
        with self.assertRaisesMessage(ValidationError, "Only cancelled"):
            journal2 = self._journal()
            journal2.amend()


class OpeningEntryTest(JournalEntryTestBase):
    def test_opening_submit_sets_is_opening(self):
        journal = self._journal(voucher_type=JournalEntry.OPENING)
        self._row(journal, self.cash, debit=Decimal("5000"), remarks="Cash count")
        self._row(journal, self.sales, credit=Decimal("5000"), remarks="Retained earnings")
        journal.submit()
        journal.refresh_from_db()
        self.assertTrue(journal.is_opening)
        gl = GLEntry.objects.filter(voucher_type="Journal Entry", voucher_no=str(journal.pk))
        self.assertTrue(gl.filter(is_opening=True).exists())

    def test_second_opening_for_same_fiscal_year_rejected(self):
        journal = self._journal(voucher_type=JournalEntry.OPENING)
        self._row(journal, self.cash, debit=Decimal("5000"), remarks="Cash count")
        self._row(journal, self.sales, credit=Decimal("5000"), remarks="Retained earnings")
        journal.submit()

        second = self._journal(voucher_type=JournalEntry.OPENING)
        self._row(second, self.cash, debit=Decimal("100"), remarks="Cash count")
        self._row(second, self.sales, credit=Decimal("100"), remarks="Retained earnings")
        with self.assertRaisesMessage(ValidationError, "already exists for fiscal year"):
            second.submit()

    def test_opening_requires_balanced_rows_with_remarks(self):
        journal = self._journal(voucher_type=JournalEntry.OPENING)
        self._row(journal, self.cash, debit=Decimal("5000"))
        self._row(journal, self.sales, credit=Decimal("5000"))
        with self.assertRaisesMessage(ValidationError, "source or note"):
            journal.submit()
