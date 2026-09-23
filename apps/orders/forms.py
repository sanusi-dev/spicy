"""POS-facing forms."""

from django import forms

from .models import CANCEL_REASON_CHOICES


class POSOrderCancelForm(forms.Form):
    """Validate the POS cancellation reason and optional note."""

    cancel_reason = forms.ChoiceField(
        choices=CANCEL_REASON_CHOICES,
        label="Reason",
        widget=forms.Select(
            attrs={
                "class": "w-full rounded-xl border border-gray-300 px-4 py-2.5 text-sm focus:border-orange-500 "
                "focus:outline-none focus:ring-2 focus:ring-orange-500/25 bg-white",
                "x-model": "cancelReason",
            }
        ),
    )
    cancel_reason_note = forms.CharField(
        label="Note",
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "Add a short note",
                "class": "w-full rounded-xl border border-gray-300 px-4 py-2.5 text-sm focus:border-orange-500 "
                "focus:outline-none focus:ring-2 focus:ring-orange-500/25 bg-white",
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("cancel_reason") == "other" and not (cleaned_data.get("cancel_reason_note") or "").strip():
            self.add_error("cancel_reason_note", "Add a note when choosing Other.")
        return cleaned_data
