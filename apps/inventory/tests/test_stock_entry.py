from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.inventory.models import (
    UOM,
    Bin,
    Item,
    ItemGroup,
    StockEntry,
    StockEntryDetail,
    StockLedgerEntry,
    Warehouse,
)
from apps.inventory.services import cancel_stock_entry, submit_stock_entry
from apps.settings.models import ProductionUnit, Restaurant


class StockEntryTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.accounting.tests.helpers import setup_chart_of_accounts

        cls.uom = UOM.objects.create(name="Nos")
        cls.group = ItemGroup.objects.create(name="Stock")
        cls.store = Warehouse.objects.create(name="Store")
        cls.kitchen = Warehouse.objects.create(name="Kitchen")
        cls.bar = Warehouse.objects.create(name="Bar")
        cls.restaurant = Restaurant.objects.create(
            company="Test Restaurant", store_warehouse=cls.store, default_warehouse=cls.bar
        )
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        for wh in [cls.store, cls.kitchen, cls.bar]:
            wh.account = cls.accounts["stock_in_hand"]
            wh.save(update_fields=["account", "updated_at"])
        ProductionUnit.objects.create(name="Kitchen", department="FOOD", warehouse=cls.kitchen)
        ProductionUnit.objects.create(name="Bar", department="DRINKS", warehouse=cls.bar)
        cls.food = Item.objects.create(
            item_name="Rice",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="FOOD",
            is_stock_item=True,
            is_purchase_item=True,
            is_sales_item=False,
        )
        cls.drink = Item.objects.create(
            item_name="Cola",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="DRINKS",
            is_stock_item=True,
            is_purchase_item=True,
            is_sales_item=True,
        )

    def test_receipt_forces_store_and_requires_purchasable_stock_item(self):
        from apps.payments.models import ModeOfPayment, PaymentGLMapping

        cash = ModeOfPayment.objects.create(name="Cash Test", type="CASH", enabled=True)
        PaymentGLMapping.objects.create(mode_of_payment=cash, default_account=self.accounts["cash"])
        # Receipt without mode → funding error
        entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT")
        line = StockEntryDetail.objects.create(
            stock_entry=entry, item=self.food, target_warehouse=self.bar, qty=Decimal("4"), basic_rate=Decimal("50")
        )
        with self.assertRaisesMessage(ValidationError, "payment mode"):
            submit_stock_entry(entry)
        # With mode but wrong target → store error (proves funding passed)
        entry.mode_of_payment = cash
        entry.save(update_fields=["mode_of_payment", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "central Store"):
            submit_stock_entry(entry)
        line.target_warehouse = None
        line.save()
        submit_stock_entry(entry)
        line.refresh_from_db()
        self.assertEqual(line.target_warehouse, self.store)
        self.assertEqual(Bin.objects.get(item=self.food, warehouse=self.store).actual_qty, Decimal("4"))

    def test_receipt_rejects_zero_rate(self):
        from apps.payments.models import ModeOfPayment, PaymentGLMapping

        cash = ModeOfPayment.objects.create(name="Cash Zero", type="CASH", enabled=True)
        PaymentGLMapping.objects.create(mode_of_payment=cash, default_account=self.accounts["cash"])
        entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT", mode_of_payment=cash)
        StockEntryDetail.objects.create(
            stock_entry=entry, item=self.food, target_warehouse=self.store, qty=Decimal("4"), basic_rate=Decimal("0")
        )
        with self.assertRaisesMessage(ValidationError, "Rate for Rice must be greater than zero."):
            submit_stock_entry(entry)

    def test_transfer_derives_department_targets_and_preserves_wac_rate(self):
        # WAC: store 2@100 + 3@200 => WAC 160. Transfer 3 at source WAC 160.
        StockLedgerEntry.create_entry(
            item=self.food,
            warehouse=self.store,
            quantity=Decimal("2"),
            voucher_type="Opening",
            voucher_no="1",
            unit_rate=Decimal("100"),
        )
        StockLedgerEntry.create_entry(
            item=self.food,
            warehouse=self.store,
            quantity=Decimal("3"),
            voucher_type="Opening",
            voucher_no="2",
            unit_rate=Decimal("200"),
        )
        StockLedgerEntry.create_entry(
            item=self.drink,
            warehouse=self.store,
            quantity=Decimal("2"),
            voucher_type="Opening",
            voucher_no="3",
            unit_rate=Decimal("75"),
        )
        entry = StockEntry.objects.create(purpose="MATERIAL_TRANSFER")
        food_line = StockEntryDetail.objects.create(stock_entry=entry, item=self.food, qty=Decimal("3"))
        drink_line = StockEntryDetail.objects.create(stock_entry=entry, item=self.drink, qty=Decimal("2"))

        submit_stock_entry(entry)

        food_line.refresh_from_db()
        drink_line.refresh_from_db()
        self.assertEqual((food_line.source_warehouse, food_line.target_warehouse), (self.store, self.kitchen))
        self.assertEqual((drink_line.source_warehouse, drink_line.target_warehouse), (self.store, self.bar))
        food_in = StockLedgerEntry.objects.get(
            voucher_type="Stock Entry", voucher_no=str(entry.pk), voucher_detail_no=str(food_line.pk), quantity__gt=0
        )
        self.assertEqual(food_in.unit_rate, Decimal("160"))

    def test_transfer_posts_sih_legs_and_cancel_mirrors_them(self):
        from apps.accounting.models import GLEntry, LedgerAccount

        store_account = LedgerAccount.objects.create(
            name="SIH Store transfer test",
            parent=self.accounts["assets"],
            account_type=LedgerAccount.ASSET,
            report_type=LedgerAccount.BALANCE_SHEET,
        )
        kitchen_account = LedgerAccount.objects.create(
            name="SIH Kitchen transfer test",
            parent=self.accounts["assets"],
            account_type=LedgerAccount.ASSET,
            report_type=LedgerAccount.BALANCE_SHEET,
        )
        self.store.account = store_account
        self.store.save(update_fields=["account", "updated_at"])
        self.kitchen.account = kitchen_account
        self.kitchen.save(update_fields=["account", "updated_at"])
        StockLedgerEntry.create_entry(
            item=self.food,
            warehouse=self.store,
            quantity=Decimal("2"),
            voucher_type="Opening",
            voucher_no="T1",
            unit_rate=Decimal("100"),
        )
        entry = StockEntry.objects.create(purpose="MATERIAL_TRANSFER", posting_date=date(2026, 1, 15))
        StockEntryDetail.objects.create(stock_entry=entry, item=self.food, qty=Decimal("2"))
        submit_stock_entry(entry)

        legs = GLEntry.objects.filter(voucher_type="Stock Entry", voucher_no=str(entry.pk), is_cancelled=False)
        self.assertEqual(legs.filter(account=kitchen_account, debit=Decimal("200")).count(), 1)
        self.assertEqual(legs.filter(account=store_account, credit=Decimal("200")).count(), 1)

        # A later receipt lifts the destination WAC to 200; cancellation gives the
        # stock back to Store at its original value and routes the drift to variance.
        StockLedgerEntry.create_entry(
            item=self.food,
            warehouse=self.kitchen,
            quantity=Decimal("2"),
            voucher_type="Opening",
            voucher_no="T2",
            unit_rate=Decimal("300"),
        )
        cancel_stock_entry(entry)

        self.assertEqual(
            GLEntry.objects.filter(voucher_type="Stock Entry", voucher_no=str(entry.pk), is_cancelled=True).count(), 2
        )
        reversal = GLEntry.objects.filter(voucher_type="Stock Entry", voucher_no=str(entry.pk), is_cancelled=False)
        self.assertEqual(reversal.filter(account=store_account, debit=Decimal("200")).count(), 1)
        self.assertEqual(reversal.filter(account=kitchen_account, credit=Decimal("400")).count(), 1)
        self.assertEqual(reversal.filter(account=self.accounts["variance"], debit=Decimal("200")).count(), 1)
        self.assertEqual(reversal.first().posting_date, timezone.localdate())
        # Store is restored at what it originally gave up: 2 back @ 100 => WAC 100.
        store_bin = Bin.objects.get(item=self.food, warehouse=self.store)
        self.assertEqual(store_bin.valuation_rate, Decimal("100"))

    def test_transfer_line_carries_no_rate(self):
        # Transfer lines don't price anything — value moves at the source WAC.
        entry = StockEntry.objects.create(purpose="MATERIAL_TRANSFER")
        line = StockEntryDetail.objects.create(
            stock_entry=entry, item=self.food, qty=Decimal("2"), basic_rate=Decimal("350")
        )
        line.refresh_from_db()
        self.assertEqual((line.basic_rate, line.amount), (Decimal("0"), Decimal("0")))

    def test_transfer_rejects_override_and_negative_store(self):
        entry = StockEntry.objects.create(purpose="MATERIAL_TRANSFER")
        line = StockEntryDetail.objects.create(
            stock_entry=entry, item=self.food, source_warehouse=self.bar, target_warehouse=self.store, qty=1
        )
        with self.assertRaises(ValidationError):
            submit_stock_entry(entry)
        line.source_warehouse = None
        line.target_warehouse = None
        line.save()
        with self.assertRaisesMessage(ValidationError, "Insufficient stock"):
            submit_stock_entry(entry)
        self.assertEqual(
            StockLedgerEntry.objects.filter(voucher_type="Stock Entry", voucher_no=str(entry.pk)).count(), 0
        )

    def test_cancel_transfer_rejects_consumed_target_atomically(self):
        StockLedgerEntry.create_entry(
            item=self.food,
            warehouse=self.store,
            quantity=Decimal("5"),
            voucher_type="Opening",
            voucher_no="1",
            unit_rate=Decimal("100"),
        )
        entry = StockEntry.objects.create(purpose="MATERIAL_TRANSFER")
        StockEntryDetail.objects.create(stock_entry=entry, item=self.food, qty=Decimal("2"))
        submit_stock_entry(entry)
        StockLedgerEntry.create_entry(
            item=self.food, warehouse=self.kitchen, quantity=Decimal("-2"), voucher_type="Consumption", voucher_no="1"
        )
        with self.assertRaisesMessage(ValidationError, "already been used"):
            cancel_stock_entry(entry)
        entry.refresh_from_db()
        self.assertEqual(entry.status, "SUBMITTED")
        self.assertFalse(StockLedgerEntry.objects.filter(voucher_type="Stock Entry Cancellation").exists())

    def test_cancel_transfer_restores_source_at_original_value(self):
        # Store 2@100, kitchen 1@300. Transfer 1 at store WAC 100.
        # After transfer: kitchen WAC (1*300+1*100)/2=200. Cancel restores Store
        # at the original transfer value 100; the 100 drift to Kitchen's current
        # valuation is stamped CANCELLATION_WAC (GL nets to zero — shared SIH).
        StockLedgerEntry.create_entry(
            item=self.food,
            warehouse=self.store,
            quantity=Decimal("2"),
            voucher_type="Opening",
            voucher_no="1",
            unit_rate=Decimal("100"),
        )
        StockLedgerEntry.create_entry(
            item=self.food,
            warehouse=self.kitchen,
            quantity=Decimal("1"),
            voucher_type="Opening",
            voucher_no="2",
            unit_rate=Decimal("300"),
        )
        entry = StockEntry.objects.create(purpose="MATERIAL_TRANSFER", posting_date=date(2026, 1, 15))
        StockEntryDetail.objects.create(stock_entry=entry, item=self.food, qty=Decimal("1"))
        submit_stock_entry(entry)

        cancel_stock_entry(entry)

        reversals = StockLedgerEntry.objects.filter(voucher_type="Stock Entry Cancellation", voucher_no=str(entry.pk))
        target_reversal = reversals.get(warehouse=self.kitchen)
        source_reversal = reversals.get(warehouse=self.store)
        self.assertEqual(target_reversal.unit_rate, Decimal("200"))
        self.assertEqual(target_reversal.quantity, Decimal("-1"))
        self.assertEqual(target_reversal.posting_date, timezone.localdate())
        # Source reversal inbound at the original transfer rate 100, with the
        # cross-warehouse drift stamped as variance.
        self.assertEqual(source_reversal.unit_rate, Decimal("100"))
        self.assertEqual(source_reversal.quantity, Decimal("1"))
        self.assertEqual(source_reversal.variance_amount, Decimal("100"))
        self.assertEqual(source_reversal.variance_type, "CANCELLATION_WAC")
        kitchen_bin = Bin.objects.get(item=self.food, warehouse=self.kitchen)
        self.assertEqual(kitchen_bin.actual_qty, Decimal("1"))
        self.assertEqual(kitchen_bin.valuation_rate, Decimal("200"))
        # Store after cancel: 1*100 +1*100 blended => 100
        store_bin = Bin.objects.get(item=self.food, warehouse=self.store)
        self.assertEqual(store_bin.actual_qty, Decimal("2"))
        self.assertEqual(store_bin.valuation_rate, Decimal("100"))
        history = StockEntry.stock_ledger_entries_for_voucher(str(entry.pk))
        self.assertEqual(history.count(), 4)
        self.assertEqual(
            set(history.values_list("voucher_type", flat=True)),
            {"Stock Entry", "Stock Entry Cancellation"},
        )
