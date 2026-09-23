from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.inventory.models import (
    UOM,
    Bin,
    InsufficientStock,
    Item,
    ItemGroup,
    StockLedgerEntry,
    Warehouse,
)


class StockLedgerEntryTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.uom = UOM.objects.create(name="Nos")
        cls.group = ItemGroup.objects.create(name="Food")
        cls.warehouse = Warehouse.objects.create(name="Main Store")
        cls.item = Item.objects.create(
            item_name="Jollof Rice",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="FOOD",
        )


class WACInboundTest(StockLedgerEntryTestBase):
    def test_receipt_creates_wac_sle(self):
        sle = StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.warehouse,
            quantity=Decimal("10"),
            voucher_type="Stock Entry",
            voucher_no="SE1",
            unit_rate=Decimal("100"),
        )
        self.assertEqual(sle.quantity, Decimal("10"))
        self.assertEqual(sle.unit_rate, Decimal("100"))
        self.assertEqual(sle.stock_value_change, Decimal("1000"))

    def test_inbound_blend(self):
        StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            voucher_type="T",
            voucher_no="1",
            unit_rate=Decimal("100"),
        )
        StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.warehouse,
            quantity=Decimal("2"),
            voucher_type="T",
            voucher_no="2",
            unit_rate=Decimal("150"),
        )
        bin_obj = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        self.assertEqual(bin_obj.actual_qty, Decimal("7"))
        expected_wac = (Decimal("5") * Decimal("100") + Decimal("2") * Decimal("150")) / Decimal("7")
        self.assertEqual(bin_obj.valuation_rate, expected_wac.quantize(Decimal("0.01")))
        self.assertEqual(bin_obj.stock_value, bin_obj.actual_qty * bin_obj.valuation_rate)

    def test_outbound_at_current_wac(self):
        StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.warehouse,
            quantity=Decimal("10"),
            voucher_type="T",
            voucher_no="1",
            unit_rate=Decimal("100"),
        )
        StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            voucher_type="T",
            voucher_no="2",
            unit_rate=Decimal("200"),
        )
        bin_before = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        wac_before = bin_before.valuation_rate
        sle = StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.warehouse,
            quantity=Decimal("-4"),
            voucher_type="T",
            voucher_no="3",
        )
        self.assertEqual(sle.unit_rate, wac_before)
        self.assertEqual(sle.stock_value_change, Decimal("-4") * wac_before)
        bin_after = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        self.assertEqual(bin_after.actual_qty, Decimal("11"))
        self.assertEqual(bin_after.valuation_rate, wac_before)

    def test_zero_qty_rejected(self):
        StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            voucher_type="T",
            voucher_no="1",
            unit_rate=Decimal("100"),
        )
        with self.assertRaises(ValidationError):
            StockLedgerEntry.create_entry(
                item=self.item,
                warehouse=self.warehouse,
                quantity=Decimal("0"),
                voucher_type="T",
                voucher_no="2",
            )

    def test_seed_from_zero_with_rate(self):
        sle = StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            voucher_type="T",
            voucher_no="1",
            unit_rate=Decimal("80"),
        )
        bin_obj = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        self.assertEqual(bin_obj.valuation_rate, Decimal("80"))
        self.assertEqual(sle.unit_rate, Decimal("80"))

    def test_seed_from_zero_without_rate_keeps_zero(self):
        sle = StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            voucher_type="T",
            voucher_no="1",
            unit_rate=None,
        )
        bin_obj = Bin.objects.get(item=self.item, warehouse=self.warehouse)
        # No rate supplied on empty bin -> identity blend with wac 0 -> stays 0
        self.assertEqual(bin_obj.valuation_rate, Decimal("0"))
        self.assertEqual(sle.unit_rate, Decimal("0"))


class NegativeStockTest(StockLedgerEntryTestBase):
    def test_outbound_beyond_available_raises(self):
        StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            voucher_type="T",
            voucher_no="1",
            unit_rate=Decimal("100"),
        )
        with self.assertRaises(InsufficientStock):
            StockLedgerEntry.create_entry(
                item=self.item,
                warehouse=self.warehouse,
                quantity=Decimal("-6"),
                voucher_type="T",
                voucher_no="2",
            )
        # No SLE created for failed move
        self.assertEqual(StockLedgerEntry.objects.count(), 1)
        self.assertEqual(Bin.objects.get(item=self.item, warehouse=self.warehouse).actual_qty, Decimal("5"))


class FutureDateTest(StockLedgerEntryTestBase):
    def test_future_posting_date_rejected(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        with self.assertRaises(ValidationError):
            StockLedgerEntry.create_entry(
                item=self.item,
                warehouse=self.warehouse,
                quantity=Decimal("1"),
                voucher_type="T",
                voucher_no="1",
                unit_rate=Decimal("100"),
                posting_date=tomorrow,
            )
