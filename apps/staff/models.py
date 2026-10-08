"""Shift models — opening entries, closing entries, payments, and cash-outs."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from apps.payments.models import ModeOfPayment
from apps.users.models import CustomUser
from apps.utils.models import BaseModel


class POSOpeningEntry(BaseModel):
    """Start-of-shift document. One OPEN shift at a time."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (CANCELLED, "Cancelled"),
    ]

    period_start_date = models.DateTimeField(default=timezone.now, editable=False)
    period_end_date = models.DateTimeField(null=True, blank=True, editable=False)
    posting_date = models.DateField(default=timezone.localdate)
    cashier = models.ForeignKey(
        CustomUser,
        on_delete=models.PROTECT,
        related_name="pos_opening_entries",
    )
    closing_entry = models.OneToOneField(
        "staff.POSClosingEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opening_entry_ref",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    remarks = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_opening_entries",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-period_start_date"]
        indexes = [models.Index(fields=["status", "period_start_date"])]

    def __str__(self):
        return f"Opening #{self.pk} — {self.posting_date}"

    def clean(self):
        super().clean()
        # Must also fire on DRAFT: the view calls full_clean() before submit() flips the status.
        is_open_or_will_open = self.status == self.SUBMITTED and self.closing_entry_id is None
        is_being_submitted = self.status == self.DRAFT
        if is_open_or_will_open or is_being_submitted:
            qs = POSOpeningEntry.objects.filter(
                status=self.SUBMITTED,
                closing_entry__isnull=True,
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError(
                    "An open shift already exists. Close the existing shift before opening a new one."
                )

    @property
    def is_open(self):
        return self.status == self.SUBMITTED and self.closing_entry_id is None

    @property
    def is_closed(self):
        return self.status == self.SUBMITTED and self.closing_entry_id is not None

    def can_be_closed_by(self, user) -> bool:
        """Return True if the user may close this shift: its opener or a manager/admin."""
        if user.is_manager or user.is_admin:
            return True
        return self.cashier_id == user.pk

    def submit(self):
        """Transition from DRAFT to SUBMITTED; re-checks "one Open shift" under row locks to close the submit race."""
        if self.status != self.DRAFT:
            return
        with transaction.atomic():
            from apps.settings.models import Restaurant

            # Restaurant row lock is the global mutex: two concurrent opens can't both pass the check.
            settings = Restaurant.objects.select_for_update().first()
            if settings is None:
                raise ValidationError("Restaurant settings are not configured.")
            open_exists = (
                POSOpeningEntry.objects.select_for_update()
                .filter(
                    status=self.SUBMITTED,
                    closing_entry__isnull=True,
                )
                .exclude(pk=self.pk)
                .exists()
            )
            if open_exists:
                raise ValidationError(
                    "An open shift already exists. Close the existing shift before opening a new one."
                )
            cash_rows = [
                op
                for op in self.opening_payments.select_related("mode_of_payment")
                if op.mode_of_payment.type == ModeOfPayment.TYPE_CASH
            ]
            if len(cash_rows) > 1:
                names = ", ".join(op.mode_of_payment.name for op in cash_rows)
                raise ValidationError(
                    f"A shift can declare at most one cash payment mode (declared: {names}). "
                    "Change and the close variance need a single drawer."
                )
            self.status = self.SUBMITTED
            self.save(update_fields=["status", "updated_at"])

    def cancel(self, by_user=None):
        """Cancel a draft or open shift. Closed shifts cannot be cancelled."""
        if self.status == self.CANCELLED:
            return
        if self.closing_entry_id is not None:
            raise ValidationError(
                "Cannot cancel a shift that has already been closed. Cancel the closing entry instead."
            )
        from apps.orders.models import Order

        order_count = Order.objects.filter(opening_entry_id=self.pk).count()
        if order_count:
            raise ValidationError(
                f"Cannot cancel a shift that has {order_count} order{'s' if order_count != 1 else ''}. "
                "Settle, cancel, or discard every order first."
            )
        self.status = self.CANCELLED
        self.cancelled_at = timezone.now()
        if by_user is not None:
            self.cancelled_by = by_user
        self.save(update_fields=["status", "cancelled_at", "cancelled_by", "updated_at"])


class OpeningPayment(BaseModel):
    """Child row: opening float per payment method at shift-open time."""

    opening_entry = models.ForeignKey(
        POSOpeningEntry,
        on_delete=models.CASCADE,
        related_name="opening_payments",
    )
    mode_of_payment = models.ForeignKey(
        ModeOfPayment,
        on_delete=models.PROTECT,
        related_name="opening_payments",
    )
    opening_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    class Meta:
        unique_together = [("opening_entry", "mode_of_payment")]
        ordering = ["mode_of_payment__name"]

    def __str__(self):
        return f"{self.mode_of_payment.name}: {self.opening_amount}"

    def save(self, *args, **kwargs):
        if self.opening_entry_id:
            status = POSOpeningEntry.objects.only("status").get(pk=self.opening_entry_id).status
            if status != POSOpeningEntry.DRAFT:
                raise ValidationError("Opening rows cannot change after the shift opens.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.opening_entry_id:
            status = POSOpeningEntry.objects.only("status").get(pk=self.opening_entry_id).status
            if status != POSOpeningEntry.DRAFT:
                raise ValidationError("Opening rows cannot be deleted after the shift opens.")
        return super().delete(*args, **kwargs)


class POSClosingEntry(BaseModel):
    """End-of-shift reconciliation document. Links to a POSOpeningEntry."""

    # Service-only transition flags — set and cleared around one status transition.
    _allow_submit = False
    _allow_cancel = False

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (CANCELLED, "Cancelled"),
    ]

    opening_entry = models.OneToOneField(
        POSOpeningEntry,
        on_delete=models.PROTECT,
        related_name="closing_entry_for",
    )
    period_start_date = models.DateTimeField(editable=False)
    period_end_date = models.DateTimeField(default=timezone.now)
    posting_date = models.DateField(default=timezone.localdate)
    cashier = models.ForeignKey(
        CustomUser,
        on_delete=models.PROTECT,
        related_name="pos_closing_entries",
    )
    total_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), editable=False)
    net_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    bill_count = models.PositiveIntegerField(default=0, editable=False)
    refunded_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    total_short_excess = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    variance_note = models.TextField(
        blank=True,
        help_text="Required when the cash difference is large enough to need manager approval.",
    )
    variance_journal_entry = models.OneToOneField(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cash_variance_closing",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    remarks = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_closing_entries",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-period_end_date"]

    def __str__(self):
        return f"Closing #{self.pk} — {self.posting_date}"

    def save(self, *args, **kwargs):
        allow_cancel = getattr(self, "_allow_cancel", False)
        allow_submit = getattr(self, "_allow_submit", False)
        if self.pk:
            previous = type(self).objects.only("status").get(pk=self.pk)
            if previous.status != self.DRAFT and not (allow_cancel or allow_submit):
                raise ValidationError("Submitted or cancelled closing entries cannot be modified.")
            if previous.status != self.DRAFT and self.status == self.SUBMITTED and not allow_submit:
                raise ValidationError("Only the close service can submit a closing entry.")
        if self.opening_entry_id and self.period_start_date is None:
            self.period_start_date = self.opening_entry.period_start_date
        if self.opening_entry_id and not self.cashier_id:
            self.cashier_id = self.opening_entry.cashier_id
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.opening_entry_id and not self.opening_entry.is_open:
            raise ValidationError(
                {
                    "opening_entry": (
                        "This opening entry is not open — it is already closed, cancelled, or still in draft."
                    )
                }
            )

    def cancel(self, by_user=None):
        """Cancel a closing entry. Blocked if a new Open shift exists."""
        if self.status == self.CANCELLED:
            return
        new_open_exists = (
            POSOpeningEntry.objects.filter(
                status=self.SUBMITTED,
                closing_entry__isnull=True,
            )
            .exclude(pk=self.opening_entry_id)
            .exists()
        )
        if new_open_exists:
            raise ValidationError(
                "Cannot cancel this closing entry — a new shift is open. Close or cancel the new shift first."
            )
        if self.variance_journal_entry_id:
            journal = self.variance_journal_entry
            if journal.status == journal.SUBMITTED:
                journal.cancel()
        self.status = self.CANCELLED
        self.cancelled_at = timezone.now()
        if by_user is not None:
            self.cancelled_by = by_user
        self._allow_cancel = True
        try:
            self.save(update_fields=["status", "cancelled_at", "cancelled_by", "updated_at"])
        finally:
            del self._allow_cancel


class ClosingPayment(BaseModel):
    """Child row: one per payment method, with opening/expected/closing/difference."""

    closing_entry = models.ForeignKey(
        POSClosingEntry,
        on_delete=models.CASCADE,
        related_name="closing_payments",
    )
    mode_of_payment = models.ForeignKey(
        ModeOfPayment,
        on_delete=models.PROTECT,
        related_name="closing_payments",
    )
    opening_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), editable=False)
    expected_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), editable=False)
    closing_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))]
    )
    difference = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), editable=False)

    class Meta:
        unique_together = [("closing_entry", "mode_of_payment")]
        ordering = ["mode_of_payment__name"]

    def __str__(self):
        return f"{self.mode_of_payment.name}: closing {self.closing_amount} / expected {self.expected_amount}"

    def save(self, *args, **kwargs):
        if self.closing_entry_id:
            status = POSClosingEntry.objects.only("status").get(pk=self.closing_entry_id).status
            if status != POSClosingEntry.DRAFT:
                raise ValidationError("Closing rows cannot change after the close leaves draft.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.closing_entry_id:
            status = POSClosingEntry.objects.only("status").get(pk=self.closing_entry_id).status
            if status != POSClosingEntry.DRAFT:
                raise ValidationError("Closing rows cannot be deleted after the close leaves draft.")
        return super().delete(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.closing_entry_id and self.mode_of_payment_id:
            valid_modes = set(
                self.closing_entry.opening_entry.opening_payments.values_list("mode_of_payment_id", flat=True)
            )
            if self.mode_of_payment_id not in valid_modes:
                raise ValidationError({"mode_of_payment": (f"{self.mode_of_payment} was not declared at shift open.")})


class ShiftCashOut(BaseModel):
    """Cash leaving the drawer mid-shift for non-stock reasons."""

    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (SUBMITTED, "Submitted"),
        (CANCELLED, "Cancelled"),
    ]

    TRANSPORT = "TRANSPORT"
    ICE = "ICE"
    PETTY_REPAIRS = "PETTY_REPAIRS"
    OTHER = "OTHER"
    REASON_CHOICES = [
        (TRANSPORT, "Transport"),
        (ICE, "Ice"),
        (PETTY_REPAIRS, "Petty repairs"),
        (OTHER, "Other"),
    ]

    opening_entry = models.ForeignKey(
        POSOpeningEntry,
        on_delete=models.CASCADE,
        related_name="cash_outs",
    )
    mode_of_payment = models.ForeignKey(
        ModeOfPayment,
        on_delete=models.PROTECT,
        related_name="shift_cash_outs",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default=OTHER)
    note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=SUBMITTED)
    recorded_by = models.ForeignKey(
        CustomUser,
        on_delete=models.PROTECT,
        related_name="recorded_cash_outs",
    )
    cancelled_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_cash_outs",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Cash-out #{self.pk} — {self.amount}"

    def clean(self):
        super().clean()
        if self.amount is None or self.amount <= 0:
            raise ValidationError({"amount": "The cash-out amount must be greater than zero."})
        if self.reason == self.OTHER and not (self.note or "").strip():
            raise ValidationError({"note": "A note is required when the reason is Other."})
        if self.mode_of_payment_id:
            mode = self.mode_of_payment
            if not mode.enabled:
                raise ValidationError({"mode_of_payment": f"{mode} is disabled."})
            if mode.type != ModeOfPayment.TYPE_CASH:
                raise ValidationError({"mode_of_payment": f"{mode} is not a cash payment mode."})
        if self.opening_entry_id and self.mode_of_payment_id:
            valid_modes = set(self.opening_entry.opening_payments.values_list("mode_of_payment_id", flat=True))
            if self.mode_of_payment_id not in valid_modes:
                raise ValidationError({"mode_of_payment": (f"{self.mode_of_payment} was not declared at shift open.")})
        if self.opening_entry_id and not self.opening_entry.is_open:
            raise ValidationError("Cash-outs can only be recorded while the shift is open.")
