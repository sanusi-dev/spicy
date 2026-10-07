from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import GLEntry
from apps.inventory.models import (
    UOM,
    Bin,
    Item,
    ItemGroup,
    StockLedgerEntry,
    StockReconciliation,
    StockReconciliationItem,
    Warehouse,
)
from apps.inventory.services import cancel_stock_reconciliation, submit_stock_reconciliation
from apps.settings.models import ProductionUnit, Restaurant
from apps.users.models import CustomUser


class StockReconciliationStandardizationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.accounting.tests.helpers import setup_chart_of_accounts

        cls.uom = UOM.objects.create(name="Nos")
        cls.group = ItemGroup.objects.create(name="Food")
        cls.store = Warehouse.objects.create(name="Store")
        cls.kitchen = Warehouse.objects.create(name="Kitchen")
        cls.bar = Warehouse.objects.create(name="Bar")
        cls.restaurant = Restaurant.objects.create(
            company="Test Co", store_warehouse=cls.store, default_warehouse=cls.bar
        )
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        for wh in (cls.store, cls.kitchen, cls.bar):
            wh.account = cls.accounts["stock_in_hand"]
            wh.save(update_fields=["account", "updated_at"])
        cls.restaurant.refresh_from_db()
        ProductionUnit.objects.create(name="Kitchen", department="FOOD", warehouse=cls.kitchen)
        ProductionUnit.objects.create(name="Bar", department="DRINKS", warehouse=cls.bar)
        cls.rice = Item.objects.create(
            item_name="Rice",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="FOOD",
            is_stock_item=True,
            is_purchase_item=True,
        )
        cls.coke = Item.objects.create(
            item_name="Coke",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="DRINKS",
            is_stock_item=True,
            is_sales_item=True,
            is_purchase_item=True,
        )

    def make_rec(self, reason, warehouse=None, **kwargs):
        return StockReconciliation.objects.create(reason=reason, warehouse=warehouse or self.kitchen, **kwargs)

    def gl_for(self, rec):
        return list(GLEntry.objects.filter(voucher_type="Stock Reconciliation", voucher_no=str(rec.pk)))

    # Opening

    def test_opening_fresh_warehouse_posts_dr_warehouse_cr_temporary_opening(self):
        fresh = Warehouse.objects.create(name="Fresh", account=self.accounts["stock_in_hand"])
        rec = self.make_rec("OPENING_STOCK", warehouse=fresh)
        StockReconciliationItem.objects.create(
            reconciliation=rec, item=self.rice, qty=Decimal("10"), valuation_rate=Decimal("100")
        )
        submit_stock_reconciliation(rec)
        bin_obj = Bin.objects.get(item=self.rice, warehouse=fresh)
        self.assertEqual(bin_obj.actual_qty, Decimal("10"))
        self.assertEqual(bin_obj.valuation_rate, Decimal("100"))
        entries = self.gl_for(rec)
        self.assertEqual(len(entries), 2)
        by_acct = {e.account_id: e for e in entries}
        wh = by_acct[self.accounts["stock_in_hand"].pk]
        opening = by_acct[self.accounts["temporary_opening"].pk]
        self.assertEqual(wh.debit, Decimal("1000"))
        self.assertEqual(opening.credit, Decimal("1000"))

    def test_opening_rejected_when_warehouse_has_any_prior_sle(self):
        StockLedgerEntry.create_entry(self.rice, self.store, Decimal("5"), "Receipt", "1", unit_rate=Decimal("50"))
        cancel_rec = StockReconciliation.objects.filter(warehouse=self.store, status="SUBMITTED").first()
        _ = cancel_rec
        rec = self.make_rec("OPENING_STOCK", warehouse=self.store)
        StockReconciliationItem.objects.create(
            reconciliation=rec, item=self.rice, qty=Decimal("10"), valuation_rate=Decimal("100")
        )
        with self.assertRaisesMessage(ValidationError, "fresh warehouse"):
            submit_stock_reconciliation(rec)

    def test_opening_rejected_when_cancelled_sle_exists(self):
        other = Warehouse.objects.create(name="OtherFresh", account=self.accounts["stock_in_hand"])
        StockLedgerEntry.create_entry(self.rice, other, Decimal("5"), "Receipt", "1", unit_rate=Decimal("50"))
        # Reverse it so only a cancellation pair remains in history — gate still applies.
        bin_obj = Bin.objects.get(item=self.rice, warehouse=other)
        StockLedgerEntry.create_entry(
            self.rice, other, Decimal("-5"), "Receipt Cancellation", "1", reversal_of_sle_id=None
        )
        self.assertTrue(StockLedgerEntry.objects.filter(warehouse=other).exists())
        rec = self.make_rec("OPENING_STOCK", warehouse=other)
        StockReconciliationItem.objects.create(
            reconciliation=rec, item=self.rice, qty=Decimal("3"), valuation_rate=Decimal("100")
        )
        with self.assertRaisesMessage(ValidationError, "fresh warehouse"):
            submit_stock_reconciliation(rec)
        _ = bin_obj

    def test_opening_positive_line_requires_rate(self):
        fresh = Warehouse.objects.create(name="Fresh2", account=self.accounts["stock_in_hand"])
        rec = self.make_rec("OPENING_STOCK", warehouse=fresh)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("4"))
        with self.assertRaisesMessage(ValidationError, "valuation rate"):
            submit_stock_reconciliation(rec)

    def test_opening_rejects_pl_temporary_opening_account(self):
        from apps.accounting.models import LedgerAccount

        pl = LedgerAccount.objects.create(
            name="Fake P&L opening",
            parent=self.accounts["expenses"],
            account_type=LedgerAccount.EXPENSE,
            report_type=LedgerAccount.PROFIT_AND_LOSS,
        )
        self.restaurant.temporary_opening_account = pl
        self.restaurant.save(update_fields=["temporary_opening_account", "updated_at"])
        try:
            fresh = Warehouse.objects.create(name="FreshPL", account=self.accounts["stock_in_hand"])
            rec = self.make_rec("OPENING_STOCK", warehouse=fresh)
            StockReconciliationItem.objects.create(
                reconciliation=rec, item=self.rice, qty=Decimal("2"), valuation_rate=Decimal("50")
            )
            with self.assertRaisesMessage(ValidationError, "balance-sheet"):
                submit_stock_reconciliation(rec)
        finally:
            self.restaurant.temporary_opening_account = self.accounts["temporary_opening"]
            self.restaurant.save(update_fields=["temporary_opening_account", "updated_at"])

    # Adjustment

    def test_adjustment_outbound_posts_dr_adjustment_cr_warehouse(self):
        StockLedgerEntry.create_entry(self.rice, self.store, Decimal("10"), "Receipt", "A1", unit_rate=Decimal("100"))
        rec = self.make_rec("ADJUSTMENT", warehouse=self.store)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("6"))
        submit_stock_reconciliation(rec)
        self.assertEqual(Bin.objects.get(item=self.rice, warehouse=self.store).actual_qty, Decimal("6"))
        entries = self.gl_for(rec)
        by_acct = {e.account_id: e for e in entries}
        adj = by_acct[self.accounts["stock_adjustment"].pk]
        wh = by_acct[self.accounts["stock_in_hand"].pk]
        self.assertEqual(adj.debit, Decimal("400"))
        self.assertEqual(wh.credit, Decimal("400"))

    def test_adjustment_inbound_reverses_legs(self):
        StockLedgerEntry.create_entry(self.rice, self.store, Decimal("5"), "Receipt", "A2", unit_rate=Decimal("100"))
        rec = self.make_rec("ADJUSTMENT", warehouse=self.store)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("8"))
        submit_stock_reconciliation(rec)
        entries = self.gl_for(rec)
        by_acct = {e.account_id: e for e in entries}
        adj = by_acct[self.accounts["stock_adjustment"].pk]
        wh = by_acct[self.accounts["stock_in_hand"].pk]
        self.assertEqual(wh.debit, Decimal("300"))
        self.assertEqual(adj.credit, Decimal("300"))

    def test_adjustment_reserved_floor_held(self):
        Bin.objects.create(item=self.rice, warehouse=self.store, actual_qty=10, reserved_qty=4)
        rec = self.make_rec("ADJUSTMENT", warehouse=self.store)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("3"))
        with self.assertRaisesMessage(ValidationError, "reserved quantity"):
            submit_stock_reconciliation(rec)

    def test_adjustment_cancel_reverses_gl(self):
        StockLedgerEntry.create_entry(self.rice, self.store, Decimal("10"), "Receipt", "A3", unit_rate=Decimal("100"))
        rec = self.make_rec("ADJUSTMENT", warehouse=self.store, posting_date=date(2026, 1, 15))
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("6"))
        submit_stock_reconciliation(rec)
        cancel_stock_reconciliation(rec)
        self.assertEqual(Bin.objects.get(item=self.rice, warehouse=self.store).actual_qty, Decimal("10"))
        reversal_gl = GLEntry.objects.filter(
            voucher_type="Stock Reconciliation", voucher_no=str(rec.pk), remarks="Reversal"
        ).first()
        reversal_sle = StockLedgerEntry.objects.get(
            voucher_type="Stock Reconciliation Cancellation", voucher_no=str(rec.pk)
        )
        self.assertEqual(reversal_gl.posting_date, timezone.localdate())
        self.assertEqual(reversal_sle.posting_date, timezone.localdate())

    def test_adjustment_cancel_posts_wac_drift_to_variance(self):
        StockLedgerEntry.create_entry(self.rice, self.store, Decimal("10"), "Receipt", "A7", unit_rate=Decimal("100"))
        rec = self.make_rec("ADJUSTMENT", warehouse=self.store)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("6"))
        submit_stock_reconciliation(rec)
        StockLedgerEntry.create_entry(
            item=self.rice,
            warehouse=self.store,
            quantity=Decimal("4"),
            voucher_type="Later Receipt",
            voucher_no="A8",
            unit_rate=Decimal("200"),
        )
        self.assertEqual(Bin.objects.get(item=self.rice, warehouse=self.store).valuation_rate, Decimal("140"))

        cancel_stock_reconciliation(rec)

        reversal = StockLedgerEntry.objects.get(
            voucher_type="Stock Reconciliation Cancellation", voucher_no=str(rec.pk)
        )
        self.assertEqual(reversal.quantity, Decimal("4"))
        self.assertEqual(reversal.unit_rate, Decimal("140"))
        self.assertEqual(reversal.variance_type, "CANCELLATION_WAC")
        self.assertEqual(reversal.variance_amount, Decimal("-160"))
        stock_bin = Bin.objects.get(item=self.rice, warehouse=self.store)
        self.assertEqual(stock_bin.actual_qty, Decimal("14"))
        self.assertEqual(stock_bin.valuation_rate, Decimal("140"))
        reversal_gl = GLEntry.objects.filter(
            voucher_type="Stock Reconciliation", voucher_no=str(rec.pk), is_cancelled=False
        )
        self.assertEqual(reversal_gl.filter(account=self.accounts["stock_in_hand"], debit=Decimal("560")).count(), 1)
        self.assertEqual(
            reversal_gl.filter(account=self.accounts["stock_adjustment"], credit=Decimal("400")).count(), 1
        )
        self.assertEqual(reversal_gl.filter(account=self.accounts["variance"], credit=Decimal("160")).count(), 1)

    # Consumption

    def test_consumption_posts_dr_expense_cr_kitchen(self):
        StockLedgerEntry.create_entry(self.rice, self.kitchen, Decimal("10"), "Receipt", "C1", unit_rate=Decimal("200"))
        rec = self.make_rec("CONSUMPTION", warehouse=self.kitchen)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("6"))
        submit_stock_reconciliation(rec)
        self.assertEqual(Bin.objects.get(item=self.rice, warehouse=self.kitchen).actual_qty, Decimal("6"))
        sle = StockLedgerEntry.objects.get(voucher_type="Stock Reconciliation", voucher_no=str(rec.pk))
        self.assertEqual(sle.quantity, Decimal("-4"))
        self.assertEqual(sle.unit_rate, Decimal("200"))
        entries = self.gl_for(rec)
        by_acct = {e.account_id: e for e in entries}
        exp = by_acct[self.accounts["cogs"].pk]
        self.assertEqual(exp.debit, Decimal("800"))

    def test_consumption_uses_kitchen_unit_expense_account(self):
        from apps.accounting.models import LedgerAccount

        food_cogs = LedgerAccount.objects.create(
            name="Food COGS test",
            parent=self.accounts["expenses"],
            account_type=LedgerAccount.EXPENSE,
            report_type=LedgerAccount.PROFIT_AND_LOSS,
        )
        kitchen_unit = ProductionUnit.objects.get(department="FOOD")
        kitchen_unit.expense_account = food_cogs
        kitchen_unit.save(update_fields=["expense_account", "updated_at"])
        StockLedgerEntry.create_entry(self.rice, self.kitchen, Decimal("10"), "Receipt", "C5", unit_rate=Decimal("200"))
        rec = self.make_rec("CONSUMPTION", warehouse=self.kitchen)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("6"))
        submit_stock_reconciliation(rec)
        entries = self.gl_for(rec)
        by_acct = {e.account_id: e for e in entries}
        self.assertEqual(by_acct[food_cogs.pk].debit, Decimal("800"))
        self.assertNotIn(self.accounts["cogs"].pk, by_acct)

    def test_consumption_count_above_bin_rejected(self):
        StockLedgerEntry.create_entry(self.rice, self.kitchen, Decimal("5"), "Receipt", "C2", unit_rate=Decimal("100"))
        rec = self.make_rec("CONSUMPTION", warehouse=self.kitchen)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("9"))
        with self.assertRaisesMessage(ValidationError, "cannot exceed"):
            submit_stock_reconciliation(rec)

    def test_consumption_count_equal_to_bin_is_noop(self):
        StockLedgerEntry.create_entry(self.rice, self.kitchen, Decimal("5"), "Receipt", "C3", unit_rate=Decimal("100"))
        rec = self.make_rec("CONSUMPTION", warehouse=self.kitchen)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("5"))
        submit_stock_reconciliation(rec)
        self.assertFalse(
            StockLedgerEntry.objects.filter(voucher_type="Stock Reconciliation", voucher_no=str(rec.pk)).exists()
        )
        self.assertFalse(self.gl_for(rec))

    def test_consumption_requires_kitchen_and_food(self):
        rec = self.make_rec("CONSUMPTION", warehouse=self.store)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "configured Kitchen"):
            submit_stock_reconciliation(rec)
        rec2 = self.make_rec("CONSUMPTION", warehouse=self.kitchen)
        StockReconciliationItem.objects.create(reconciliation=rec2, item=self.coke, qty=Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "FOOD"):
            submit_stock_reconciliation(rec2)

    def test_consumption_cancel_reverses(self):
        StockLedgerEntry.create_entry(self.rice, self.kitchen, Decimal("10"), "Receipt", "C4", unit_rate=Decimal("100"))
        rec = self.make_rec("CONSUMPTION", warehouse=self.kitchen)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("7"))
        submit_stock_reconciliation(rec)
        cancel_stock_reconciliation(rec)
        self.assertEqual(Bin.objects.get(item=self.rice, warehouse=self.kitchen).actual_qty, Decimal("10"))

    # Waste (delta-entry)

    def test_waste_posts_negative_delta_and_dr_wastage(self):
        StockLedgerEntry.create_entry(self.rice, self.store, Decimal("10"), "Receipt", "W1", unit_rate=Decimal("100"))
        rec = self.make_rec("WASTE_DAMAGE", warehouse=self.store)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("3"))
        submit_stock_reconciliation(rec)
        sle = StockLedgerEntry.objects.get(voucher_type="Stock Reconciliation", voucher_no=str(rec.pk))
        self.assertEqual(sle.quantity, Decimal("-3"))
        self.assertEqual(Bin.objects.get(item=self.rice, warehouse=self.store).actual_qty, Decimal("7"))
        entries = self.gl_for(rec)
        by_acct = {e.account_id: e for e in entries}
        waste = by_acct[self.accounts["wastage"].pk]
        self.assertEqual(waste.debit, Decimal("300"))

    def test_waste_above_on_hand_minus_reserved_rejected(self):
        Bin.objects.create(item=self.rice, warehouse=self.store, actual_qty=10, reserved_qty=4)
        Bin.objects.filter(item=self.rice, warehouse=self.store).update(valuation_rate=Decimal("100"))
        rec = self.make_rec("WASTE_DAMAGE", warehouse=self.store)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("7"))
        with self.assertRaisesMessage(ValidationError, "on-hand"):
            submit_stock_reconciliation(rec)

    def test_waste_requires_positive_qty(self):
        rec = self.make_rec("WASTE_DAMAGE", warehouse=self.store)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "greater than zero"):
            submit_stock_reconciliation(rec)

    def test_waste_cancel_reverses(self):
        StockLedgerEntry.create_entry(self.rice, self.store, Decimal("10"), "Receipt", "W2", unit_rate=Decimal("50"))
        rec = self.make_rec("WASTE_DAMAGE", warehouse=self.store)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("4"))
        submit_stock_reconciliation(rec)
        cancel_stock_reconciliation(rec)
        self.assertEqual(Bin.objects.get(item=self.rice, warehouse=self.store).actual_qty, Decimal("10"))

    def test_drinks_adjustment_unaffected(self):
        StockLedgerEntry.create_entry(self.coke, self.bar, Decimal("24"), "Receipt", "D1", unit_rate=Decimal("200"))
        rec = self.make_rec("ADJUSTMENT", warehouse=self.bar)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.coke, qty=Decimal("20"))
        submit_stock_reconciliation(rec)
        self.assertEqual(Bin.objects.get(item=self.coke, warehouse=self.bar).actual_qty, Decimal("20"))

    # Attribution

    def test_write_off_notes_remain_optional(self):
        StockLedgerEntry.create_entry(self.rice, self.store, Decimal("10"), "Receipt", "A4", unit_rate=Decimal("100"))
        for reason, qty in (("ADJUSTMENT", Decimal("9")), ("WASTE_DAMAGE", Decimal("1"))):
            with self.subTest(reason=reason):
                rec = self.make_rec(reason, warehouse=self.store)
                StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=qty)
                submit_stock_reconciliation(rec)
                rec.refresh_from_db()
                self.assertEqual(rec.status, "SUBMITTED")
                self.assertEqual(rec.remarks, "")

    def test_submit_records_actor(self):
        user = CustomUser.objects.create_user(username="inv@test.com", password="testpass123")
        StockLedgerEntry.create_entry(self.rice, self.store, Decimal("10"), "Receipt", "A5", unit_rate=Decimal("100"))
        rec = self.make_rec("ADJUSTMENT", warehouse=self.store)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("9"))
        submit_stock_reconciliation(rec, actor=user)
        rec.refresh_from_db()
        self.assertEqual(rec.submitted_by, user)
        self.assertIsNotNone(rec.submitted_at)

    def test_cancel_records_actor(self):
        user = CustomUser.objects.create_user(username="inv2@test.com", password="testpass123")
        StockLedgerEntry.create_entry(self.rice, self.store, Decimal("10"), "Receipt", "A6", unit_rate=Decimal("100"))
        rec = self.make_rec("ADJUSTMENT", warehouse=self.store)
        StockReconciliationItem.objects.create(reconciliation=rec, item=self.rice, qty=Decimal("9"))
        submit_stock_reconciliation(rec, actor=user)
        cancel_stock_reconciliation(rec, actor=user)
        rec.refresh_from_db()
        self.assertEqual(rec.cancelled_by, user)
        self.assertIsNotNone(rec.cancelled_at)
