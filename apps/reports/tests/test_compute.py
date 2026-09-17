"""Daily P&L computation — window, sales, COGS, memos, templates, submit snapshot."""

from datetime import date, time, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import GLEntry
from apps.inventory.models import (
    Bin,
    Recipe,
    RecipeItem,
    StockLedgerEntry,
    StockReconciliation,
    StockReconciliationItem,
)
from apps.inventory.services import submit_stock_reconciliation
from apps.orders.services import add_order_line, make_return, submit_return, update_return_line
from apps.reports.models import DailyPnL, DailyPnLLine, PnLMaterial, PnLRecurringExpense
from apps.reports.services import business_day_window, compute_daily_pnl
from apps.staff.models import ClosingPayment, POSClosingEntry
from apps.staff.services import submit_closing_entry

from .helpers import DailyPnLTestMixin


class WindowTest(DailyPnLTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._setup_pnl_world()

    def test_start_hour_6_puts_0100_on_previous_business_date(self):
        from datetime import datetime as dt

        start, end = business_day_window(date(2026, 8, 19), 6)
        one_am = timezone.make_aware(dt(2026, 8, 20, 1, 0))
        self.assertTrue(start <= one_am < end)
        next_start, _ = business_day_window(date(2026, 8, 20), 6)
        self.assertFalse(one_am >= next_start)

    def test_sales_follow_order_posting_datetime(self):
        self.config.business_day_start_hour = 6
        self.config.save()
        order = self._create_order(posting_date=date.today() + timedelta(days=1), posting_time=time(1, 0))
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        pnl = self._draft(business_date=date.today())
        computation = compute_daily_pnl(pnl)
        self.assertEqual(computation.totals["gross_sales_food"], Decimal("1500"))


class SalesAndCogsTest(DailyPnLTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._setup_pnl_world()

    def test_food_and_drinks_sales_split(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        add_order_line(order, self.drink, qty=1, rate=Decimal("500"), menu_item=self.drink_mi)
        self._settle(order)
        computation = compute_daily_pnl(self._draft())
        self.assertEqual(computation.totals["gross_sales_food"], Decimal("1500"))
        self.assertEqual(computation.totals["gross_sales_drinks"], Decimal("500"))
        self.assertEqual(computation.totals["gross_sales"], Decimal("2000"))

    def test_draft_orders_ignored(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        computation = compute_daily_pnl(self._draft())
        self.assertEqual(computation.totals["gross_sales"], Decimal("0"))
        self.assertEqual(order.status, "DRAFT")

    def test_food_contributes_zero_cogs(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        computation = compute_daily_pnl(self._draft())
        self.assertEqual(computation.totals["cogs"], Decimal("0"))

    def test_wastage_return_cogs_uses_settle_time_rate(self):
        # Sale at WAC 100, then a receipt lifts WAC to 140 — a wastage return of the
        # first sale values at its settle-time 100 (the GL rate), not the bin's 140.
        Bin.objects.filter(item=self.drink, warehouse=self.bar_wh).update(actual_qty=0, valuation_rate=0)
        StockLedgerEntry.create_entry(
            item=self.drink,
            warehouse=self.bar_wh,
            quantity=Decimal("1"),
            voucher_type="Purchase Receipt",
            voucher_no="PR-W1",
            unit_rate=Decimal("100"),
        )
        sale = self._create_order()
        add_order_line(sale, self.drink, qty=1, rate=Decimal("500"), menu_item=self.drink_mi)
        self._settle(sale)
        StockLedgerEntry.create_entry(
            item=self.drink,
            warehouse=self.bar_wh,
            quantity=Decimal("1"),
            voucher_type="Purchase Receipt",
            voucher_no="PR-W2",
            unit_rate=Decimal("140"),
        )
        other = self._create_order()
        add_order_line(other, self.drink, qty=1, rate=Decimal("500"), menu_item=self.drink_mi)
        self._settle(other)
        ret = make_return(sale)
        ret.recalculate_totals()
        update_return_line(ret, ret.items.first().pk, not_restockable=True)
        submit_return(ret, actor=self.user)
        computation = compute_daily_pnl(self._draft())
        wastage = [row for row in computation.cogs_rows if row["kind"] == "WASTAGE"]
        self.assertEqual(len(wastage), 1)
        self.assertEqual(wastage[0]["rate"], Decimal("100"))
        self.assertEqual(wastage[0]["amount"], Decimal("100"))

    def test_drink_cogs_from_wac(self):
        # Reset WAC to known state — helper leaves 100 @ 0 which would dilute.
        from apps.inventory.models import Bin

        Bin.objects.filter(item=self.drink, warehouse=self.bar_wh).update(actual_qty=0, valuation_rate=0)
        StockLedgerEntry.create_entry(
            item=self.drink,
            warehouse=self.bar_wh,
            quantity=Decimal("100"),
            voucher_type="Purchase Receipt",
            voucher_no="PR-1",
            unit_rate=Decimal("300"),
        )
        order = self._create_order()
        add_order_line(order, self.drink, qty=2, rate=Decimal("500"), menu_item=self.drink_mi)
        self._settle(order)
        computation = compute_daily_pnl(self._draft())
        self.assertEqual(computation.totals["cogs"], Decimal("600"))
        self.assertTrue(any(row["kind"] == "SALE" for row in computation.cogs_rows))

    def test_food_actual_is_cogs_and_memos_in_gp(self):
        from apps.inventory.models import Bin, Item, Recipe, RecipeItem

        rice = Item.objects.create(
            item_name="Rice stock",
            item_group=self.group_food,
            stock_uom=self.uom,
            department="FOOD",
            is_sales_item=False,
            is_stock_item=True,
            is_purchase_item=True,
        )
        recipe = Recipe.objects.create(item=self.food, output_qty=Decimal("1"))
        RecipeItem.objects.create(recipe=recipe, ingredient=rice, qty=Decimal("0.20"))
        Bin.objects.create(item=rice, warehouse=self.kitchen_wh, actual_qty=Decimal("0"))
        StockLedgerEntry.create_entry(
            item=rice,
            warehouse=self.kitchen_wh,
            quantity=Decimal("10"),
            voucher_type="Purchase Receipt",
            voucher_no="PR-FOOD",
            unit_rate=Decimal("200"),
        )
        rec = StockReconciliation.objects.create(
            reason="CONSUMPTION", warehouse=self.kitchen_wh, posting_date=date.today()
        )
        StockReconciliationItem.objects.create(reconciliation=rec, item=rice, qty=Decimal("0"))
        submit_stock_reconciliation(rec)
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        computation = compute_daily_pnl(self._draft())
        self.assertEqual(computation.totals["cogs"], Decimal("2000"))
        self.assertEqual(computation.totals["cogs_drinks"], Decimal("0"))
        self.assertEqual(computation.totals["kitchen_consumption"], Decimal("2000"))
        self.assertEqual(computation.totals["theoretical_food_cost"], Decimal("40"))
        self.assertEqual(computation.totals["food_cost_variance"], Decimal("-1960"))
        # Food GP subtracts actual usage now.
        self.assertEqual(computation.totals["gross_profit"], Decimal("-500"))
        memos = {line.section for line in computation.lines if line.is_memo}
        self.assertIn(DailyPnLLine.THEORETICAL_FOOD_COST, memos)
        self.assertIn(DailyPnLLine.FOOD_COST_VARIANCE, memos)
        self.assertFalse(any(line.section == DailyPnLLine.KITCHEN_CONSUMPTION for line in computation.lines))
        kinds = {row["kind"] for row in computation.consumption_rows}
        self.assertEqual(kinds, {"CONSUMPTION"})
        self.assertEqual(len(computation.theoretical_rows), 1)
        self.assertEqual(computation.unmapped_rows, [])


class FoodCogsTest(DailyPnLTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._setup_pnl_world()

    def _rice_with_recipe(self, qty="0.20", rate="200"):
        from apps.inventory.models import Bin, Item

        rice = Item.objects.create(
            item_name="Rice AvT",
            item_group=self.group_food,
            stock_uom=self.uom,
            department="FOOD",
            is_sales_item=False,
            is_stock_item=True,
            is_purchase_item=True,
        )
        recipe = Recipe.objects.create(item=self.food, output_qty=Decimal("1"))
        RecipeItem.objects.create(recipe=recipe, ingredient=rice, qty=Decimal(qty))
        Bin.objects.create(item=rice, warehouse=self.kitchen_wh, actual_qty=Decimal("0"))
        StockLedgerEntry.create_entry(
            item=rice,
            warehouse=self.kitchen_wh,
            quantity=Decimal("10"),
            voucher_type="Purchase Receipt",
            voucher_no="PR-AVT",
            unit_rate=Decimal(rate),
        )
        return rice, recipe

    def _consume_all(self, rice):
        rec = StockReconciliation.objects.create(
            reason="CONSUMPTION", warehouse=self.kitchen_wh, posting_date=date.today()
        )
        StockReconciliationItem.objects.create(reconciliation=rec, item=rice, qty=Decimal("5"))
        submit_stock_reconciliation(rec)

    def test_cogs_total_includes_food_and_drinks(self):
        from apps.inventory.models import Bin

        rice, _recipe = self._rice_with_recipe()
        self._consume_all(rice)
        Bin.objects.filter(item=self.drink, warehouse=self.bar_wh).update(actual_qty=0, valuation_rate=0)
        StockLedgerEntry.create_entry(
            item=self.drink,
            warehouse=self.bar_wh,
            quantity=Decimal("100"),
            voucher_type="Purchase Receipt",
            voucher_no="PR-D",
            unit_rate=Decimal("300"),
        )
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        add_order_line(order, self.drink, qty=2, rate=Decimal("500"), menu_item=self.drink_mi)
        self._settle(order)
        computation = compute_daily_pnl(self._draft())
        self.assertEqual(computation.totals["cogs_drinks"], Decimal("600"))
        self.assertEqual(computation.totals["cogs"], Decimal("1600"))
        self.assertEqual(computation.totals["prime_cost"], Decimal("1600"))

    def test_submitted_snapshot_stable_after_recipe_edit(self):
        rice, recipe = self._rice_with_recipe()
        self._consume_all(rice)
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        pnl = self._draft()
        pnl.submit(actor=self.manager)
        self.assertEqual(pnl.theoretical_food_cost, Decimal("40"))
        recipe.is_active = False
        recipe.save(update_fields=["is_active", "updated_at"])
        pnl.refresh_from_db()
        self.assertEqual(pnl.theoretical_food_cost, Decimal("40"))
        self.assertEqual(pnl.theoretical_rows.count(), 1)
        self.assertEqual(pnl.consumption_rows.count(), 1)

    def test_unmapped_dish_listed(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=2, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        computation = compute_daily_pnl(self._draft())
        self.assertEqual(len(computation.unmapped_rows), 1)
        self.assertEqual(computation.unmapped_rows[0]["item_name"], "Jollof Rice")
        self.assertEqual(computation.unmapped_rows[0]["qty"], Decimal("2"))
        self.assertEqual(computation.totals["theoretical_food_cost"], Decimal("0"))


class ElectricityAndTemplatesTest(DailyPnLTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._setup_pnl_world()

    def test_one_sided_readings_raise(self):
        with self.assertRaises(ValidationError):
            self._draft(electricity_opening=Decimal("10"))

    def test_readings_without_rate_raise(self):
        pnl = self._draft(electricity_opening=Decimal("10"), electricity_closing=Decimal("20"))
        with self.assertRaises(ValidationError):
            compute_daily_pnl(pnl)

    def test_electricity_amount(self):
        self.config.electricity_rate = Decimal("50")
        self.config.save()
        pnl = self._draft(electricity_opening=Decimal("10"), electricity_closing=Decimal("12"))
        computation = compute_daily_pnl(pnl)
        elec = next(line for line in computation.lines if line.label == "Electricity")
        self.assertEqual(elec.amount_total, Decimal("100"))
        self.assertEqual(elec.section, DailyPnLLine.DIRECT)

    def test_monthly_template_divides_by_days_in_month(self):
        PnLRecurringExpense.objects.create(
            name="Rent", kind=PnLRecurringExpense.INDIRECT_MONTHLY, amount=Decimal("31000")
        )
        pnl = self._draft(business_date=date(2026, 8, 19))
        computation = compute_daily_pnl(pnl)
        rent = next(line for line in computation.lines if line.label == "Rent")
        self.assertEqual(rent.amount_total, Decimal("1000"))

    def test_employee_override_replaces_templates(self):
        PnLRecurringExpense.objects.create(
            name="Wages", kind=PnLRecurringExpense.EMPLOYEE_DAILY, amount=Decimal("8000")
        )
        pnl = self._draft(employee_cost_override=Decimal("1000"))
        computation = compute_daily_pnl(pnl)
        self.assertEqual(computation.totals["total_employee_costs"], Decimal("1000"))
        self.assertFalse(any(line.label == "Wages" for line in computation.lines))

    def test_percent_of_gross(self):
        PnLRecurringExpense.objects.create(
            name="Commission", kind=PnLRecurringExpense.INDIRECT_PERCENT, percent=Decimal("10")
        )
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        computation = compute_daily_pnl(self._draft())
        commission = next(line for line in computation.lines if line.label == "Commission")
        self.assertEqual(commission.amount_total, Decimal("150"))

    def test_material_qty(self):
        gas = PnLMaterial.objects.create(name="Cooking gas", unit="kg", rate=Decimal("100"))
        pnl = self._draft()
        pnl.material_qtys.create(material=gas, qty=Decimal("2"))
        computation = compute_daily_pnl(pnl)
        line = next(line for line in computation.lines if line.label == "Cooking gas")
        self.assertEqual(line.amount_total, Decimal("200"))
        self.assertEqual(line.section, DailyPnLLine.DIRECT)


class SubmitAndVarianceTest(DailyPnLTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._setup_pnl_world()

    def test_submit_creates_no_gl(self):
        order = self._create_order()
        add_order_line(order, self.food, qty=1, rate=Decimal("1500"), menu_item=self.food_mi)
        self._settle(order)
        gl_before = GLEntry.objects.count()
        pnl = self._draft()
        pnl.submit(actor=self.manager)
        self.assertEqual(pnl.status, DailyPnL.SUBMITTED)
        self.assertEqual(GLEntry.objects.count(), gl_before)
        self.assertTrue(pnl.lines.exists())

    def test_settings_change_does_not_alter_submitted(self):
        self.config.daily_depreciation = Decimal("50")
        self.config.save()
        pnl = self._draft()
        pnl.submit(actor=self.manager)
        self.config.daily_depreciation = Decimal("999")
        self.config.save()
        pnl.refresh_from_db()
        self.assertEqual(pnl.depreciation, Decimal("50"))

    def test_one_submitted_per_date(self):
        self._draft().submit(actor=self.manager)
        with self.assertRaises(ValidationError):
            self._draft()

    def test_cancel_and_amend_recomputes(self):
        pnl = self._draft()
        pnl.submit(actor=self.manager)
        pnl.cancel()
        copy = pnl.amend()
        self.assertEqual(copy.status, DailyPnL.DRAFT)
        self.assertEqual(copy.amended_from_id, pnl.pk)
        copy.submit(actor=self.manager)
        self.assertEqual(copy.status, DailyPnL.SUBMITTED)

    def test_cash_variance_shortage_is_expense(self):
        closing = POSClosingEntry.objects.create(opening_entry=self.opening, cashier=self.user)
        ClosingPayment.objects.create(
            closing_entry=closing,
            mode_of_payment=self.cash,
            closing_amount=Decimal("0"),
        )
        submit_closing_entry(closing, actor=self.manager)
        closing.refresh_from_db()
        computation = compute_daily_pnl(self._draft())
        # opening 50000, no sales, counted 0 → shortage 50000 → P&L expense +50000
        self.assertEqual(computation.totals["cash_variance"], Decimal("50000"))

    def test_cash_variance_toggle_off(self):
        self.config.include_cash_variance = False
        self.config.save()
        computation = compute_daily_pnl(self._draft())
        self.assertEqual(computation.totals["cash_variance"], Decimal("0"))
