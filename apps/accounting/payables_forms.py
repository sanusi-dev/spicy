"""Supplier payables forms — supplier, invoice (with expense formset), payment (with allocations)."""

from django import forms

from apps.inventory.models import PurchaseReceipt
from apps.payments.models import ModeOfPayment
from apps.utils.forms import StyledModelForm, active_choices

from .models import LedgerAccount
from .payables_models import (
    Supplier,
    SupplierInvoice,
    SupplierInvoiceExpense,
    SupplierPayment,
    SupplierPaymentAllocation,
)


class PayablesModelForm(StyledModelForm):
    pass


class SupplierForm(PayablesModelForm):
    class Meta:
        model = Supplier
        fields = [
            "supplier_name",
            "supplier_type",
            "contact_person",
            "phone",
            "email",
            "address",
            "tax_id",
            "payable_account",
            "is_default",
            "disabled",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payable_account"].queryset = active_choices(
            LedgerAccount, self.instance.payable_account_id, disabled=False, is_group=False
        )


class SupplierInvoiceForm(PayablesModelForm):
    class Meta:
        model = SupplierInvoice
        fields = ["supplier", "posting_date", "due_date", "bill_no", "bill_date", "purchase_receipt", "remarks"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = active_choices(Supplier, self.instance.supplier_id, disabled=False)
        self.fields["purchase_receipt"].queryset = active_choices(
            PurchaseReceipt, self.instance.purchase_receipt_id, status="SUBMITTED"
        )
        self.fields["purchase_receipt"].required = False
        self.fields["due_date"].required = False
        self.fields["bill_no"].required = False
        self.fields["bill_date"].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("purchase_receipt"):
            receipt = cleaned["purchase_receipt"]
            supplier = cleaned.get("supplier")
            if supplier and receipt.supplier_id and receipt.supplier_id != supplier.pk:
                raise forms.ValidationError("The receipt's supplier must match the invoice supplier.")
        return cleaned


class SupplierInvoiceExpenseForm(PayablesModelForm):
    class Meta:
        model = SupplierInvoiceExpense
        fields = ["description", "amount"]


class SupplierPaymentForm(PayablesModelForm):
    class Meta:
        model = SupplierPayment
        fields = [
            "supplier",
            "posting_date",
            "mode_of_payment",
            "paid_amount",
            "reference_no",
            "reference_date",
            "remarks",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = active_choices(Supplier, self.instance.supplier_id, disabled=False)
        self.fields["mode_of_payment"].queryset = active_choices(
            ModeOfPayment, self.instance.mode_of_payment_id, enabled=True
        )
        self.fields["reference_no"].required = False
        self.fields["reference_date"].required = False


class SupplierPaymentAllocationForm(PayablesModelForm):
    class Meta:
        model = SupplierPaymentAllocation
        fields = ["invoice", "allocated_amount"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        invoices = SupplierInvoice.objects.filter(
            status=SupplierInvoice.SUBMITTED, outstanding_amount__gt=0
        ).select_related("supplier")
        payment = getattr(self.instance, "payment", None)
        if payment is not None and payment.supplier_id:
            invoices = invoices.filter(supplier_id=payment.supplier_id)
        self.fields["invoice"].queryset = invoices
        self.fields["invoice"].label = "Invoice"
        self.fields["invoice"].help_text = "Only submitted invoices with an outstanding balance are listed."
        self.fields["allocated_amount"].help_text = "Must not exceed the invoice's outstanding amount."

    def clean_allocated_amount(self):
        value = self.cleaned_data.get("allocated_amount")
        if value is not None and value <= 0:
            raise forms.ValidationError("Allocated amount must be greater than zero.")
        return value

    def clean(self):
        cleaned = super().clean()
        invoice = cleaned.get("invoice")
        allocated = cleaned.get("allocated_amount")
        if invoice and allocated is not None and allocated > invoice.outstanding_amount:
            raise forms.ValidationError("Allocated amount cannot exceed the invoice's outstanding amount.")
        return cleaned


SupplierInvoiceExpenseFormSet = forms.inlineformset_factory(
    SupplierInvoice,
    SupplierInvoiceExpense,
    form=SupplierInvoiceExpenseForm,
    extra=1,
    can_delete=True,
)
SupplierPaymentAllocationFormSet = forms.inlineformset_factory(
    SupplierPayment,
    SupplierPaymentAllocation,
    form=SupplierPaymentAllocationForm,
    extra=1,
    can_delete=True,
)
