"""Accounting backoffice forms — chart, journal entries, fiscal years."""

from decimal import Decimal

from django import forms

from apps.utils.forms import StyledModelForm, active_choices

from .models import FiscalYear, JournalEntry, JournalEntryAccount, LedgerAccount


class AccountingModelForm(StyledModelForm):
    pass


class LedgerAccountForm(AccountingModelForm):
    class Meta:
        model = LedgerAccount
        fields = [
            "name",
            "parent",
            "is_group",
            "account_type",
            "report_type",
            "account_number",
            "freeze_account",
            "disabled",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = active_choices(LedgerAccount, self.instance.parent_id, is_group=True)
        self.fields["parent"].label = "Parent group"
        self.fields["account_type"].required = False
        self.fields["report_type"].required = False


class FiscalYearForm(AccountingModelForm):
    class Meta:
        model = FiscalYear
        fields = ["name", "year_start_date", "year_end_date", "is_short_year", "disabled"]


class JournalEntryForm(AccountingModelForm):
    class Meta:
        model = JournalEntry
        fields = [
            "voucher_type",
            "posting_date",
            "reference_no",
            "reference_date",
            "remark",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reference_no"].required = False
        self.fields["reference_date"].required = False
        if self.instance.pk and self.instance.voucher_type == JournalEntry.OPENING:
            self.fields["voucher_type"].disabled = True


class JournalEntryAccountForm(AccountingModelForm):
    class Meta:
        model = JournalEntryAccount
        fields = ["account", "debit", "credit", "remarks"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = active_choices(
            LedgerAccount, self.instance.account_id, disabled=False, is_group=False
        )
        self.fields["debit"].required = False
        self.fields["credit"].required = False
        self.fields["remarks"].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("debit") is None:
            cleaned["debit"] = Decimal("0")
        if cleaned.get("credit") is None:
            cleaned["credit"] = Decimal("0")
        return cleaned


JournalEntryAccountFormSet = forms.inlineformset_factory(
    JournalEntry,
    JournalEntryAccount,
    form=JournalEntryAccountForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
