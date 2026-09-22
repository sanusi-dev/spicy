from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.accounting.models import GLEntry
from apps.inventory.forms import PurchaseReceiptItemForm, StockEntryDetailForm
from apps.inventory.models import (
    UOM,
    Bin,
    Item,
    ItemGroup,
    ItemUOMConversion,
    PurchaseReceipt,
    PurchaseReceiptItem,
    StockEntry,
    StockEntryDetail,
    StockLedgerEntry,
    Warehouse,
)
from apps.inventory.services import (
    cancel_purchase_receipt,
    cancel_stock_entry,
    submit_purchase_receipt,
    submit_stock_entry,
)
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import Restaurant


class UOMConversionTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.accounting.tests.helpers import setup_chart_of_accounts

        cls.bottle, _ = UOM.objects.get_or_create(name="Bottle")
        cls.crate, _ = UOM.objects.get_or_create(name="Crate")
        cls.kg, _ = UOM.objects.get_or_create(name="Kg")
        cls.bag, _ = UOM.objects.get_or_create(name="Bag")
        cls.plate, _ = UOM.objects.get_or_create(name="Plate")
        cls.group = ItemGroup.objects.create(name="Drinks")
        cls.food_group = ItemGroup.objects.create(name="Food")
        cls.store = Warehouse.objects.create(name="Store")
        cls.restaurant = Restaurant.objects.create(company="Test", store_warehouse=cls.store)
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        cls.store.account = cls.accounts["stock_in_hand"]
        cls.store.save(update_fields=["account", "updated_at"])
        cls.drink = Item.objects.create(
            item_name="Star Lager",
            item_group=cls.group,
            stock_uom=cls.bottle,
            department="DRINKS",
            is_stock_item=True,
            is_sales_item=True,
            is_purchase_item=True,
        )
        cls.rice = Item.objects.create(
            item_name="Raw Rice",
            item_group=cls.food_group,
            stock_uom=cls.kg,
            department="FOOD",
            is_stock_item=True,
            is_purchase_item=True,
            is_sales_item=False,
        )
        cls.dish = Item.objects.create(
            item_name="Jollof Rice",
            item_group=cls.food_group,
            stock_uom=cls.plate,
            department="FOOD",
            is_stock_item=False,
            is_purchase_item=False,
            is_sales_item=True,
        )

    def _conversion(self, item=None, uom=None, factor="24"):
        return ItemUOMConversion.objects.create(
            item=item or self.drink,
            uom=uom or self.crate,
            conversion_factor=Decimal(factor),
        )


class ItemUOMConversionValidationTest(UOMConversionTestBase):
    def test_clean_rejects_stock_uom_row(self):
        conv = ItemUOMConversion(item=self.drink, uom=self.bottle, conversion_factor=Decimal("1"))
        with self.assertRaises(ValidationError):
            conv.full_clean()

    def test_clean_rejects_zero_factor(self):
        conv = ItemUOMConversion(item=self.drink, uom=self.crate, conversion_factor=Decimal("0"))
        with self.assertRaises(ValidationError):
            conv.full_clean()

    def test_clean_rejects_virtual_food(self):
        conv = ItemUOMConversion(item=self.dish, uom=self.bag, conversion_factor=Decimal("10"))
        with self.assertRaises(ValidationError):
            conv.full_clean()

    def test_clean_rejects_template(self):
        template = Item.objects.create(
            item_name="Chicken",
            item_group=self.food_group,
            stock_uom=self.plate,
            department="FOOD",
            has_variants=True,
            is_stock_item=False,
            is_sales_item=False,
            is_purchase_item=False,
        )
        conv = ItemUOMConversion(item=template, uom=self.crate, conversion_factor=Decimal("12"))
        with self.assertRaises(ValidationError):
            conv.full_clean()

    def test_unique_item_uom(self):
        self._conversion()
        dup = ItemUOMConversion(item=self.drink, uom=self.crate, conversion_factor=Decimal("12"))
        with self.assertRaises(ValidationError):
            dup.full_clean()

    def test_parent_must_be_stock_and_purchase(self):
        self.drink.is_purchase_item = False
        self.drink.save(update_fields=["is_purchase_item", "updated_at"])
        conv = ItemUOMConversion(item=self.drink, uom=self.crate, conversion_factor=Decimal("24"))
        with self.assertRaises(ValidationError):
            conv.full_clean()

    def test_item_clean_blocks_stock_uom_change_while_rows_exist(self):
        self._conversion()
        self.drink.stock_uom = self.kg
        with self.assertRaises(ValidationError):
            self.drink.full_clean()

    def test_item_clean_blocks_flag_off_while_rows_exist(self):
        self._conversion()
        self.drink.is_purchase_item = False
        with self.assertRaises(ValidationError):
            self.drink.full_clean()

    def test_uom_factor_stock_unit_is_one(self):
        self.assertEqual(self.drink.uom_factor(self.bottle), Decimal("1"))

    def test_uom_factor_from_row(self):
        self._conversion(factor="24")
        self.assertEqual(self.drink.uom_factor(self.crate), Decimal("24"))

    def test_uom_factor_unknown_raises(self):
        with self.assertRaises(ValidationError):
            self.drink.uom_factor(self.crate)


