"""Forms for payment modes and GL mappings."""

from apps.accounting.models import LedgerAccount
from apps.utils.forms import StyledModelForm, active_choices

from .models import ModeOfPayment, PaymentGLMapping


class PaymentsModelForm(StyledModelForm):
    pass


class ModeOfPaymentForm(PaymentsModelForm):
    class Meta:
        model = ModeOfPayment
        fields = ["name", "type", "enabled", "is_default"]


class PaymentGLMappingForm(PaymentsModelForm):
    class Meta:
        model = PaymentGLMapping
        fields = ["mode_of_payment", "default_account"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["mode_of_payment"].queryset = active_choices(
            ModeOfPayment, self.instance.mode_of_payment_id, enabled=True
        )
        self.fields["default_account"].queryset = active_choices(
            LedgerAccount, self.instance.default_account_id, disabled=False, is_group=False
        )
