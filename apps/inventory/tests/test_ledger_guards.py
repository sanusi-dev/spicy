"""Ledger immutability — StockLedgerEntry ORM guards and view-only Bin/SLE admins."""

from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from apps.inventory.admin import BinAdmin, StockLedgerEntryAdmin
from apps.inventory.models import UOM, Bin, InsufficientStock, Item, ItemGroup, StockLedgerEntry, Warehouse
from apps.users.models import CustomUser


class StockLedgerGuardTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.uom = UOM.objects.create(name="Nos")
        cls.group = ItemGroup.objects.create(name="Guard Group")
        cls.store = Warehouse.objects.create(name="Store")
        cls.item = Item.objects.create(
            item_name="Rice",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="FOOD",
            is_stock_item=True,
        )
        cls.superuser = CustomUser.objects.create_user(
            username="guardboss", password="testpass123", is_staff=True, is_superuser=True
        )
        cls.factory = RequestFactory()
        cls.sle = StockLedgerEntry.create_entry(
            item=cls.item,
            warehouse=cls.store,
            quantity=Decimal("2"),
            voucher_type="Opening",
            voucher_no="G1",
            unit_rate=Decimal("100"),
        )

    def _request(self):
        request = self.factory.get("/admin/")
        request.user = self.superuser
        return request


class StockLedgerEntryGuardTest(StockLedgerGuardTestBase):
    def test_unguarded_create_rejected(self):
        with self.assertRaisesMessage(ValidationError, "create_entry"):
            StockLedgerEntry.objects.create(
                item=self.item,
                warehouse=self.store,
                quantity=Decimal("-1"),
                voucher_type="Stock Entry",
                voucher_no="G2",
            )

    def test_unguarded_save_rejected(self):
        sle = StockLedgerEntry(
            item=self.item,
            warehouse=self.store,
            quantity=Decimal("1"),
            voucher_type="Stock Entry",
            voucher_no="G3",
        )
        with self.assertRaisesMessage(ValidationError, "create_entry"):
            sle.save()

    def test_unguarded_bulk_create_rejected(self):
        with self.assertRaisesMessage(ValidationError, "create_entry"):
            StockLedgerEntry.objects.bulk_create(
                [
                    StockLedgerEntry(
                        item=self.item,
                        warehouse=self.store,
                        quantity=Decimal("1"),
                        voucher_type="Stock Entry",
                        voucher_no="G4",
                    )
                ]
            )

    def test_update_rejected(self):
        self.sle.quantity = Decimal("5")
        with self.assertRaisesMessage(ValidationError, "immutable"):
            self.sle.save()

    def test_delete_rejected(self):
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            self.sle.delete()


class ReservedFloorGuardTest(StockLedgerGuardTestBase):
    def _bin(self, actual, reserved):
        bin_obj, _created = Bin.objects.get_or_create(item=self.item, warehouse=self.store)
        bin_obj.actual_qty = actual
        bin_obj.reserved_qty = reserved
        bin_obj.save(update_fields=["actual_qty", "reserved_qty", "updated_at"])
        return bin_obj

    def test_outbound_cannot_dip_below_reservations(self):
        self._bin(Decimal("5"), Decimal("8"))
        with self.assertRaisesMessage(InsufficientStock, "reserved for open orders"):
            StockLedgerEntry.create_entry(
                item=self.item,
                warehouse=self.store,
                quantity=Decimal("-3"),
                voucher_type="Stock Entry Cancellation",
                voucher_no="R1",
            )

    def test_outbound_within_reserved_floor_allowed(self):
        self._bin(Decimal("10"), Decimal("6"))
        StockLedgerEntry.create_entry(
            item=self.item,
            warehouse=self.store,
            quantity=Decimal("-2"),
            voucher_type="Stock Entry",
            voucher_no="R2",
        )
        refreshed = Bin.objects.get(item=self.item, warehouse=self.store)
        self.assertEqual((refreshed.actual_qty, refreshed.reserved_qty), (Decimal("8"), Decimal("6")))


class LedgerAdminViewOnlyTest(StockLedgerGuardTestBase):
    def test_sle_admin_is_view_only(self):
        admin_instance = StockLedgerEntryAdmin(StockLedgerEntry, AdminSite())
        request = self._request()
        self.assertFalse(admin_instance.has_add_permission(request))
        self.assertFalse(admin_instance.has_change_permission(request, self.sle))
        self.assertFalse(admin_instance.has_delete_permission(request, self.sle))

    def test_bin_admin_is_view_only(self):
        admin_instance = BinAdmin(Bin, AdminSite())
        request = self._request()
        bin_obj = Bin.objects.get_or_create(item=self.item, warehouse=self.store)[0]
        self.assertFalse(admin_instance.has_add_permission(request))
        self.assertFalse(admin_instance.has_change_permission(request, bin_obj))
        self.assertFalse(admin_instance.has_delete_permission(request, bin_obj))
