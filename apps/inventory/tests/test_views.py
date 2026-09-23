from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounting.payables_models import Supplier
from apps.inventory.models import (
    UOM,
    Item,
    ItemGroup,
    PurchaseReceipt,
    PurchaseReceiptItem,
    StockEntry,
    StockEntryDetail,
    Warehouse,
)
from apps.inventory.services import submit_purchase_receipt, submit_stock_entry
from apps.settings.models import Restaurant
from apps.users.models import CustomUser


class InventoryViewTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.accounting.tests.helpers import setup_chart_of_accounts

        cls.user = CustomUser.objects.create_user(
            username="admin@test.com", password="testpass123", email="admin@test.com"
        )
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        cls.user.groups.add(mgr)
        cls.uom = UOM.objects.create(name="Nos")
        cls.group = ItemGroup.objects.create(name="Food")
        cls.warehouse = Warehouse.objects.create(name="Main Store")
        cls.restaurant = Restaurant.objects.create(company="Test Restaurant", store_warehouse=cls.warehouse)
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        cls.warehouse.refresh_from_db()
        if not cls.warehouse.account_id:
            cls.warehouse.account = cls.accounts["stock_in_hand"]
            cls.warehouse.save(update_fields=["account", "updated_at"])
        cls.item = Item.objects.create(
            item_name="Jollof Rice",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="FOOD",
            is_purchase_item=True,
        )
        from apps.payments.models import ModeOfPayment, PaymentGLMapping

        cls.cash_mode = ModeOfPayment.objects.filter(name="Cash").first()
        if cls.cash_mode is None:
            cls.cash_mode = ModeOfPayment.objects.create(name="Cash View", type="CASH", enabled=True)
        # Ensure mapping to cash account for market receipts
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=cls.cash_mode, defaults={"default_account": cls.accounts["cash"]}
        )
        # If mapping exists but points elsewhere, keep it; tests only need a valid mapping
        if not PaymentGLMapping.objects.filter(mode_of_payment=cls.cash_mode).exists():
            PaymentGLMapping.objects.create(mode_of_payment=cls.cash_mode, default_account=cls.accounts["cash"])

    def setUp(self):
        self.client.login(username="admin@test.com", password="testpass123")


