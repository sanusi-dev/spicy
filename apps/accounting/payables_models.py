"""Supplier payables models — supplier master, invoices, payments, and allocations."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.utils.models import BaseModel
from apps.utils.rounding import money


class Supplier(BaseModel):
    """A supplier of goods or services the restaurant buys from."""

    COMPANY = "COMPANY"
    INDIVIDUAL = "INDIVIDUAL"
    SUPPLIER_TYPE_CHOICES = [
        (COMPANY, "Company"),
        (INDIVIDUAL, "Individual"),
    ]

    supplier_name = models.CharField(max_length=200, unique=True)
    supplier_type = models.CharField(max_length=20, choices=SUPPLIER_TYPE_CHOICES, default=COMPANY)
    contact_person = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=100, blank=True)
    email = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    tax_id = models.CharField(max_length=50, blank=True)
    payable_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Payable account",
        help_text="The account used to track what you owe this supplier. Leave blank to use the default payable account.",
    )
    is_default = models.BooleanField(default=False)
    disabled = models.BooleanField(default=False)

    class Meta:
        ordering = ["supplier_name"]

    def __str__(self):
        return self.supplier_name

    def clean(self):
        super().clean()
        self.supplier_name = self.supplier_name.strip()
        if not self.supplier_name:
            raise ValidationError({"supplier_name": "Supplier name is required."})
        if self.payable_account_id:
            account = self.payable_account
            if not account.is_leaf:
                raise ValidationError({"payable_account": "The payable account must be a leaf account."})
            if account.disabled:
                raise ValidationError({"payable_account": "The payable account must be enabled."})
            if account.freeze_account:
                raise ValidationError({"payable_account": "The payable account is frozen."})

    def save(self, *args, **kwargs):
        if self.is_default:
            Supplier.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def outstanding_balance(self):
        """Total outstanding across this supplier's submitted, non-cancelled invoices."""
        total = Decimal("0")
        for invoice in self.invoices.filter(status=SupplierInvoice.SUBMITTED):
            total += invoice.outstanding_amount
        return total


