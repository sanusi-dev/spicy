from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.inventory.models import PurchaseReceipt, StockEntry, Warehouse
from apps.orders.models import Order
from apps.settings.models import Restaurant


class RestaurantModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.restaurant = Restaurant.objects.create(company="Test Restaurant Ltd")

    def test_singleton_rejects_second_record(self):
        r2 = Restaurant(company="Other")
        with self.assertRaises(ValidationError):
            r2.clean()

    def test_store_and_bar_warehouses_must_be_enabled_and_distinct(self):
        store = Warehouse.objects.create(name="Store")
        disabled = Warehouse.objects.create(name="Disabled", disabled=True)
        self.restaurant.store_warehouse = store
        self.restaurant.default_warehouse = store
        with self.assertRaises(ValidationError):
            self.restaurant.full_clean()
        self.restaurant.default_warehouse = disabled
        with self.assertRaises(ValidationError):
            self.restaurant.full_clean()

    def test_bar_change_rejected_with_snapshotted_normal_draft(self):
        old_bar = Warehouse.objects.create(name="Old Bar")
        new_bar = Warehouse.objects.create(name="New Bar")
        self.restaurant.default_warehouse = old_bar
        self.restaurant.save(update_fields=["default_warehouse"])
        Order.objects.create(stock_warehouse=old_bar)

        self.restaurant.default_warehouse = new_bar
        with self.assertRaisesMessage(ValidationError, "open POS orders"):
            self.restaurant.full_clean()

    def test_default_income_account_cannot_be_a_payment_account(self):
        from apps.accounting.models import LedgerAccount
        from apps.payments.models import ModeOfPayment, PaymentGLMapping

        cash = LedgerAccount.objects.create(
            name="Cash (restaurant test)",
            account_type=LedgerAccount.ASSET,
            report_type=LedgerAccount.BALANCE_SHEET,
        )
        PaymentGLMapping.objects.create(
            mode_of_payment=ModeOfPayment.objects.create(name="Test Cash", type="CASH"),
            default_account=cash,
        )
        self.restaurant.default_income_account = cash
        with self.assertRaisesMessage(ValidationError, "cannot record sales income"):
            self.restaurant.full_clean()

    def test_default_sales_returns_account_cannot_be_a_payment_account(self):
        from apps.accounting.models import LedgerAccount
        from apps.payments.models import ModeOfPayment, PaymentGLMapping

        cash = LedgerAccount.objects.create(
            name="Cash (returns test)",
            account_type=LedgerAccount.ASSET,
            report_type=LedgerAccount.BALANCE_SHEET,
        )
        PaymentGLMapping.objects.create(
            mode_of_payment=ModeOfPayment.objects.create(name="Test Returns Cash", type="CASH"),
            default_account=cash,
        )
        self.restaurant.default_sales_returns_account = cash
        with self.assertRaisesMessage(ValidationError, "cannot record sales returns"):
            self.restaurant.full_clean()

    def test_store_change_rejected_with_draft_stock_documents(self):
        old_store = Warehouse.objects.create(name="Old Store")
        new_store = Warehouse.objects.create(name="New Store")
        self.restaurant.store_warehouse = old_store
        self.restaurant.save(update_fields=["store_warehouse"])

        for document in (
            StockEntry.objects.create(purpose="MATERIAL_RECEIPT"),
            PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=old_store),
        ):
            with self.subTest(document=document):
                self.restaurant.store_warehouse = new_store
                with self.assertRaisesMessage(ValidationError, "draft stock documents"):
                    self.restaurant.full_clean()
                document.delete()