class PurchaseReceiptConversionTest(UOMConversionTestBase):
    def _line(self, receipt, item=None, qty="5", rate="12000", uom=None):
        return PurchaseReceiptItem.objects.create(
            purchase_receipt=receipt,
            item=item or self.drink,
            received_qty=Decimal(qty),
            rate=Decimal(rate),
            uom=uom,
        )

    def test_save_derives_factor_one_for_stock_uom(self):
        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.store)
        line = self._line(receipt, uom=self.bottle)
        self.assertEqual(line.conversion_factor, Decimal("1"))
        self.assertEqual(line.uom_id, self.bottle.pk)

    def test_save_derives_factor_from_row(self):
        self._conversion()
        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.store)
        line = self._line(receipt, uom=self.crate)
        self.assertEqual(line.conversion_factor, Decimal("24"))

    def test_save_rejects_unknown_uom(self):
        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.store)
        with self.assertRaises(ValidationError):
            self._line(receipt, uom=self.crate)

    def test_submit_converts_crate_to_bottles_and_keeps_as_bought_money(self):
        self._conversion()
        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.store)
        line = self._line(receipt, qty="5", rate="12000", uom=self.crate)
        submit_purchase_receipt(receipt)

        sle = StockLedgerEntry.objects.get(voucher_type="Purchase Receipt", voucher_no=str(receipt.pk))
        self.assertEqual(sle.quantity, Decimal("120"))
        self.assertEqual(sle.stock_value_change, Decimal("60000"))
        self.assertEqual(sle.unit_rate, Decimal("500"))
        stock_bin = Bin.objects.get(item=self.drink, warehouse=self.store)
        self.assertEqual(stock_bin.actual_qty, Decimal("120"))
        self.assertEqual(stock_bin.valuation_rate, Decimal("500"))
        self.drink.refresh_from_db()
        self.assertEqual(self.drink.last_purchase_rate, Decimal("500"))
        gl_total = GLEntry.objects.filter(
            voucher_type="Purchase Receipt", voucher_no=str(receipt.pk), is_cancelled=False, credit__gt=0
        ).get()
        self.assertEqual(gl_total.credit, Decimal("60000"))
        self.assertEqual(line.amount, Decimal("60000"))

    def test_wac_blends_using_as_bought_amount(self):
        self._conversion()
        StockLedgerEntry.create_entry(
            item=self.drink,
            warehouse=self.store,
            quantity=Decimal("80"),
            voucher_type="Opening",
            voucher_no="1",
            unit_rate=Decimal("400"),
        )
        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.store)
        self._line(receipt, qty="5", rate="12000", uom=self.crate)
        submit_purchase_receipt(receipt)
        stock_bin = Bin.objects.get(item=self.drink, warehouse=self.store)
        self.assertEqual(stock_bin.actual_qty, Decimal("200"))
        expected = (Decimal("80") * Decimal("400") + Decimal("60000")) / Decimal("200")
        self.assertEqual(stock_bin.valuation_rate, expected.quantize(Decimal("0.01")))

    def test_repeating_decimal_keeps_stock_value_equal_to_amount(self):
        ItemUOMConversion.objects.create(item=self.drink, uom=self.crate, conversion_factor=Decimal("3"))
        receipt = PurchaseReceipt.objects.create(supplier_name="Supplier", warehouse=self.store)
        line = self._line(receipt, qty="1", rate="100", uom=self.crate)
        submit_purchase_receipt(receipt)
        sle = StockLedgerEntry.objects.get(voucher_type="Purchase Receipt", voucher_no=str(receipt.pk))
        self.assertEqual(sle.quantity, Decimal("3"))
        self.assertEqual(sle.unit_rate, Decimal("33.33"))
        self.assertEqual(sle.stock_value_change, line.amount)
        self.assertEqual(sle.stock_value_change, Decimal("100"))
        self.assertNotEqual(sle.quantity * sle.unit_rate, line.amount)

    def test_cancel_restores_prior_per_stock_unit_rate(self):
        self._conversion()
        first = PurchaseReceipt.objects.create(supplier_name="A", warehouse=self.store)
        self._line(first, qty="1", rate="9600", uom=self.crate)
        submit_purchase_receipt(first)
        self.drink.refresh_from_db()
        self.assertEqual(self.drink.last_purchase_rate, Decimal("400"))

        second = PurchaseReceipt.objects.create(supplier_name="B", warehouse=self.store)
        self._line(second, qty="1", rate="12000", uom=self.crate)
        submit_purchase_receipt(second)
        self.drink.refresh_from_db()
        self.assertEqual(self.drink.last_purchase_rate, Decimal("500"))

        cancel_purchase_receipt(second)
        self.drink.refresh_from_db()
        self.assertEqual(self.drink.last_purchase_rate, Decimal("400"))

    def test_cancel_clears_rate_when_no_prior_receipt(self):
        self._conversion()
        receipt = PurchaseReceipt.objects.create(supplier_name="A", warehouse=self.store)
        self._line(receipt, qty="1", rate="12000", uom=self.crate)
        submit_purchase_receipt(receipt)
        cancel_purchase_receipt(receipt)
        self.drink.refresh_from_db()
        self.assertIsNone(self.drink.last_purchase_rate)

    def test_stock_entry_market_receipt_converts_uom(self):
        self._conversion()
        mode, _ = ModeOfPayment.objects.get_or_create(name="Cash UOM", defaults={"type": "CASH", "enabled": True})
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=mode, defaults={"default_account": self.accounts["cash"]}
        )
        entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT", mode_of_payment=mode)
        line = StockEntryDetail.objects.create(
            stock_entry=entry, item=self.drink, qty=Decimal("5"), basic_rate=Decimal("12000"), uom=self.crate
        )
        submit_stock_entry(entry)
        sle = StockLedgerEntry.objects.get(voucher_type="Stock Entry", voucher_no=str(entry.pk))
        self.assertEqual(line.conversion_factor, Decimal("24"))
        self.assertEqual(sle.quantity, Decimal("120"))
        self.assertEqual(sle.stock_value_change, Decimal("60000"))
        self.assertEqual(sle.unit_rate, Decimal("500"))
        stock_bin = Bin.objects.get(item=self.drink, warehouse=self.store)
        self.assertEqual(stock_bin.actual_qty, Decimal("120"))
        self.assertEqual(stock_bin.valuation_rate, Decimal("500"))
        gl = GLEntry.objects.filter(
            voucher_type="Stock Entry", voucher_no=str(entry.pk), is_cancelled=False, credit__gt=0
        ).get()
        self.assertEqual(gl.credit, Decimal("60000"))
        self.drink.refresh_from_db()
        self.assertEqual(self.drink.last_purchase_rate, Decimal("500"))

    def test_stock_entry_market_receipt_cancel_uses_original_amount(self):
        self._conversion()
        mode, _ = ModeOfPayment.objects.get_or_create(name="Cash UOM2", defaults={"type": "CASH", "enabled": True})
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=mode, defaults={"default_account": self.accounts["cash"]}
        )
        entry = StockEntry.objects.create(purpose="MATERIAL_RECEIPT", mode_of_payment=mode)
        StockEntryDetail.objects.create(
            stock_entry=entry, item=self.drink, qty=Decimal("1"), basic_rate=Decimal("100"), uom=self.crate
        )
        submit_stock_entry(entry)
        cancel_stock_entry(entry)
        funding_gl = GLEntry.objects.filter(
            voucher_type="Stock Entry",
            voucher_no=str(entry.pk),
            is_cancelled=False,
            account=self.accounts["cash"],
        ).get()
        # The funding leg returns the original as-bought money, not qty × rounded WAC.
        self.assertEqual(funding_gl.debit, Decimal("100"))


