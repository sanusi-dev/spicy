from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import GLEntry
from apps.inventory.forms import PurchaseReceiptItemForm
from apps.inventory.models import (
    UOM,
    Bin,
    Item,
    ItemGroup,
    PurchaseReceipt,
    PurchaseReceiptItem,
    StockLedgerEntry,
    Warehouse,
)
from apps.inventory.services import cancel_purchase_receipt, submit_purchase_receipt
from apps.settings.models import Restaurant


class PurchaseReceiptTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.accounting.tests.helpers import setup_chart_of_accounts

        cls.uom = UOM.objects.create(name="Nos")
        cls.group = ItemGroup.objects.create(name="Stock")
        cls.store = Warehouse.objects.create(name="Store")
        cls.other = Warehouse.objects.create(name="Other")
        cls.restaurant = Restaurant.objects.create(company="Test", store_warehouse=cls.store)
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        cls.store.account = (
            cls.accounts["stock_in_hand"] if "stock_in_hand" in cls.accounts else cls.accounts.get("cogs")
        )
        cls.store.save(update_fields=["account", "updated_at"])
        cls.other.account = (
            cls.accounts["stock_in_hand"] if "stock_in_hand" in cls.accounts else cls.accounts.get("cogs")
        )
        cls.other.save(update_fields=["account", "updated_at"])
        cls.item = Item.objects.create(
            item_name="Rice",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="FOOD",
            is_stock_item=True,
            is_purchase_item=True,
        )

    def test_blank_rate_stays_empty_on_new_line(self):
        form = PurchaseReceiptItemForm()
        self.assertIsNone(form.fields["rate"].initial)

    def test_submit_forces_configured_store(self):
        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.store)
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=10, rate=100)
        submit_purchase_receipt(receipt)
        receipt.refresh_from_db()
        self.assertEqual(receipt.warehouse, self.store)
        self.assertEqual(receipt.total, Decimal("1000"))
        self.assertEqual(Bin.objects.get(item=self.item, warehouse=self.store).actual_qty, Decimal("10"))

    def test_submit_rejects_overridden_warehouse(self):
        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.other)
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=1, rate=10)
        with self.assertRaisesMessage(ValidationError, "central Store"):
            submit_purchase_receipt(receipt)

    def test_submit_rejects_zero_rate(self):
        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.store)
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=10, rate=0)
        with self.assertRaisesMessage(ValidationError, "Rate for Rice must be greater than zero."):
            submit_purchase_receipt(receipt)

    def test_submit_rejects_non_stock_non_purchase_disabled_and_template_items(self):
        for changes in (
            {"is_stock_item": False},
            {"is_purchase_item": False},
            {"disabled": True},
            {"has_variants": True},
        ):
            values = {"is_stock_item": True, "is_purchase_item": True, "disabled": False, "has_variants": False}
            values.update(changes)
            Item.objects.filter(pk=self.item.pk).update(**values)
            self.item.refresh_from_db()
            receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.store)
            PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=1, rate=10)
            with self.assertRaisesMessage(ValidationError, "enabled stock and purchase item"):
                submit_purchase_receipt(receipt)

    def test_cancel_rejects_consumed_stock_and_rolls_back_all_reversals(self):
        second_item = Item.objects.create(
            item_name="Beans",
            item_group=self.group,
            stock_uom=self.uom,
            department="FOOD",
            is_stock_item=True,
            is_purchase_item=True,
        )
        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.store)
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=2, rate=10)
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=second_item, received_qty=2, rate=20)
        submit_purchase_receipt(receipt)
        # Consuming only the second line forces cancellation to fail mid-reversal; all reversals must roll back.
        StockLedgerEntry.create_entry(
            item=second_item, warehouse=self.store, quantity=Decimal("-2"), voucher_type="Consumption", voucher_no="1"
        )

        with self.assertRaisesMessage(ValidationError, "Insufficient stock"):
            cancel_purchase_receipt(receipt)

        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "SUBMITTED")
        self.assertEqual(Bin.objects.get(item=self.item, warehouse=self.store).actual_qty, Decimal("2"))
        self.assertFalse(StockLedgerEntry.objects.filter(voucher_type="Purchase Receipt Cancellation").exists())
        self.assertFalse(StockLedgerEntry.objects.filter(reversal_of_sle__isnull=False).exists())

    def test_cancel_at_current_wac_with_variance(self):
        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.store)
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=2, rate=100)
        submit_purchase_receipt(receipt)
        StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.store,
            quantity=Decimal("2"),
            voucher_type="Later Receipt",
            voucher_no="1",
            unit_rate=Decimal("200"),
        )
        # Bin now 4 @ WAC (2*100+2*200)/4=150
        before_wac = Bin.objects.get(item=self.item, warehouse=self.store).valuation_rate
        self.assertEqual(before_wac, Decimal("150"))

        cancel_purchase_receipt(receipt)

        stock_bin = Bin.objects.get(item=self.item, warehouse=self.store)
        reversal = StockLedgerEntry.objects.get(
            voucher_type="Purchase Receipt Cancellation", voucher_no=str(receipt.pk)
        )
        # Reversal at current WAC before cancel (150), not original 100
        self.assertEqual(reversal.unit_rate, Decimal("150"))
        self.assertEqual(reversal.variance_type, "CANCELLATION_WAC")
        self.assertEqual(reversal.variance_amount, Decimal("100"))  # 2*(150-100)
        self.assertEqual(reversal.reversal_of_sle_id is not None, True)
        self.assertEqual(stock_bin.actual_qty, Decimal("2"))
        self.assertEqual(stock_bin.valuation_rate, Decimal("150"))
        self.assertEqual(stock_bin.stock_value, Decimal("300"))

    def test_cancel_dates_sle_and_gl_reversal_on_cancellation_day(self):
        receipt = PurchaseReceipt.objects.create(
            supplier_name="Supplier", warehouse=self.store, posting_date=date(2026, 1, 15)
        )
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=2, rate=100)
        submit_purchase_receipt(receipt)
        cancel_purchase_receipt(receipt)
        reversal_sle = StockLedgerEntry.objects.get(
            voucher_type="Purchase Receipt Cancellation", voucher_no=str(receipt.pk)
        )
        reversal_gl = GLEntry.objects.filter(
            voucher_type="Purchase Receipt", voucher_no=str(receipt.pk), is_cancelled=False
        ).first()
        self.assertEqual(reversal_sle.posting_date, timezone.localdate())
        self.assertEqual(reversal_gl.posting_date, timezone.localdate())