class SupplierInvoice(BaseModel):
    """A supplier's bill — the source document for accounts payable."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (CANCELLED, "Cancelled"),
    ]

    invoice_number = models.CharField(max_length=50, unique=True, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="invoices")
    posting_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(default=timezone.localdate)
    bill_no = models.CharField(max_length=100, blank=True)
    bill_date = models.DateField(null=True, blank=True)
    purchase_receipt = models.ForeignKey(
        "inventory.PurchaseReceipt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supplier_invoices",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    outstanding_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-posting_date", "-pk"]

    def __str__(self):
        return f"{self.invoice_number} — {self.supplier.supplier_name}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.only("status", "purchase_receipt_id", "invoice_number").get(pk=self.pk)
            if previous.status != self.DRAFT:
                allow_status = getattr(self, "_allow_status", False)
                if not allow_status:
                    raise ValidationError(f"Cannot modify a {previous.status.lower()} supplier invoice.")
                editable = (
                    "supplier",
                    "posting_date",
                    "due_date",
                    "bill_no",
                    "bill_date",
                    "purchase_receipt",
                    "remarks",
                )
                if any(getattr(self, field) != getattr(previous, field) for field in editable):
                    raise ValidationError(f"Cannot modify a {previous.status.lower()} supplier invoice.")
            elif previous.purchase_receipt_id and self.purchase_receipt_id != previous.purchase_receipt_id:
                raise ValidationError("The linked purchase receipt cannot be changed once set.")
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new:
            from apps.settings.models import Restaurant

            prefix = Restaurant.load().invoice_series_prefix if Restaurant.load() else "REST-"
            self.invoice_number = f"{prefix}PINV-{self.pk}"
            super().save(update_fields=["invoice_number"])

    def delete(self, *args, **kwargs):
        if self.pk and self.status != self.DRAFT:
            raise ValidationError("Submitted or cancelled supplier invoices cannot be deleted.")
        super().delete(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.due_date and self.posting_date and self.due_date < self.posting_date:
            raise ValidationError({"due_date": "The due date cannot be before the posting date."})
        if self.bill_date and self.posting_date and self.bill_date > self.posting_date:
            raise ValidationError({"bill_date": "The bill date cannot be after the posting date."})
        if self.purchase_receipt_id:
            receipt = self.purchase_receipt
            if receipt.status != "SUBMITTED":
                raise ValidationError({"purchase_receipt": "The linked purchase receipt must be submitted."})
            if receipt.supplier_id and self.supplier_id != receipt.supplier_id:
                raise ValidationError({"purchase_receipt": "The receipt's supplier must match the invoice supplier."})

    @transaction.atomic
    def submit(self):
        """Build stock lines from the receipt, post GL, set outstanding, and mark submitted."""
        from .services import build_supplier_invoice_stock_lines, post_supplier_invoice_gl

        locked = type(self).objects.select_for_update().get(pk=self.pk)
        if locked.status != self.DRAFT:
            self.status = locked.status
            return
        build_supplier_invoice_stock_lines(locked)
        lines = list(locked.items.select_related("item", "source_receipt_line"))
        expenses = list(locked.expenses.all())
        if not lines and not expenses:
            raise ValidationError("Add at least one line before submitting.")
        total = Decimal("0")
        for line in lines:
            line.validate_for_submission()
            total += line.amount
        for expense in expenses:
            expense.full_clean()
            total += expense.amount
        locked.total = total
        locked.outstanding_amount = total
        locked.save(update_fields=["total", "outstanding_amount", "updated_at"])
        post_supplier_invoice_gl(locked)
        locked.status = self.SUBMITTED
        locked._allow_status = True
        try:
            locked.save(update_fields=["status", "updated_at"])
        finally:
            del locked._allow_status
        self.total = locked.total
        self.outstanding_amount = locked.outstanding_amount
        self.status = locked.status

    @transaction.atomic
    def cancel(self):
        """Reverse GL rows and allocations, restore outstanding, and mark cancelled."""
        from .services import cancel_supplier_invoice_gl

        locked = type(self).objects.select_for_update().get(pk=self.pk)
        if locked.status == self.CANCELLED:
            self.status = locked.status
            return
        if locked.status != self.SUBMITTED:
            raise ValidationError("Only submitted supplier invoices can be cancelled.")
        if locked.allocations.filter(payment__status=SupplierPayment.SUBMITTED).exists():
            raise ValidationError("Cancel the supplier payment(s) allocated to this invoice first.")
        cancel_supplier_invoice_gl(locked)
        locked.outstanding_amount = Decimal("0")
        locked.status = self.CANCELLED
        locked._allow_status = True
        try:
            locked.save(update_fields=["status", "outstanding_amount", "updated_at"])
        finally:
            del locked._allow_status
        self.outstanding_amount = locked.outstanding_amount
        self.status = locked.status

    @property
    def payment_status(self):
        """Unpaid / Partly Paid / Paid derived from outstanding vs total."""
        if self.status != self.SUBMITTED:
            return "Unpaid"
        if self.outstanding_amount == 0:
            return "Paid"
        if self.outstanding_amount < self.total:
            return "Partly Paid"
        return "Unpaid"


class SupplierInvoiceItem(BaseModel):
    """A stock line of a supplier invoice — created from the linked purchase receipt."""

    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(
        "inventory.Item",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="supplier_invoice_items",
    )
    source_receipt_line = models.ForeignKey(
        "inventory.PurchaseReceiptItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supplier_invoice_lines",
    )
    description = models.CharField(max_length=200, blank=True)
    qty = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1"))
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    received_qty = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"), editable=False)
    amount_per_unit = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"), editable=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_receipt_line"],
                condition=models.Q(source_receipt_line__isnull=False),
                name="accounting_invoice_item_unique_receipt_line",
            ),
        ]

    def __str__(self):
        return f"{self.description or self.item} x{self.qty}"

    def clean(self):
        super().clean()
        if not self.item_id:
            raise ValidationError("Choose an item for the line.")
        item = self.item
        if item.disabled or item.has_variants or not item.is_stock_item or not item.is_purchase_item:
            raise ValidationError(f"{item.item_name} is not an enabled stock and purchase item.")
        if self.qty <= 0:
            raise ValidationError("Item quantity must be greater than zero.")
        if self.rate < 0:
            raise ValidationError("Item rate cannot be negative.")
        if not self.source_receipt_line_id and self.invoice_id and self.invoice.purchase_receipt_id:
            # Qty/rate come from the receipt so GRNI clears at the exact credited rate.
            raise ValidationError(
                {"source_receipt_line": "Stock lines on a receipt-linked invoice must link to a receipt line."}
            )
        if self.source_receipt_line_id:
            line = self.source_receipt_line
            if line.purchase_receipt.status != "SUBMITTED":
                raise ValidationError({"source_receipt_line": "The source receipt must be submitted."})
            if line.purchase_receipt.supplier_id and line.purchase_receipt.supplier_id != self.invoice.supplier_id:
                raise ValidationError(
                    {"source_receipt_line": "The receipt's supplier must match the invoice supplier."}
                )
            clash = type(self).objects.filter(source_receipt_line_id=line.pk).exclude(pk=self.pk).exists()
            if clash:
                raise ValidationError({"source_receipt_line": "This receipt line is already on an invoice."})

    def save(self, *args, **kwargs):
        if self.invoice_id:
            invoice = self.invoice
            if invoice.pk and invoice.status != SupplierInvoice.DRAFT:
                raise ValidationError("Only draft supplier invoices can have lines added or edited.")
        if self.source_receipt_line_id:
            line = self.source_receipt_line
            self.item = line.item
            self.qty = line.received_qty
            self.rate = line.rate
            self.received_qty = line.received_qty
            self.amount_per_unit = line.rate
            self.description = self.description or line.item.item_name
        if self.rate is None:
            self.rate = Decimal("0")
        self.amount = Decimal(self.qty or Decimal("0")) * Decimal(self.rate)
        self.amount = money(self.amount)
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice_id:
            invoice = self.invoice
            if invoice.pk and invoice.status != SupplierInvoice.DRAFT:
                raise ValidationError("Only draft supplier invoices can have lines removed.")
        super().delete(*args, **kwargs)

    def validate_for_submission(self):
        if not self.item_id:
            raise ValidationError("Choose an item for the line.")
        if (
            self.item.disabled
            or self.item.has_variants
            or not self.item.is_stock_item
            or not self.item.is_purchase_item
        ):
            raise ValidationError(f"{self.item.item_name} is not an enabled stock and purchase item.")
        if self.qty <= 0:
            raise ValidationError("Item quantity must be greater than zero.")
        if self.rate < 0:
            raise ValidationError("Item rate cannot be negative.")
        if not self.source_receipt_line_id and self.invoice_id and self.invoice.purchase_receipt_id:
            raise ValidationError(
                {"source_receipt_line": "Stock lines on a receipt-linked invoice must link to a receipt line."}
            )
        if self.source_receipt_line_id:
            line = self.source_receipt_line
            if line.purchase_receipt.status != "SUBMITTED":
                raise ValidationError({"source_receipt_line": "The source receipt must be submitted."})
            clash = type(self).objects.filter(source_receipt_line_id=line.pk).exclude(pk=self.pk).exists()
            if clash:
                raise ValidationError({"source_receipt_line": "This receipt line is already on an invoice."})


class SupplierInvoiceExpense(BaseModel):
    """A supplier invoice expense line — a non-stock cost with a description and amount."""

    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE, related_name="expenses")
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.description} {self.amount}"

    def clean(self):
        super().clean()
        if not self.description.strip():
            raise ValidationError({"description": "Description is required."})
        if self.amount <= 0:
            raise ValidationError({"amount": "Expense amount must be greater than zero."})

    def save(self, *args, **kwargs):
        if self.invoice_id:
            invoice = self.invoice
            if invoice.pk and invoice.status != SupplierInvoice.DRAFT:
                raise ValidationError("Only draft supplier invoices can have expenses added or edited.")
        self.amount = money(self.amount)
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice_id:
            invoice = self.invoice
            if invoice.pk and invoice.status != SupplierInvoice.DRAFT:
                raise ValidationError("Only draft supplier invoices can have expenses removed.")
        super().delete(*args, **kwargs)


class SupplierPayment(BaseModel):
    """A payment to a supplier, fully allocated to outstanding invoices."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (CANCELLED, "Cancelled"),
    ]

    payment_number = models.CharField(max_length=50, unique=True, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="payments")
    posting_date = models.DateField(default=timezone.localdate)
    mode_of_payment = models.ForeignKey(
        "payments.ModeOfPayment",
        on_delete=models.PROTECT,
        related_name="supplier_payments",
    )
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference_no = models.CharField(max_length=50, blank=True)
    reference_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-posting_date", "-pk"]

    def __str__(self):
        return f"{self.payment_number} — {self.supplier.supplier_name}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.only("status").get(pk=self.pk)
            if previous.status != self.DRAFT:
                allow_status = getattr(self, "_allow_status", False)
                if not allow_status:
                    raise ValidationError(f"Cannot modify a {previous.status.lower()} supplier payment.")
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new:
            from apps.settings.models import Restaurant

            prefix = Restaurant.load().invoice_series_prefix if Restaurant.load() else "REST-"
            self.payment_number = f"{prefix}PAY-{self.pk}"
            super().save(update_fields=["payment_number"])

    def delete(self, *args, **kwargs):
        if self.pk and self.status != self.DRAFT:
            raise ValidationError("Submitted or cancelled supplier payments cannot be deleted.")
        super().delete(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.paid_amount is not None and self.paid_amount <= 0:
            raise ValidationError({"paid_amount": "Paid amount must be greater than zero."})
        if self.mode_of_payment_id and not self.mode_of_payment.enabled:
            raise ValidationError({"mode_of_payment": "The payment mode must be enabled."})
        if self.mode_of_payment_id and self.mode_of_payment.type == "BANK" and not self.reference_no:
            raise ValidationError({"reference_no": "A bank payment requires a reference number."})
        if self.reference_date and self.posting_date and self.reference_date > self.posting_date:
            raise ValidationError({"reference_date": "The reference date cannot be after the posting date."})

    @transaction.atomic
    def submit(self):
        """Post GL (Dr payable, Cr cash/bank), apply allocations, and mark submitted."""
        from .services import post_supplier_payment_gl

        locked = type(self).objects.select_for_update().get(pk=self.pk)
        if locked.status != self.DRAFT:
            self.status = locked.status
            return
        allocations = list(locked.allocations.select_related("invoice"))
        if not allocations:
            raise ValidationError("Allocate the payment to at least one invoice before submitting.")
        total_allocated = Decimal("0")
        for alloc in allocations:
            alloc.validate_for_submission()
            total_allocated += alloc.allocated_amount
        if total_allocated != locked.paid_amount:
            raise ValidationError("The total allocated amount must equal the paid amount.")
        invoice_ids = sorted({alloc.invoice_id for alloc in allocations})
        invoices = {
            invoice.pk: invoice
            for invoice in SupplierInvoice.objects.select_for_update().filter(pk__in=invoice_ids).order_by("pk")
        }
        for alloc in allocations:
            invoice = invoices[alloc.invoice_id]
            if invoice.supplier_id != locked.supplier_id:
                raise ValidationError("Allocated invoices must belong to the payment's supplier.")
            if invoice.status != SupplierInvoice.SUBMITTED:
                raise ValidationError("Only submitted supplier invoices can be paid.")
            if alloc.allocated_amount > invoice.outstanding_amount:
                raise ValidationError(
                    f"Allocated amount cannot exceed the outstanding amount of {invoice.invoice_number}."
                )
            invoice.outstanding_amount -= alloc.allocated_amount
            invoice._allow_status = True
            try:
                invoice.save(update_fields=["outstanding_amount", "updated_at"])
            finally:
                del invoice._allow_status
        post_supplier_payment_gl(locked)
        locked.status = self.SUBMITTED
        locked._allow_status = True
        try:
            locked.save(update_fields=["status", "updated_at"])
        finally:
            del locked._allow_status
        self.status = locked.status

    @transaction.atomic
    def cancel(self):
        """Reverse GL rows and allocations, restore outstanding, and mark cancelled."""
        from .services import cancel_supplier_payment_gl

        locked = type(self).objects.select_for_update().get(pk=self.pk)
        if locked.status == self.CANCELLED:
            self.status = locked.status
            return
        if locked.status != self.SUBMITTED:
            raise ValidationError("Only submitted supplier payments can be cancelled.")
        allocations = list(locked.allocations.select_related("invoice"))
        for alloc in allocations:
            invoice = alloc.invoice
            invoice.outstanding_amount += alloc.allocated_amount
            invoice._allow_status = True
            try:
                invoice.save(update_fields=["outstanding_amount", "updated_at"])
            finally:
                del invoice._allow_status
        cancel_supplier_payment_gl(locked)
        locked.status = self.CANCELLED
        locked._allow_status = True
        try:
            locked.save(update_fields=["status", "updated_at"])
        finally:
            del locked._allow_status
        self.status = locked.status


class SupplierPaymentAllocation(BaseModel):
    """A payment's allocation to one supplier invoice — the outstanding record."""

    payment = models.ForeignKey(SupplierPayment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(
        SupplierInvoice,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    outstanding_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    allocated_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.payment.payment_number} → {self.invoice.invoice_number} {self.allocated_amount}"

    def save(self, *args, **kwargs):
        if self.payment_id:
            payment = self.payment
            if payment.pk and payment.status != SupplierPayment.DRAFT:
                raise ValidationError("Only draft supplier payments can have allocations added or edited.")
        if self.invoice_id and self.pk is None:
            self.outstanding_amount = self.invoice.outstanding_amount
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.payment_id:
            payment = self.payment
            if payment.pk and payment.status != SupplierPayment.DRAFT:
                raise ValidationError("Only draft supplier payments can have allocations removed.")
        super().delete(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.invoice_id and self.invoice.status != SupplierInvoice.SUBMITTED:
            raise ValidationError({"invoice": "Only submitted supplier invoices can be allocated."})
        if self.allocated_amount is not None and self.allocated_amount <= 0:
            raise ValidationError({"allocated_amount": "Allocated amount must be greater than zero."})
        exceeds_outstanding = (
            self.invoice_id
            and self.allocated_amount is not None
            and self.allocated_amount > self.invoice.outstanding_amount
        )
        if exceeds_outstanding:
            raise ValidationError(
                {"allocated_amount": "Allocated amount cannot exceed the invoice's outstanding amount."}
            )
        if self.payment_id and self.invoice_id and self.invoice.supplier_id != self.payment.supplier_id:
            raise ValidationError({"invoice": "Allocated invoices must belong to the payment's supplier."})

    def validate_for_submission(self):
        if self.invoice.status != SupplierInvoice.SUBMITTED:
            raise ValidationError("Only submitted supplier invoices can be paid.")
        if self.allocated_amount is None or self.allocated_amount <= 0:
            raise ValidationError("Allocated amount must be greater than zero.")
        if self.allocated_amount > self.invoice.outstanding_amount:
            raise ValidationError(
                f"Allocated amount cannot exceed the outstanding amount of {self.invoice.invoice_number}."
            )
        if self.invoice.supplier_id != self.payment.supplier_id:
            raise ValidationError("Allocated invoices must belong to the payment's supplier.")