class PurchaseReceiptUOMFormTest(UOMConversionTestBase):
    def test_uom_queryset_is_stock_plus_conversions(self):
        self._conversion()
        form = PurchaseReceiptItemForm(instance=PurchaseReceiptItem(item=self.drink))
        ids = set(form.fields["uom"].queryset.values_list("pk", flat=True))
        self.assertEqual(ids, {self.bottle.pk, self.crate.pk})

    def test_clean_rejects_uom_not_on_table(self):
        form = PurchaseReceiptItemForm(
            data={
                "item": str(self.drink.pk),
                "received_qty": "1",
                "uom": str(self.crate.pk),
                "rate": "100",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("uom", form.errors)

    def test_preview_endpoint(self):
        from django.contrib.auth.models import Group

        from apps.users.models import CustomUser

        user = CustomUser.objects.create_user(username="mgr@test.com", password="x", email="mgr@test.com")
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        user.groups.add(mgr)
        self.client.login(username="mgr@test.com", password="x")
        self._conversion()
        response = self.client.get(
            reverse("inventory:purchase_receipt_stock_qty_preview"),
            {"items-0-item": self.drink.pk, "items-0-uom": self.crate.pk, "items-0-received_qty": "5"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "5 Crate = 120 Bottle")

    def test_item_meta_defaults_to_stock_uom(self):
        from django.contrib.auth.models import Group

        from apps.users.models import CustomUser

        user = CustomUser.objects.create_user(username="mgr2@test.com", password="x", email="mgr2@test.com")
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        user.groups.add(mgr)
        self.client.login(username="mgr2@test.com", password="x")
        self._conversion()
        response = self.client.get(
            reverse("inventory:purchase_receipt_item_meta"),
            {"items-0-item": self.drink.pk, "items-0-received_qty": "5"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.bottle.name)
        self.assertContains(response, self.crate.name)
        self.assertContains(response, f'value="{self.bottle.pk}"', html=False)


class StockEntryUOMFormTest(UOMConversionTestBase):
    def _form_data(self, **overrides):
        data = {
            "purpose": "MATERIAL_RECEIPT",
            "item": str(self.drink.pk),
            "qty": "5",
            "uom": str(self.crate.pk),
            "basic_rate": "12000",
        }
        data.update(overrides)
        return data

    def test_uom_queryset_is_stock_plus_conversions(self):
        self._conversion()
        form = StockEntryDetailForm(instance=StockEntryDetail(item=self.drink))
        ids = set(form.fields["uom"].queryset.values_list("pk", flat=True))
        self.assertEqual(ids, {self.bottle.pk, self.crate.pk})

    def test_clean_rejects_uom_not_on_table(self):
        form = StockEntryDetailForm(data=self._form_data())
        self.assertFalse(form.is_valid())
        self.assertIn("uom", form.errors)

    def _login(self, username):
        from django.contrib.auth.models import Group

        from apps.users.models import CustomUser

        user = CustomUser.objects.create_user(username=username, password="x", email=username)
        mgr, _ = Group.objects.get_or_create(name="Spicy Manager")
        user.groups.add(mgr)
        self.client.login(username=username, password="x")

    def test_stock_entry_stock_qty_preview_endpoint(self):
        self._login("mgr3@test.com")
        self._conversion()
        response = self.client.get(
            reverse("inventory:stock_entry_stock_qty_preview"),
            {"items-0-item": self.drink.pk, "items-0-uom": self.crate.pk, "items-0-qty": "5"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "5 Crate = 120 Bottle")

    def test_stock_entry_item_meta_defaults_to_stock_uom(self):
        self._login("mgr4@test.com")
        self._conversion()
        response = self.client.get(
            reverse("inventory:stock_entry_item_meta"),
            {"items-0-item": self.drink.pk, "items-0-qty": "5"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.bottle.name)
        self.assertContains(response, self.crate.name)
        self.assertContains(response, f'value="{self.bottle.pk}"', html=False)
