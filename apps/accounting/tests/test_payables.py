"""Supplier payables tests — models, GL posting, outstanding, and cancellation."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import GLEntry, LedgerAccount
from apps.accounting.payables_models import (
    Supplier,
    SupplierInvoice,
    SupplierInvoiceExpense,
    SupplierInvoiceItem,
    SupplierPayment,
    SupplierPaymentAllocation,
)
from apps.inventory.models import UOM, Item, ItemGroup, PurchaseReceipt, PurchaseReceiptItem, Warehouse
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.settings.models import Restaurant

from .helpers import setup_chart_of_accounts


def _submitted_receipt(store, supplier, item, qty=2, rate=100, posting_date=None):
    """Create and submit a purchase receipt with one item line, returning (receipt, line)."""
    receipt = PurchaseReceipt.objects.create(
        supplier=supplier,
        supplier_name=supplier.supplier_name,
        warehouse=store,
        posting_date=posting_date or date.today(),
    )
    receipt_line = PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=item, received_qty=qty, rate=rate)
    from apps.inventory.services import submit_purchase_receipt

    submit_purchase_receipt(receipt)
    return receipt, receipt_line


class PayablesTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.uom = UOM.objects.create(name="Nos")
        cls.store = Warehouse.objects.create(name="Store Payables")
        cls.restaurant = Restaurant.objects.create(company="Test Co", store_warehouse=cls.store)
        cls.accounts = setup_chart_of_accounts(cls.restaurant)
        cls.store.refresh_from_db()
        if not cls.store.account_id:
            cls.store.account = cls.accounts["stock_in_hand"]
            cls.store.save(update_fields=["account", "updated_at"])
            cls.store.refresh_from_db()
        cls.group = ItemGroup.objects.create(name=f"Supplies {cls.restaurant.pk}")
        cls.item = Item.objects.create(
            item_name="Rice",
            item_group=cls.group,
            stock_uom=cls.uom,
            department="FOOD",
            is_stock_item=True,
            is_purchase_item=True,
        )
        cls.supplier = Supplier.objects.create(supplier_name=f"Mama Bisi Foods {cls.restaurant.pk}")
        cls.cash, _ = ModeOfPayment.objects.get_or_create(name="Cash", defaults={"type": "CASH"})
        PaymentGLMapping.objects.get_or_create(
            mode_of_payment=cls.cash, defaults={"default_account": cls.accounts["cash"]}
        )

    def _make_invoice(self, supplier=None, **kwargs):
        defaults = {"supplier": supplier or self.supplier, "posting_date": date.today()}
        defaults.update(kwargs)
        return SupplierInvoice.objects.create(**defaults)

    def _make_receipt_invoice(self, qty=2, rate=100, posting_date=None):
        """Create a draft invoice linked to a submitted receipt."""
        receipt, _ = _submitted_receipt(
            self.store, self.supplier, self.item, qty=qty, rate=rate, posting_date=posting_date
        )
        return self._make_invoice(purchase_receipt=receipt, posting_date=posting_date or date.today())


class SupplierModelTest(PayablesTestBase):
    def test_payable_account_requires_leaf(self):
        with self.assertRaises(ValidationError):
            Supplier.objects.create(supplier_name="Bad", payable_account=self.accounts["assets"])

    def test_disabled_supplier_can_hold_history(self):
        self.supplier.disabled = True
        self.supplier.save()
        receipt = PurchaseReceipt.objects.create(
            supplier=self.supplier,
            supplier_name=self.supplier.supplier_name,
            warehouse=self.store,
            posting_date=date.today(),
        )
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=2, rate=100)
        from apps.inventory.services import submit_purchase_receipt

        submit_purchase_receipt(receipt)
        invoice = self._make_invoice(purchase_receipt=receipt)
        invoice.submit()
        self.assertEqual(invoice.status, SupplierInvoice.SUBMITTED)


class SupplierInvoiceSubmitTest(PayablesTestBase):
    def test_submit_generates_stock_lines_from_receipt(self):
        receipt = PurchaseReceipt.objects.create(
            supplier=self.supplier,
            supplier_name=self.supplier.supplier_name,
            warehouse=self.store,
            posting_date=date.today(),
        )
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=2, rate=100)
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=3, rate=50)
        from apps.inventory.services import submit_purchase_receipt

        submit_purchase_receipt(receipt)
        invoice = self._make_invoice(purchase_receipt=receipt)
        invoice.submit()

        invoice.refresh_from_db()
        lines = list(invoice.items.all())
        self.assertEqual(len(lines), 2)
        self.assertEqual(invoice.total, Decimal("350"))
        self.assertEqual(invoice.outstanding_amount, Decimal("350"))
        entries = GLEntry.objects.filter(voucher_type="Supplier Invoice", voucher_no=invoice.invoice_number)
        self.assertEqual(entries.count(), 2)
        grni_acct = self.restaurant.stock_received_but_not_billed_account
        self.assertEqual(entries.get(account=grni_acct).debit, Decimal("350"))
        self.assertEqual(entries.get(account=self.accounts["payable"]).credit, Decimal("350"))

    def test_invoice_copies_as_bought_qty_and_rate(self):
        crate, _ = UOM.objects.get_or_create(name="Crate")
        from apps.inventory.models import ItemUOMConversion

        ItemUOMConversion.objects.create(item=self.item, uom=crate, conversion_factor=Decimal("24"))
        receipt = PurchaseReceipt.objects.create(
            supplier=self.supplier,
            supplier_name=self.supplier.supplier_name,
            warehouse=self.store,
            posting_date=date.today(),
        )
        PurchaseReceiptItem.objects.create(
            purchase_receipt=receipt, item=self.item, received_qty=5, rate=12000, uom=crate
        )
        from apps.inventory.services import submit_purchase_receipt

        submit_purchase_receipt(receipt)
        invoice = self._make_invoice(purchase_receipt=receipt)
        invoice.submit()
        line = invoice.items.get()
        self.assertEqual(line.qty, Decimal("5"))
        self.assertEqual(line.rate, Decimal("12000"))
        grni_acct = self.restaurant.stock_received_but_not_billed_account
        receipt_gl = GLEntry.objects.get(
            voucher_type="Purchase Receipt",
            voucher_no=str(receipt.pk),
            account=grni_acct,
            is_cancelled=False,
        )
        invoice_gl = GLEntry.objects.get(
            voucher_type="Supplier Invoice",
            voucher_no=invoice.invoice_number,
            account=grni_acct,
            is_cancelled=False,
        )
        self.assertEqual(receipt_gl.credit, Decimal("60000"))
        self.assertEqual(invoice_gl.debit, Decimal("60000"))

    def test_submit_posts_expense_and_payable_legs(self):
        invoice = self._make_invoice()
        SupplierInvoiceExpense.objects.create(invoice=invoice, description="Cleaning", amount=500)
        invoice.submit()
        entries = GLEntry.objects.filter(voucher_type="Supplier Invoice", voucher_no=invoice.invoice_number)
        expense = entries.get(account=self.accounts["supplier_expense"])
        self.assertEqual(expense.debit, Decimal("500"))
        payable = entries.get(account=self.accounts["payable"])
        self.assertEqual(payable.credit, Decimal("500"))

    def test_submit_with_no_lines_rejected(self):
        invoice = self._make_invoice()
        with self.assertRaisesMessage(ValidationError, "Add at least one line"):
            invoice.submit()
        self.assertEqual(invoice.status, SupplierInvoice.DRAFT)

    def test_submit_fails_closed_without_payable_account(self):
        self.restaurant.default_payable_account = None
        self.restaurant.save()
        invoice = self._make_receipt_invoice()
        with self.assertRaisesMessage(ValidationError, "default payable account"):
            invoice.submit()
        self.assertEqual(invoice.status, SupplierInvoice.DRAFT)

    def test_submit_fails_without_default_supplier_expense_account(self):
        self.restaurant.default_supplier_expense_account = None
        self.restaurant.save()
        invoice = self._make_invoice()
        SupplierInvoiceExpense.objects.create(invoice=invoice, description="Cleaning", amount=500)
        with self.assertRaisesMessage(ValidationError, "default supplier expense account"):
            invoice.submit()
        self.assertEqual(invoice.status, SupplierInvoice.DRAFT)

    def test_submit_uses_supplier_payable_override(self):
        override = LedgerAccount.objects.create(
            name="Special Payable",
            parent=self.accounts["liabilities"],
            account_type=LedgerAccount.LIABILITY,
            report_type=LedgerAccount.BALANCE_SHEET,
        )
        self.supplier.payable_account = override
        self.supplier.save()
        invoice = self._make_receipt_invoice()
        invoice.submit()
        entries = GLEntry.objects.filter(voucher_type="Supplier Invoice", voucher_no=invoice.invoice_number)
        self.assertTrue(entries.filter(account=override).exists())

    def test_stock_line_on_receipt_invoice_requires_receipt_link(self):
        receipt = PurchaseReceipt.objects.create(
            supplier=self.supplier,
            supplier_name="Mama Bisi Foods",
            posting_date=date.today(),
            warehouse=self.store,
        )
        PurchaseReceiptItem.objects.create(purchase_receipt=receipt, item=self.item, received_qty=2, rate=100)
        from apps.inventory.services import submit_purchase_receipt

        submit_purchase_receipt(receipt)
        invoice = self._make_invoice(purchase_receipt=receipt)
        with self.assertRaisesMessage(ValidationError, "must link to a receipt line"):
            SupplierInvoiceItem.objects.create(invoice=invoice, item=self.item, qty=2, rate=100)
        line = SupplierInvoiceItem(invoice=invoice, item=self.item, qty=2, rate=100)
        with self.assertRaisesMessage(ValidationError, "must link to a receipt line"):
            line.validate_for_submission()

    def test_expense_requires_description_and_positive_amount(self):
        invoice = self._make_invoice()
        with self.assertRaisesMessage(ValidationError, "Description is required"):
            SupplierInvoiceExpense.objects.create(invoice=invoice, description="   ", amount=100)
        with self.assertRaisesMessage(ValidationError, "greater than zero"):
            SupplierInvoiceExpense.objects.create(invoice=invoice, description="Cleaning", amount=0)

    def test_cancel_reverses_gl_and_clears_outstanding(self):
        invoice = self._make_receipt_invoice()
        invoice.submit()
        invoice.cancel()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SupplierInvoice.CANCELLED)
        self.assertEqual(invoice.outstanding_amount, Decimal("0"))
        originals = GLEntry.objects.filter(
            voucher_type="Supplier Invoice", voucher_no=invoice.invoice_number, is_cancelled=True
        )
        self.assertEqual(originals.count(), 2)
        reversals = GLEntry.objects.filter(
            voucher_type="Supplier Invoice", voucher_no=invoice.invoice_number, is_cancelled=False, remarks="Reversal"
        )
        self.assertEqual(reversals.count(), 2)
        self.assertEqual(reversals.get(account=self.accounts["grni"]).credit, Decimal("200"))
        self.assertEqual(reversals.get(account=self.accounts["payable"]).debit, Decimal("200"))

    def test_cancel_dates_reversal_on_cancellation_day(self):
        invoice = self._make_receipt_invoice(posting_date=date(2026, 1, 15))
        invoice.submit()
        invoice.cancel()
        reversal = GLEntry.objects.get(
            voucher_type="Supplier Invoice",
            voucher_no=invoice.invoice_number,
            is_cancelled=False,
            remarks="Reversal",
            account=self.accounts["grni"],
        )
        self.assertEqual(reversal.posting_date, timezone.localdate())
        self.assertNotEqual(reversal.posting_date, invoice.posting_date)


class SupplierPaymentTest(PayablesTestBase):
    def _paid_invoice(self, amount=200):
        invoice = self._make_receipt_invoice(rate=amount)
        invoice.submit()
        return invoice

    def _make_payment(self, invoice, amount):
        payment = SupplierPayment.objects.create(supplier=self.supplier, paid_amount=amount, mode_of_payment=self.cash)
        SupplierPaymentAllocation.objects.create(payment=payment, invoice=invoice, allocated_amount=amount)
        return payment

    def test_submit_posts_payable_and_cash_legs_and_reduces_outstanding(self):
        invoice = self._paid_invoice(amount=100)
        payment = self._make_payment(invoice, Decimal("200"))
        payment.submit()
        payment.refresh_from_db()
        invoice.refresh_from_db()

        self.assertEqual(payment.status, SupplierPayment.SUBMITTED)
        self.assertEqual(invoice.outstanding_amount, Decimal("0"))
        self.assertEqual(invoice.payment_status, "Paid")
        entries = GLEntry.objects.filter(voucher_type="Supplier Payment", voucher_no=payment.payment_number)
        self.assertEqual(entries.count(), 2)
        self.assertEqual(entries.get(account=self.accounts["payable"]).debit, Decimal("200"))
        self.assertEqual(entries.get(account=self.accounts["cash"]).credit, Decimal("200"))

    def test_partial_payment_leaves_partly_paid(self):
        invoice = self._paid_invoice(amount=200)
        payment = self._make_payment(invoice, Decimal("150"))
        payment.submit()
        invoice.refresh_from_db()
        self.assertEqual(invoice.outstanding_amount, Decimal("250"))
        self.assertEqual(invoice.payment_status, "Partly Paid")

    def test_payment_total_must_equal_allocations(self):
        invoice = self._paid_invoice(amount=200)
        payment = SupplierPayment.objects.create(
            supplier=self.supplier, paid_amount=Decimal("500"), mode_of_payment=self.cash
        )
        SupplierPaymentAllocation.objects.create(payment=payment, invoice=invoice, allocated_amount=Decimal("400"))
        with self.assertRaisesMessage(ValidationError, "must equal the paid amount"):
            payment.submit()
        self.assertEqual(payment.status, SupplierPayment.DRAFT)

    def test_allocation_cannot_exceed_outstanding(self):
        invoice = self._paid_invoice(amount=200)
        payment = SupplierPayment.objects.create(
            supplier=self.supplier, paid_amount=Decimal("500"), mode_of_payment=self.cash
        )
        with self.assertRaises(ValidationError):
            SupplierPaymentAllocation.objects.create(payment=payment, invoice=invoice, allocated_amount=Decimal("500"))

    def test_cancel_restores_outstanding_and_reverses_gl(self):
        invoice = self._paid_invoice(amount=100)
        payment = self._make_payment(invoice, Decimal("200"))
        payment.submit()
        payment.cancel()
        invoice.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(payment.status, SupplierPayment.CANCELLED)
        self.assertEqual(invoice.outstanding_amount, Decimal("200"))
        originals = GLEntry.objects.filter(
            voucher_type="Supplier Payment", voucher_no=payment.payment_number, is_cancelled=True
        )
        self.assertEqual(originals.count(), 2)
        reversals = GLEntry.objects.filter(
            voucher_type="Supplier Payment", voucher_no=payment.payment_number, is_cancelled=False, remarks="Reversal"
        )
        self.assertEqual(reversals.get(account=self.accounts["payable"]).credit, Decimal("200"))
        self.assertEqual(reversals.get(account=self.accounts["cash"]).debit, Decimal("200"))

    def test_supplier_outstanding_balance_reflects_invoices_minus_payments(self):
        invoice1 = self._paid_invoice(amount=300)
        self._paid_invoice(amount=100)
        self.assertEqual(self.supplier.outstanding_balance, Decimal("800"))
        payment = self._make_payment(invoice1, Decimal("200"))
        payment.submit()
        self.assertEqual(self.supplier.outstanding_balance, Decimal("600"))
