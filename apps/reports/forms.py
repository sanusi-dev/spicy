"""Daily P&L and P&L settings forms."""

from django import forms
from django.forms import inlineformset_factory

from apps.utils.forms import StyledModelForm, active_choices

from .models import DailyPnL, DailyPnLAdHoc, DailyPnLMaterialQty, PnLConfiguration, PnLMaterial, PnLRecurringExpense


class ReportsModelForm(StyledModelForm):
    pass


class PnLConfigurationForm(ReportsModelForm):
    class Meta:
        model = PnLConfiguration
        fields = ["business_day_start_hour", "electricity_rate", "daily_depreciation", "include_cash_variance"]


class PnLMaterialForm(ReportsModelForm):
    class Meta:
        model = PnLMaterial
        fields = ["name", "unit", "rate", "disabled"]


class PnLRecurringExpenseForm(ReportsModelForm):
    class Meta:
        model = PnLRecurringExpense
        fields = ["name", "kind", "amount", "percent", "department", "disabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].required = False
        self.fields["percent"].required = False
        self.fields["amount"].required = False


class DailyPnLForm(ReportsModelForm):
    class Meta:
        model = DailyPnL
        fields = [
            "business_date",
            "electricity_opening",
            "electricity_closing",
            "employee_cost_override",
            "remarks",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["electricity_opening"].required = False
        self.fields["electricity_closing"].required = False
        self.fields["employee_cost_override"].required = False
        self.fields["remarks"].required = False
        if self.instance.pk:
            self.fields["business_date"].disabled = True


class DailyPnLMaterialQtyForm(ReportsModelForm):
    class Meta:
        model = DailyPnLMaterialQty
        fields = ["material", "qty"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = active_choices(PnLMaterial, self.instance.material_id, disabled=False)
        self.fields["qty"].required = False

    def clean_qty(self):
        qty = self.cleaned_data.get("qty")
        return qty if qty is not None else 0


class DailyPnLAdHocForm(ReportsModelForm):
    class Meta:
        model = DailyPnLAdHoc
        fields = ["label", "amount", "section", "department"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].required = False


PnLMaterialFormSet = forms.modelformset_factory(PnLMaterial, form=PnLMaterialForm, extra=1, can_delete=True)

PnLRecurringExpenseFormSet = forms.modelformset_factory(
    PnLRecurringExpense, form=PnLRecurringExpenseForm, extra=1, can_delete=True
)

DailyPnLMaterialQtyFormSet = inlineformset_factory(
    DailyPnL, DailyPnLMaterialQty, form=DailyPnLMaterialQtyForm, extra=1, can_delete=True
)

DailyPnLAdHocFormSet = inlineformset_factory(DailyPnL, DailyPnLAdHoc, form=DailyPnLAdHocForm, extra=1, can_delete=True)
