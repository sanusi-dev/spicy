from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.utils.models import BaseModel
from apps.utils.rounding import money


class LedgerAccount(BaseModel):
    """A chart-of-accounts node — a group (heading) or a leaf posting account."""

    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"
    ACCOUNT_TYPE_CHOICES = [
        (ASSET, "Asset"),
        (LIABILITY, "Liability"),
        (EQUITY, "Equity"),
        (INCOME, "Income"),
        (EXPENSE, "Expense"),
    ]

    BALANCE_SHEET = "BALANCE_SHEET"
    PROFIT_AND_LOSS = "PROFIT_AND_LOSS"
    REPORT_TYPE_CHOICES = [
        (BALANCE_SHEET, "Balance Sheet"),
        (PROFIT_AND_LOSS, "Profit & Loss"),
    ]

    name = models.CharField(max_length=200, unique=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    is_group = models.BooleanField(default=False)
    account_type = models.CharField(max_length=10, choices=ACCOUNT_TYPE_CHOICES, blank=True)
    report_type = models.CharField(max_length=15, choices=REPORT_TYPE_CHOICES, blank=True)
    account_number = models.CharField(max_length=50, blank=True)
    freeze_account = models.BooleanField(default=False)
    disabled = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_leaf(self):
        return not self.is_group

    def clean(self):
        super().clean()
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "Account name is required."})
        if self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": "An account cannot be its own parent."})
        # Detect cycles by walking up the parent chain.
        if self.parent_id:
            node = self.parent
            seen = {self.pk}
            while node is not None:
                if node.pk in seen:
                    raise ValidationError({"parent": "Account parent chain would create a cycle."})
                seen.add(node.pk)
                node = node.parent
        if self.parent_id and not self.parent.is_group:
            raise ValidationError({"parent": "The parent must be a group account."})
        if not self.parent_id and not self.account_type:
            raise ValidationError({"account_type": "Root accounts must declare an account type."})
        if self.parent_id:
            if not self.account_type:
                self.account_type = self.parent.account_type
            if self.account_type != self.parent.account_type:
                raise ValidationError({"account_type": "Account type must match the parent group."})
        if self.report_type and not self.account_type:
            raise ValidationError({"report_type": "A report type requires an account type."})
        if self.is_group and self.disabled and self.pk and self.children.exists():
            raise ValidationError({"disabled": "A group with children cannot be disabled."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.children.exists():
            raise ValidationError("Cannot delete an account that has children.")
        super().delete(*args, **kwargs)


class FiscalYear(BaseModel):
    """A financial year. Enabled years must not overlap."""

    name = models.CharField(max_length=10, unique=True)
    year_start_date = models.DateField()
    year_end_date = models.DateField()
    disabled = models.BooleanField(default=False)
    is_short_year = models.BooleanField(default=False)

    class Meta:
        ordering = ["year_start_date"]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.year_end_date and self.year_start_date and self.year_end_date <= self.year_start_date:
            raise ValidationError({"year_end_date": "The year end must be after the year start."})
        if self.disabled:
            return
        if not self.year_start_date or not self.year_end_date:
            return
        overlapping = (
            FiscalYear.objects.filter(disabled=False)
            .exclude(pk=self.pk)
            .filter(
                year_start_date__lte=self.year_end_date,
                year_end_date__gte=self.year_start_date,
            )
        )
        if overlapping.exists():
            raise ValidationError("Enabled fiscal years cannot overlap.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def get_for(cls, date):
        """Return the enabled fiscal year covering ``date``, or raise."""
        year = cls.objects.filter(disabled=False, year_start_date__lte=date, year_end_date__gte=date).first()
        if year is None:
            raise ValidationError(f"No enabled fiscal year covers {date}.")
        return year


class GLEntry(BaseModel):
    """One side of a journalised posting — immutable once created.

    Exactly one of debit/credit is non-zero; reversal postings mark the
    original row ``is_cancelled`` and write mirror rows.
    """

    posting_date = models.DateField()
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="gl_entries")
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    against = models.CharField(max_length=200, blank=True)
    voucher_type = models.CharField(max_length=50)
    voucher_no = models.CharField(max_length=100)
    remarks = models.TextField(blank=True)
    fiscal_year = models.ForeignKey(FiscalYear, on_delete=models.PROTECT, related_name="gl_entries")
    is_cancelled = models.BooleanField(default=False, editable=False)
    is_opening = models.BooleanField(default=False)

    class Meta:
        ordering = ["-posting_date", "-pk"]
        indexes = [
            models.Index(fields=["account", "posting_date"]),
            models.Index(fields=["voucher_type", "voucher_no"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(debit__gte=0) & models.Q(credit__gte=0),
                name="accounting_gl_amounts_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(debit__gt=0) | models.Q(credit__gt=0),
                name="accounting_gl_single_side",
            ),
        ]

    def __str__(self):
        side = f"Dr {self.debit}" if self.debit else f"Cr {self.credit}"
        return f"{self.voucher_type} {self.voucher_no} — {self.account.name} {side}"

    def clean(self):
        super().clean()
        if self.debit and self.credit:
            raise ValidationError("A GL entry cannot have both a debit and a credit.")
        if not self.debit and not self.credit:
            raise ValidationError("A GL entry must have a debit or a credit.")
        if self.account_id and not self.account.is_leaf:
            raise ValidationError({"account": "Only leaf accounts can receive postings."})
        if self.account_id and self.account.disabled:
            raise ValidationError({"account": f"Account {self.account.name} is disabled."})
        if self.account_id and self.account.freeze_account:
            raise ValidationError({"account": f"Account {self.account.name} is frozen."})
        if not self.fiscal_year_id:
            return
        if not (self.fiscal_year.year_start_date <= self.posting_date <= self.fiscal_year.year_end_date):
            raise ValidationError({"posting_date": f"Posting date is outside fiscal year {self.fiscal_year.name}."})

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            if previous.is_cancelled and not self.is_cancelled:
                raise ValidationError("A cancelled GL entry cannot be un-cancelled.")
            if self.is_cancelled != previous.is_cancelled:
                # Only the reversal flag may be flipped.
                if kwargs.get("update_fields") is None:
                    kwargs["update_fields"] = ["is_cancelled", "updated_at"]
                return super().save(*args, **kwargs)
            raise ValidationError("GL entries are immutable; post a reversal instead.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("GL entries cannot be deleted.")

    @classmethod
    def post(cls, *, posting_date, rows, voucher_type, voucher_no, remarks=""):
        """Create a balanced batch of GL entries atomically, resolving the fiscal year from the posting date."""
        rows = list(rows)
        total_debit = sum((row.get("debit") or Decimal("0") for row in rows), Decimal("0"))
        total_credit = sum((row.get("credit") or Decimal("0") for row in rows), Decimal("0"))
        if money(total_debit) != money(total_credit):
            raise ValidationError("GL batch is not balanced (total debit must equal total credit).")
        with transaction.atomic():
            fiscal_year = FiscalYear.get_for(posting_date)
            created = []
            for row in rows:
                entry = cls.objects.create(
                    posting_date=posting_date,
                    account=row["account"],
                    debit=row.get("debit", Decimal("0")),
                    credit=row.get("credit", Decimal("0")),
                    against=row.get("against", ""),
                    voucher_type=voucher_type,
                    voucher_no=voucher_no,
                    remarks=remarks,
                    fiscal_year=fiscal_year,
                )
                created.append(entry)
            return created


class JournalEntry(BaseModel):
    """A manual journal voucher — balanced, immutable after submit."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (CANCELLED, "Cancelled"),
    ]

    JOURNAL = "JOURNAL"
    CASH = "CASH"
    BANK = "BANK"
    OPENING = "OPENING"
    VOUCHER_TYPE_CHOICES = [
        (JOURNAL, "Journal Entry"),
        (CASH, "Cash Entry"),
        (BANK, "Bank Entry"),
        (OPENING, "Opening Entry"),
    ]

    voucher_type = models.CharField(max_length=20, choices=VOUCHER_TYPE_CHOICES, default=JOURNAL)
    posting_date = models.DateField()
    reference_no = models.CharField(max_length=50, blank=True)
    reference_date = models.DateField(null=True, blank=True)
    remark = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    total_debit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    total_credit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    difference = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    is_opening = models.BooleanField(default=False, editable=False)
    amended_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="amendments",
    )

    class Meta:
        ordering = ["-posting_date", "-pk"]

    def __str__(self):
        return f"{self.get_voucher_type_display()} #{self.pk} ({self.get_status_display()})"

    def _recompute_totals(self):
        rows = list(self.accounts.all())
        self.total_debit = sum((row.debit for row in rows), Decimal("0"))
        self.total_credit = sum((row.credit for row in rows), Decimal("0"))
        self.difference = self.total_debit - self.total_credit

    def save(self, *args, **kwargs):
        if self.pk:
            previous = (
                type(self)
                .objects.only("status", "is_opening", "voucher_type", "posting_date", "amended_from_id")
                .get(pk=self.pk)
            )
            allow_cancel = getattr(self, "_allow_cancel", False)
            allow_submit = getattr(self, "_allow_submit", False)
            if previous.status != self.DRAFT and not allow_cancel:
                raise ValidationError("Only draft journal entries can be edited.")
            if previous.voucher_type == self.OPENING and self.voucher_type != self.OPENING:
                raise ValidationError("An opening entry's voucher type cannot change.")
        if self.status != self.DRAFT and not (allow_submit or allow_cancel):
            raise ValidationError("Use submit() to submit a journal entry.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk and self.status != self.DRAFT:
            raise ValidationError("Submitted or cancelled journal entries cannot be deleted.")
        super().delete(*args, **kwargs)

    @transaction.atomic
    def submit(self):
        """Post the journal to the GL. Atomic; validates balance and rows."""
        if self.status == self.SUBMITTED:
            return
        if self.status != self.DRAFT:
            raise ValidationError("Only draft journal entries can be submitted.")
        locked = type(self).objects.select_for_update().get(pk=self.pk)
        rows = list(locked.accounts.all())
        if not rows:
            raise ValidationError("A journal entry must have at least one account row.")
        for row in rows:
            if row.debit and row.credit:
                raise ValidationError("An account row cannot have both a debit and a credit.")
            if not row.debit and not row.credit:
                raise ValidationError("Every account row must have a debit or a credit.")
        seen = set()
        for row in rows:
            key = row.account_id
            if key in seen:
                raise ValidationError("Duplicate account rows are not allowed.")
            seen.add(key)
        locked._recompute_totals()
        if locked.total_debit != locked.total_credit:
            raise ValidationError("The journal entry must balance (total debit = total credit).")
        if locked.total_debit <= 0:
            raise ValidationError("The journal entry total must be greater than zero.")
        if locked.voucher_type == self.OPENING:
            for row in rows:
                if not (row.remarks or "").strip():
                    raise ValidationError("Every opening-balance row needs a source or note.")
            fiscal_year = FiscalYear.get_for(locked.posting_date)
            dup_entry = (
                type(self)
                .objects.filter(
                    voucher_type=self.OPENING,
                    status=self.SUBMITTED,
                    posting_date__gte=fiscal_year.year_start_date,
                    posting_date__lte=fiscal_year.year_end_date,
                )
                .exclude(pk=locked.pk)
                .exists()
            )
            if dup_entry:
                raise ValidationError(f"An opening journal entry already exists for fiscal year {fiscal_year.name}.")
        if locked.voucher_type == self.OPENING:
            locked.is_opening = True
        locked.status = self.SUBMITTED
        locked._allow_submit = True
        try:
            locked.save(
                update_fields=["total_debit", "total_credit", "difference", "is_opening", "status", "updated_at"]
            )
        finally:
            del locked._allow_submit
        GLEntry.post(
            posting_date=locked.posting_date,
            rows=[
                {
                    "account": row.account,
                    "debit": row.debit,
                    "credit": row.credit,
                    "against": row.remarks or "",
                }
                for row in rows
            ],
            voucher_type="Journal Entry",
            voucher_no=str(locked.pk),
            remarks=locked.remark,
        )
        if locked.is_opening:
            GLEntry.objects.filter(voucher_type="Journal Entry", voucher_no=str(locked.pk)).update(is_opening=True)

    @transaction.atomic
    def cancel(self):
        """Cancel a submitted entry — post mirrored negated GL rows and mark originals cancelled."""
        locked = type(self).objects.select_for_update().get(pk=self.pk)
        if locked.status == self.CANCELLED:
            return
        if locked.status != self.SUBMITTED:
            raise ValidationError("Only submitted journal entries can be cancelled.")
        original_rows = list(locked.accounts.all())
        posted = GLEntry.objects.filter(voucher_type="Journal Entry", voucher_no=str(locked.pk), is_cancelled=False)
        for gl in posted:
            gl.is_cancelled = True
            gl.save(update_fields=["is_cancelled", "updated_at"])
        GLEntry.post(
            posting_date=locked.posting_date,
            rows=[
                {
                    "account": row.account,
                    "debit": row.credit,
                    "credit": row.debit,
                    "against": row.remarks or "",
                }
                for row in original_rows
            ],
            voucher_type="Journal Entry",
            voucher_no=str(locked.pk),
            remarks="Reversal",
        )
        locked.status = self.CANCELLED
        locked._allow_cancel = True
        try:
            locked.save(update_fields=["status", "updated_at"])
        finally:
            del locked._allow_cancel

    def amend(self):
        """Create a new DRAFT copy of a CANCELLED entry, linked via amended_from."""
        persisted = type(self).objects.only("status").get(pk=self.pk)
        if persisted.status != self.CANCELLED:
            raise ValidationError("Only cancelled journal entries can be amended.")
        if self.amendments.exists():
            raise ValidationError("This journal entry has already been amended.")
        copy = JournalEntry.objects.create(
            voucher_type=self.voucher_type,
            posting_date=self.posting_date,
            reference_no=self.reference_no,
            reference_date=self.reference_date,
            remark=self.remark,
            amended_from=self,
        )
        for row in self.accounts.all():
            JournalEntryAccount.objects.create(
                journal_entry=copy,
                account=row.account,
                debit=row.debit,
                credit=row.credit,
                remarks=row.remarks,
            )
        return copy


class JournalEntryAccount(BaseModel):
    """A single debit/credit row on a journal entry."""

    journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="journal_rows")
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    remarks = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        side = f"Dr {self.debit}" if self.debit else f"Cr {self.credit}"
        return f"{self.account.name} {side}"

    def clean(self):
        super().clean()
        if self.debit and self.credit:
            raise ValidationError("A journal row cannot have both a debit and a credit.")
        if not self.debit and not self.credit:
            raise ValidationError("A journal row must have a debit or a credit.")
        if self.account_id and not self.account.is_leaf:
            raise ValidationError({"account": "Only leaf accounts can be journalised."})
        if self.account_id and self.account.disabled:
            raise ValidationError({"account": f"Account {self.account.name} is disabled."})
        if self.account_id and self.account.freeze_account:
            raise ValidationError({"account": f"Account {self.account.name} is frozen."})

    def save(self, *args, **kwargs):
        if self.journal_entry_id:
            journal = self.journal_entry
            if journal.pk and journal.status != JournalEntry.DRAFT:
                raise ValidationError("Only draft journal entries can have rows added or edited.")
        self.full_clean()
        super().save(*args, **kwargs)


from .payables_models import (  # noqa: E402,F401
    Supplier,
    SupplierInvoice,
    SupplierInvoiceExpense,
    SupplierInvoiceItem,
    SupplierPayment,
    SupplierPaymentAllocation,
)
