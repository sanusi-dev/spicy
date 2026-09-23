"""GL running balance, trial balance, and simple P&L."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounting.models import GLEntry, LedgerAccount
from apps.reports.accounting_reports import gl_report, simple_pnl, trial_balance
from apps.settings.models import ProductionUnit
from apps.staff.models import ShiftCashOut

from .helpers import DailyPnLTestMixin


class AccountingReportTest(DailyPnLTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._setup_pnl_world()
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.manager.groups.add(mgr)
        cls.day = date.today()
        cls.fy = cls.accounts["fiscal_year"]

    def _post(self, rows, *, voucher_no, voucher_type="Journal Entry", posting_date=None):
        return GLEntry.post(
            posting_date=posting_date or self.day,
            rows=rows,
            voucher_type=voucher_type,
            voucher_no=voucher_no,
        )

    def _cancel_and_reverse(self, entries, *, voucher_no):
        for entry in entries:
            entry.is_cancelled = True
            entry.save(update_fields=["is_cancelled", "updated_at"])
        reversal = [
            {
                "account": entry.account,
                "debit": entry.credit,
                "credit": entry.debit,
            }
            for entry in entries
        ]
        return GLEntry.post(
            posting_date=entries[0].posting_date,
            rows=reversal,
            voucher_type=entries[0].voucher_type,
            voucher_no=voucher_no,
            remarks="Reversal",
        )

    def test_gl_running_balance_nets_cancelled_and_reversal_to_zero(self):
        posted = self._post(
            [
                {"account": self.accounts["cash"], "debit": Decimal("1000")},
                {"account": self.accounts["food_sales"], "credit": Decimal("1000")},
            ],
            voucher_no="10",
        )
        self._cancel_and_reverse(posted, voucher_no="10")
        rows, totals = gl_report(
            fiscal_year=self.fy,
            date_from=self.day,
            date_to=self.day,
            account_id=self.accounts["cash"].pk,
        )
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0]["is_brought_forward"])
        self.assertTrue(any(row["is_cancelled"] for row in rows[1:]))
        self.assertEqual(rows[-1]["running_balance"], Decimal("0.00"))
        self.assertEqual(totals["debit"], totals["credit"])
        self.assertEqual(totals["running_balance"], Decimal("0.00"))

    def test_gl_without_account_hides_running_balance(self):
        self._post(
            [
                {"account": self.accounts["cash"], "debit": Decimal("250")},
                {"account": self.accounts["food_sales"], "credit": Decimal("250")},
            ],
            voucher_no="11",
        )
        rows, totals = gl_report(fiscal_year=self.fy, date_from=self.day, date_to=self.day)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[-1]["running_balance"])
        self.assertIsNone(totals["running_balance"])

    def test_gl_brought_forward_seeds_account_running_balance(self):
        opening_day = self.fy.year_start_date
        self._post(
            [
                {"account": self.accounts["cash"], "debit": Decimal("700")},
                {"account": self.accounts["food_sales"], "credit": Decimal("700")},
            ],
            voucher_no="12",
            posting_date=opening_day,
        )
        query_day = opening_day + timedelta(days=1)
        rows, totals = gl_report(
            fiscal_year=self.fy,
            date_from=query_day,
            date_to=query_day,
            account_id=self.accounts["cash"].pk,
        )
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["is_brought_forward"])
        self.assertEqual(rows[0]["running_balance"], Decimal("700.00"))
        self.assertEqual(totals["running_balance"], Decimal("700.00"))

    def test_trial_balance_omits_zero_and_stays_balanced(self):
        self._post(
            [
                {"account": self.accounts["cash"], "debit": Decimal("2500")},
                {"account": self.accounts["food_sales"], "credit": Decimal("2500")},
            ],
            voucher_no="20",
        )
        cancelled = self._post(
            [
                {"account": self.accounts["cash"], "debit": Decimal("100")},
                {"account": self.accounts["drinks_sales"], "credit": Decimal("100")},
            ],
            voucher_no="21",
        )
        self._cancel_and_reverse(cancelled, voucher_no="21")
        groups, totals = trial_balance(fiscal_year=self.fy, date_to=self.day)
        names = [row["account_name"] for group in groups for row in group["rows"]]
        self.assertIn(self.accounts["cash"].name, names)
        self.assertIn(self.accounts["food_sales"].name, names)
        self.assertNotIn(self.accounts["drinks_sales"].name, names)
        self.assertEqual(totals["debit"], totals["credit"])
        cash = next(row for group in groups for row in group["rows"] if row["account_id"] == self.accounts["cash"].pk)
        self.assertEqual(cash["balance"], Decimal("2500.00"))
        self.assertEqual(cash["debit"] - cash["credit"], Decimal("2500.00"))

    def test_trial_balance_includes_opening_entries(self):
        GLEntry.objects.create(
            posting_date=self.fy.year_start_date,
            account=self.accounts["cash"],
            debit=Decimal("8000"),
            credit=Decimal("0"),
            voucher_type="Journal Entry",
            voucher_no="open",
            fiscal_year=self.fy,
            is_opening=True,
        )
        GLEntry.objects.create(
            posting_date=self.fy.year_start_date,
            account=self.accounts["owner_equity"],
            debit=Decimal("0"),
            credit=Decimal("8000"),
            voucher_type="Journal Entry",
            voucher_no="open",
            fiscal_year=self.fy,
            is_opening=True,
        )
        groups, totals = trial_balance(fiscal_year=self.fy, date_to=self.fy.year_start_date)
        equity = next(
            row for group in groups for row in group["rows"] if row["account_id"] == self.accounts["owner_equity"].pk
        )
        self.assertEqual(equity["credit"], Decimal("8000.00"))
        self.assertEqual(totals["debit"], totals["credit"])

    def test_simple_pnl_income_minus_expense_with_food_drinks_split(self):
        self._post(
            [
                {"account": self.accounts["cash"], "debit": Decimal("2000")},
                {"account": self.accounts["food_sales"], "credit": Decimal("1500")},
                {"account": self.accounts["drinks_sales"], "credit": Decimal("500")},
            ],
            voucher_no="30",
        )
        self._post(
            [
                {"account": self.accounts["cogs"], "debit": Decimal("400")},
                {"account": self.accounts["cash"], "credit": Decimal("400")},
            ],
            voucher_no="31",
        )
        statement = simple_pnl(fiscal_year=self.fy, date_from=self.day, date_to=self.day)
        self.assertEqual(statement["food_sales"], Decimal("1500.00"))
        self.assertEqual(statement["drinks_sales"], Decimal("500.00"))
        self.assertEqual(statement["total_income"], Decimal("2000.00"))
        self.assertEqual(statement["gross_profit"], Decimal("2000.00"))
        self.assertEqual(statement["total_expenses"], Decimal("400.00"))
        self.assertEqual(statement["net_profit"], Decimal("1600.00"))

    def test_simple_pnl_nets_cancelled_reversals(self):
        posted = self._post(
            [
                {"account": self.accounts["cash"], "debit": Decimal("900")},
                {"account": self.accounts["food_sales"], "credit": Decimal("900")},
            ],
            voucher_no="40",
        )
        self._cancel_and_reverse(posted, voucher_no="40")
        statement = simple_pnl(fiscal_year=self.fy, date_from=self.day, date_to=self.day)
        self.assertEqual(statement["food_sales"], Decimal("0.00"))
        self.assertEqual(statement["net_profit"], Decimal("0.00"))

    def test_simple_pnl_default_income_account_splits_single_missing_department(self):
        default_income = LedgerAccount.objects.create(
            name=f"Default Sales {self.restaurant.pk}",
            parent=self.accounts["income"],
            account_type=LedgerAccount.INCOME,
            report_type=LedgerAccount.PROFIT_AND_LOSS,
        )
        self.restaurant.default_income_account = default_income
        self.restaurant.save(update_fields=["default_income_account"])
        ProductionUnit.objects.filter(department="DRINKS").delete()
        self._post(
            [
                {"account": self.accounts["cash"], "debit": Decimal("2000")},
                {"account": self.accounts["food_sales"], "credit": Decimal("1500")},
                {"account": default_income, "credit": Decimal("500")},
            ],
            voucher_no="60",
        )
        statement = simple_pnl(fiscal_year=self.fy, date_from=self.day, date_to=self.day)
        self.assertEqual(statement["food_sales"], Decimal("1500.00"))
        self.assertEqual(statement["drinks_sales"], Decimal("500.00"))
        self.assertEqual(statement["total_income"], Decimal("2000.00"))

    def test_simple_pnl_shared_default_income_stays_generic(self):
        default_income = LedgerAccount.objects.create(
            name=f"Shared Sales {self.restaurant.pk}",
            parent=self.accounts["income"],
            account_type=LedgerAccount.INCOME,
            report_type=LedgerAccount.PROFIT_AND_LOSS,
        )
        self.restaurant.default_income_account = default_income
        self.restaurant.save(update_fields=["default_income_account"])
        ProductionUnit.objects.filter(department__in=("FOOD", "DRINKS")).delete()
        self._post(
            [
                {"account": self.accounts["cash"], "debit": Decimal("500")},
                {"account": default_income, "credit": Decimal("500")},
            ],
            voucher_no="61",
        )
        statement = simple_pnl(fiscal_year=self.fy, date_from=self.day, date_to=self.day)
        self.assertEqual(statement["food_sales"], Decimal("0.00"))
        self.assertEqual(statement["drinks_sales"], Decimal("0.00"))
        self.assertEqual(statement["total_income"], Decimal("500.00"))

    def _shift_cash_out(self):
        return ShiftCashOut.objects.create(
            opening_entry=self.opening,
            mode_of_payment=self.cash,
            amount=Decimal("200"),
            reason=ShiftCashOut.TRANSPORT,
            recorded_by=self.manager,
        )

    def test_gl_rows_link_shift_cash_out(self):
        cash_out = self._shift_cash_out()
        GLEntry.objects.create(
            posting_date=self.day,
            account=self.accounts["cash"],
            debit=Decimal("0"),
            credit=Decimal("200"),
            voucher_type="Shift Cash-Out",
            voucher_no=str(cash_out.pk),
            fiscal_year=self.fy,
        )
        rows, _ = gl_report(fiscal_year=self.fy, date_from=self.day, date_to=self.day)
        row = next(row for row in rows if row["voucher_type"] == "Shift Cash-Out")
        self.assertEqual(
            row["voucher_url"],
            reverse("staff:opening_entry_detail", args=[self.opening.pk]),
        )

    def test_manager_pages_render(self):
        self._post(
            [
                {"account": self.accounts["cash"], "debit": Decimal("100")},
                {"account": self.accounts["food_sales"], "credit": Decimal("100")},
            ],
            voucher_no="50",
        )
        self.client.force_login(self.user)
        self.assertIn(self.client.get(reverse("reports:gl_report")).status_code, (302, 403))
        self.client.force_login(self.manager)
        for name in ("reports:gl_report", "reports:trial_balance", "reports:simple_pnl"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