class TestStockEntryViews(InventoryViewTestBase):
    def test_stock_entry_create_rolls_back_parent_when_formset_save_fails(self):
        with (
            patch("apps.inventory.views.StockEntryDetailFormSet.save", side_effect=RuntimeError("failed")),
            self.assertRaisesMessage(RuntimeError, "failed"),
        ):
            self.client.post(
                reverse("inventory:stock_entry_create"),
                {
                    "purpose": "MATERIAL_RECEIPT",
                    "posting_date": "2025-01-15",
                    "mode_of_payment": str(self.cash_mode.pk),
                    "remarks": "atomic failure",
                    "items-TOTAL_FORMS": "0",
                    "items-INITIAL_FORMS": "0",
                    "items-MIN_NUM_FORMS": "0",
                    "items-MAX_NUM_FORMS": "1000",
                },
            )
        self.assertFalse(StockEntry.objects.filter(remarks="atomic failure").exists())

    def test_stock_entry_submit_post(self):
        entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT", mode_of_payment=self.cash_mode)
        StockEntryDetail.objects.create(
            stock_entry=entry,
            item=self.item,
            target_warehouse=self.warehouse,
            qty=Decimal("10"),
            basic_rate=Decimal("100"),
        )
        response = self.client.post(reverse("inventory:stock_entry_submit", kwargs={"pk": entry.pk}))
        self.assertRedirects(response, reverse("inventory:stock_entry_detail", kwargs={"pk": entry.pk}))
        entry.refresh_from_db()
        self.assertEqual(entry.status, "SUBMITTED")

    def test_stock_entry_submit_validation_error_is_visible(self):
        entry = StockEntry.objects.create(purpose="MATERIAL_TRANSFER")
        StockEntryDetail.objects.create(stock_entry=entry, item=self.item, qty=Decimal("10"))
        response = self.client.post(reverse("inventory:stock_entry_submit", kwargs={"pk": entry.pk}), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bar / POS sales warehouse")
        entry.refresh_from_db()
        self.assertEqual(entry.status, "DRAFT")

    def test_stock_entry_submit_htmx_error_returns_hx_redirect(self):
        entry = StockEntry.objects.create(purpose="MATERIAL_TRANSFER")
        StockEntryDetail.objects.create(stock_entry=entry, item=self.item, qty=Decimal("10"))
        response = self.client.post(
            reverse("inventory:stock_entry_submit", kwargs={"pk": entry.pk}),
            HTTP_HX_REQUEST="true",
            HTTP_HX_BOOSTED="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], reverse("inventory:stock_entry_detail", kwargs={"pk": entry.pk}))
        # The queued message persists in the cookie and renders on the followed page.
        followed = self.client.get(reverse("inventory:stock_entry_detail", kwargs={"pk": entry.pk}))
        self.assertContains(followed, "Bar / POS sales warehouse")

    def test_stock_entry_submit_htmx_success_returns_hx_redirect(self):
        entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT", mode_of_payment=self.cash_mode)
        StockEntryDetail.objects.create(
            stock_entry=entry,
            item=self.item,
            target_warehouse=self.warehouse,
            qty=Decimal("10"),
            basic_rate=Decimal("100"),
        )
        response = self.client.post(
            reverse("inventory:stock_entry_submit", kwargs={"pk": entry.pk}),
            HTTP_HX_REQUEST="true",
            HTTP_HX_BOOSTED="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], reverse("inventory:stock_entry_detail", kwargs={"pk": entry.pk}))
        entry.refresh_from_db()
        self.assertEqual(entry.status, "SUBMITTED")

    def test_stock_entry_cancel_post(self):
        entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT", mode_of_payment=self.cash_mode)
        StockEntryDetail.objects.create(
            stock_entry=entry,
            item=self.item,
            target_warehouse=self.warehouse,
            qty=Decimal("10"),
            basic_rate=Decimal("100"),
        )
        submit_stock_entry(entry)
        response = self.client.post(reverse("inventory:stock_entry_cancel", kwargs={"pk": entry.pk}))
        self.assertRedirects(response, reverse("inventory:stock_entry_detail", kwargs={"pk": entry.pk}))
        entry.refresh_from_db()
        self.assertEqual(entry.status, "CANCELLED")
        detail_response = self.client.get(reverse("inventory:stock_entry_detail", kwargs={"pk": entry.pk}))
        self.assertContains(detail_response, "Stock Entry Cancellation")


class TestReconciliationViews(InventoryViewTestBase):
    def test_reconciliation_create_rolls_back_parent_when_formset_save_fails(self):
        from apps.inventory.models import StockReconciliation

        with (
            patch("apps.inventory.views.StockReconciliationItemFormSet.save", side_effect=RuntimeError("failed")),
            self.assertRaisesMessage(RuntimeError, "failed"),
        ):
            self.client.post(
                reverse("inventory:reconciliation_create"),
                {
                    "reason": "ADJUSTMENT",
                    "posting_date": "2025-01-15",
                    "warehouse": self.warehouse.pk,
                    "remarks": "atomic failure",
                    "items-TOTAL_FORMS": "0",
                    "items-INITIAL_FORMS": "0",
                    "items-MIN_NUM_FORMS": "0",
                    "items-MAX_NUM_FORMS": "1000",
                },
            )
        self.assertFalse(StockReconciliation.objects.filter(remarks="atomic failure").exists())

    def test_reconciliation_submit_validation_error_is_visible(self):
        from apps.inventory.models import StockReconciliation

        rec = StockReconciliation.objects.create(warehouse=self.warehouse, reason="ADJUSTMENT")
        response = self.client.post(reverse("inventory:reconciliation_submit", kwargs={"pk": rec.pk}), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add at least one item")

    def test_reconciliation_detail_includes_cancellation_history_and_confirmation(self):
        from apps.inventory.models import StockLedgerEntry, StockReconciliation, StockReconciliationItem
        from apps.inventory.services import submit_stock_reconciliation

        rec = StockReconciliation.objects.create(warehouse=self.warehouse, reason="ADJUSTMENT")
        StockReconciliationItem.objects.create(
            reconciliation=rec, item=self.item, qty=Decimal("4"), valuation_rate=Decimal("100")
        )
        submit_stock_reconciliation(rec)
        rec.refresh_from_db()
        # While submitted, the detail shows the cancel confirmation
        submitted_response = self.client.get(reverse("inventory:reconciliation_detail", kwargs={"pk": rec.pk}))
        self.assertContains(submitted_response, 'data-confirm-title="Cancel this reconciliation?"')
        self.client.post(reverse("inventory:reconciliation_cancel", kwargs={"pk": rec.pk}))

        response = self.client.get(reverse("inventory:reconciliation_detail", kwargs={"pk": rec.pk}))
        self.assertContains(response, "Stock Reconciliation Cancellation")
        self.assertEqual(
            StockLedgerEntry.objects.filter(voucher_no=str(rec.pk)).count(),
            2,
        )

    def test_reconciliation_submit_and_cancel_record_actors(self):
        from apps.inventory.models import StockReconciliation, StockReconciliationItem

        rec = StockReconciliation.objects.create(warehouse=self.warehouse, reason="ADJUSTMENT")
        StockReconciliationItem.objects.create(
            reconciliation=rec, item=self.item, qty=Decimal("4"), valuation_rate=Decimal("100")
        )
        self.client.post(reverse("inventory:reconciliation_submit", kwargs={"pk": rec.pk}))
        rec.refresh_from_db()
        self.assertEqual(rec.submitted_by, self.user)

        self.client.post(reverse("inventory:reconciliation_cancel", kwargs={"pk": rec.pk}))
        rec.refresh_from_db()
        self.assertEqual(rec.cancelled_by, self.user)


class TestRecipeViews(InventoryViewTestBase):
    def _dish_and_ingredient(self):
        from apps.inventory.models import Item

        dish = Item.objects.create(
            item_name="Test Jollof",
            item_group=self.group,
            stock_uom=self.uom,
            department="FOOD",
            is_sales_item=True,
            is_stock_item=False,
            is_purchase_item=False,
        )
        return dish, self.item

    def test_recipe_open_redirects_to_detail_or_create(self):
        from apps.inventory.models import Recipe, RecipeItem

        dish, ingredient = self._dish_and_ingredient()
        create_url = reverse("inventory:recipe_create")
        response = self.client.get(reverse("inventory:recipe_open", kwargs={"item_id": dish.pk}))
        self.assertRedirects(response, f"{create_url}?item={dish.pk}")
        recipe = Recipe.objects.create(item=dish, output_qty=Decimal("1"))
        RecipeItem.objects.create(recipe=recipe, ingredient=ingredient, qty=Decimal("0.2"))
        response = self.client.get(reverse("inventory:recipe_open", kwargs={"item_id": dish.pk}))
        self.assertRedirects(response, reverse("inventory:recipe_detail", kwargs={"pk": recipe.pk}))


class TestPurchaseReceiptViews(InventoryViewTestBase):
    def test_purchase_receipt_create_with_supplier_master_and_no_name(self):
        supplier = Supplier.objects.create(supplier_name="Master Foods Ltd")
        response = self.client.post(
            reverse("inventory:purchase_receipt_create"),
            {
                "supplier": str(supplier.pk),
                "supplier_delivery_note": "DN-001",
                "posting_date": "2025-01-15",
                "remarks": "",
                "items-TOTAL_FORMS": "0",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000",
            },
        )
        receipt = PurchaseReceipt.objects.filter(supplier=supplier).first()
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.supplier_name, "Master Foods Ltd")
        self.assertRedirects(response, reverse("inventory:purchase_receipt_detail", kwargs={"pk": receipt.pk}))

    def test_purchase_receipt_create_free_text_name_wins_over_master(self):
        supplier = Supplier.objects.create(supplier_name="Master Foods Ltd")
        response = self.client.post(
            reverse("inventory:purchase_receipt_create"),
            {
                "supplier_name": "Typo Corp",
                "supplier": str(supplier.pk),
                "supplier_delivery_note": "DN-001",
                "posting_date": "2025-01-15",
                "remarks": "",
                "items-TOTAL_FORMS": "0",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000",
            },
        )
        receipt = PurchaseReceipt.objects.filter(supplier=supplier).first()
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.supplier_name, "Typo Corp")
        self.assertRedirects(response, reverse("inventory:purchase_receipt_detail", kwargs={"pk": receipt.pk}))

    def test_purchase_receipt_create_requires_name_when_no_master(self):
        response = self.client.post(
            reverse("inventory:purchase_receipt_create"),
            {
                "supplier_delivery_note": "",
                "posting_date": "2025-01-15",
                "remarks": "",
                "items-TOTAL_FORMS": "0",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        self.assertFalse(PurchaseReceipt.objects.exists())

    def test_purchase_receipt_create_rolls_back_parent_when_formset_save_fails(self):
        with (
            patch("apps.inventory.views.PurchaseReceiptItemFormSet.save", side_effect=RuntimeError("failed")),
            self.assertRaisesMessage(RuntimeError, "failed"),
        ):
            self.client.post(
                reverse("inventory:purchase_receipt_create"),
                {
                    "supplier_name": "Atomic Failure",
                    "supplier_delivery_note": "",
                    "posting_date": "2025-01-15",
                    "remarks": "",
                    "items-TOTAL_FORMS": "0",
                    "items-INITIAL_FORMS": "0",
                    "items-MIN_NUM_FORMS": "0",
                    "items-MAX_NUM_FORMS": "1000",
                },
            )
        self.assertFalse(PurchaseReceipt.objects.filter(supplier_name="Atomic Failure").exists())

    def test_purchase_receipt_submit_post(self):
        receipt = PurchaseReceipt.objects.create(
            supplier_name="ABC Suppliers",
            warehouse=self.warehouse,
        )
        PurchaseReceiptItem.objects.create(
            purchase_receipt=receipt,
            item=self.item,
            received_qty=Decimal("10"),
            rate=Decimal("100"),
        )
        response = self.client.post(reverse("inventory:purchase_receipt_submit", kwargs={"pk": receipt.pk}))
        self.assertRedirects(response, reverse("inventory:purchase_receipt_detail", kwargs={"pk": receipt.pk}))
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "SUBMITTED")

    def test_purchase_receipt_submit_validation_error_is_visible(self):
        receipt = PurchaseReceipt.objects.create(supplier_name="ABC Suppliers", warehouse=self.warehouse)
        response = self.client.post(
            reverse("inventory:purchase_receipt_submit", kwargs={"pk": receipt.pk}), follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add at least one item")
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "DRAFT")

    def test_purchase_receipt_detail_includes_cancellation_history_and_confirmations(self):
        from apps.inventory.models import PurchaseReceiptItem

        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.warehouse)
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=2, rate=100)
        submit_purchase_receipt(receipt)
        submitted_response = self.client.get(reverse("inventory:purchase_receipt_detail", kwargs={"pk": receipt.pk}))
        self.assertContains(submitted_response, 'data-confirm-title="Cancel this purchase receipt?"')

        self.client.post(reverse("inventory:purchase_receipt_cancel", kwargs={"pk": receipt.pk}))
        response = self.client.get(reverse("inventory:purchase_receipt_detail", kwargs={"pk": receipt.pk}))
        self.assertContains(response, "Purchase Receipt Cancellation")

        draft_receipt = PurchaseReceipt.objects.create(supplier_name="Draft Supplier", warehouse=self.warehouse)
        draft_response = self.client.get(reverse("inventory:purchase_receipt_detail", kwargs={"pk": draft_receipt.pk}))
        self.assertContains(draft_response, 'data-confirm-title="Submit this purchase receipt?"')

    def test_purchase_receipt_cancel_post(self):
        receipt = PurchaseReceipt.objects.create(
            supplier_name="ABC Suppliers",
            warehouse=self.warehouse,
        )
        PurchaseReceiptItem.objects.create(
            purchase_receipt=receipt,
            item=self.item,
            received_qty=Decimal("10"),
            rate=Decimal("100"),
        )
        submit_purchase_receipt(receipt)
        response = self.client.post(reverse("inventory:purchase_receipt_cancel", kwargs={"pk": receipt.pk}))
        self.assertRedirects(response, reverse("inventory:purchase_receipt_detail", kwargs={"pk": receipt.pk}))
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "CANCELLED")
