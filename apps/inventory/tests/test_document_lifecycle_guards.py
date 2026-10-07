from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.inventory.models import (
    PurchaseReceipt,
    StockEntry,
    StockReconciliation,
    Warehouse,
    persist_inventory_lifecycle,
)


class InventoryDocumentLifecycleGuardTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.warehouse = Warehouse.objects.create(name="Store")

    def test_stock_entry_cannot_be_created_submitted(self):
        with self.assertRaisesMessage(ValidationError, "submit or cancel service"):
            StockEntry.objects.create(purpose="MATERIAL_RECEIPT", status="SUBMITTED")

    def test_stock_entry_cannot_flip_status_on_save(self):
        entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT")
        entry.status = "SUBMITTED"
        with self.assertRaisesMessage(ValidationError, "submit or cancel service"):
            entry.save(update_fields=["status", "updated_at"])

    def test_stock_entry_submit_flag_persists(self):
        entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT")
        entry.status = "SUBMITTED"
        persist_inventory_lifecycle(entry, submit=True, update_fields=["status", "updated_at"])
        entry.refresh_from_db()
        self.assertEqual(entry.status, "SUBMITTED")

    def test_submitted_stock_entry_cannot_be_cancelled_without_flag(self):
        entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT")
        entry.status = "SUBMITTED"
        persist_inventory_lifecycle(entry, submit=True, update_fields=["status", "updated_at"])
        entry.status = "CANCELLED"
        with self.assertRaisesMessage(ValidationError, "Cannot modify a submitted"):
            entry.save(update_fields=["status", "updated_at"])

    def test_reconciliation_and_receipt_share_the_same_guard(self):
        rec = StockReconciliation.objects.create(reason="ADJUSTMENT", warehouse=self.warehouse)
        rec.status = "SUBMITTED"
        with self.assertRaisesMessage(ValidationError, "submit or cancel service"):
            rec.save(update_fields=["status", "updated_at"])

        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.warehouse)
        receipt.status = "CANCELLED"
        with self.assertRaisesMessage(ValidationError, "submit or cancel service"):
            receipt.save(update_fields=["status", "updated_at"])
