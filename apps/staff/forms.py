"""Opening-float and closing-float forms for POS shift management."""

from decimal import Decimal

from django import forms

from apps.payments.models import ModeOfPayment
from apps.utils.forms import TAILWIND_INPUT_CLASS, StyledModelForm

from .models import ClosingPayment


class StaffModelForm(StyledModelForm):
    pass


class ClosingPaymentForm(StaffModelForm):
    class Meta:
        model = ClosingPayment
        fields = ["closing_amount"]
        widgets = {
            "closing_amount": forms.NumberInput(
                attrs={
                    "data-counted": "",
                    "inputmode": "decimal",
                    "min": "0",
                    "step": "0.01",
                }
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["closing_amount"]
        field.min_value = Decimal("0")
        field.error_messages["min_value"] = "Counted amounts can't be negative."


class OpeningFloatForm(forms.Form):
    """Opening-shift form with one amount field per active payment mode, defaulting to 0."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        all_modes = list(ModeOfPayment.objects.filter(enabled=True).order_by("name"))
        # Cash modes first — the primary float renders at the top of the form.
        all_modes.sort(key=lambda m: (0 if m.type == ModeOfPayment.TYPE_CASH else 1, m.name))
        self.modes = all_modes
        for mode in self.modes:
            field_name = self._field_name_for(mode)
            is_cash = mode.type == ModeOfPayment.TYPE_CASH
            self.fields[field_name] = forms.DecimalField(
                label=f"{mode.name} Float" if is_cash else f"{mode.name} Opening Balance",
                min_value=Decimal("0"),
                max_digits=12,
                decimal_places=2,
                initial=Decimal("0"),
                required=False,
                widget=forms.NumberInput(
                    attrs={
                        "class": TAILWIND_INPUT_CLASS,
                        "inputmode": "decimal",
                        "step": "0.01",
                        "placeholder": "0.00",
                    }
                ),
            )

    @staticmethod
    def _field_name_for(mode: ModeOfPayment) -> str:
        return f"mop_{mode.pk}"

    def opening_amounts(self):
        """Return a dict mapping each ModeOfPayment to its entered Decimal amount."""
        amounts = {}
        for mode in self.modes:
            raw = self.cleaned_data.get(self._field_name_for(mode))
            if raw is None:
                raw = Decimal("0")
            amounts[mode] = Decimal(raw)
        return amounts
