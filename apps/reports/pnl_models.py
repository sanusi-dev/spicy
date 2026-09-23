"""Daily P&L document and its snapshot / input rows."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q

from apps.utils.models import BaseModel

from .models import DEPARTMENT_CHOICES, PnLConfiguration, PnLMaterial


def _money(**kwargs):
    defaults = {"max_digits": 14, "decimal_places": 2, "default": Decimal("0"), "editable": False}
    defaults.update(kwargs)
    return models.DecimalField(**defaults)


def _pct():
    return models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0"), editable=False)


class DailyPnL(BaseModel):
    """One restaurant business day's profit and loss snapshot."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (CANCELLED, "Cancelled"),
    ]

    business_date = models.DateField()
    period_start = models.DateTimeField(editable=False)
    period_end = models.DateTimeField(editable=False)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    electricity_opening = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    electricity_closing = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    electricity_rate = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"), editable=False)
    employee_cost_override = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    remarks = models.TextField(blank=True)
    amended_from = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="amendments",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_daily_pnls",
    )

    gross_sales = _money()
    gross_sales_food = _money()
    gross_sales_drinks = _money()
    round_off = _money()
    net_sales = _money()
    cogs = _money()
    cogs_drinks = _money()
    kitchen_consumption = _money()
    theoretical_food_cost = _money()
    food_cost_variance = _money()
    total_direct_expenses = _money()
    gross_profit = _money()
    total_employee_costs = _money()
    depreciation = _money()
    cash_variance = _money()
    total_indirect_expenses = _money()
    prime_cost = _money()
    net_profit = _money()

    gross_sales_percent = _pct()
    round_off_percent = _pct()
    net_sales_percent = _pct()
    cogs_percent = _pct()
    kitchen_consumption_percent = _pct()
    theoretical_food_cost_percent = _pct()
    food_cost_variance_percent = _pct()
    total_direct_expenses_percent = _pct()
    gross_profit_percent = _pct()
    total_employee_costs_percent = _pct()
    depreciation_percent = _pct()
    cash_variance_percent = _pct()
    total_indirect_expenses_percent = _pct()
    prime_cost_percent = _pct()
    net_profit_percent = _pct()

    class Meta:
        ordering = ["-business_date", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["business_date"],
                condition=Q(status="DRAFT"),
                name="reports_dailypnl_one_draft_per_date",
            ),
            models.UniqueConstraint(
                fields=["business_date"],
                condition=Q(status="SUBMITTED"),
                name="reports_dailypnl_one_submitted_per_date",
            ),
        ]

    def __str__(self):
        return f"Daily P&L {self.business_date} ({self.get_status_display()})"

    def _refresh_period(self):
        from .services import business_day_window

        config = PnLConfiguration.load()
        self.period_start, self.period_end = business_day_window(self.business_date, config.business_day_start_hour)

    def clean(self):
        super().clean()
        opening, closing = self.electricity_opening, self.electricity_closing
        if (opening is None) ^ (closing is None):
            raise ValidationError("Enter both electricity readings, or leave both blank.")
        if opening is not None and closing is not None and closing < opening:
            raise ValidationError({"electricity_closing": "Closing reading cannot be below opening."})
        if self.employee_cost_override is not None and self.employee_cost_override < 0:
            raise ValidationError({"employee_cost_override": "Employee cost override cannot be negative."})
        if self.business_date:
            siblings = type(self).objects.filter(business_date=self.business_date).exclude(pk=self.pk)
            if self.status == self.DRAFT and siblings.filter(status__in=[self.DRAFT, self.SUBMITTED]).exists():
                raise ValidationError({"business_date": "A draft or submitted Daily P&L already exists for this date."})
            if self.status == self.SUBMITTED and siblings.filter(status=self.SUBMITTED).exists():
                raise ValidationError({"business_date": "A submitted Daily P&L already exists for this date."})

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.only("status", "business_date").get(pk=self.pk)
            allow_cancel = getattr(self, "_allow_cancel", False)
            allow_submit = getattr(self, "_allow_submit", False)
            if previous.status != self.DRAFT and not (allow_cancel or allow_submit):
                raise ValidationError("Only draft Daily P&L documents can be edited.")
            if previous.business_date != self.business_date:
                raise ValidationError({"business_date": "The business date cannot change after create."})
        if self.status == self.DRAFT or not self.period_start:
            self._refresh_period()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk and self.status != self.DRAFT:
            raise ValidationError("Only draft Daily P&L documents can be deleted.")
        return super().delete(*args, **kwargs)

    def submit(self, actor=None):
        from .services import submit_daily_pnl

        submit_daily_pnl(self, actor=actor)

    @transaction.atomic
    def cancel(self):
        """Cancel a submitted P&L."""
        locked = type(self).objects.select_for_update().get(pk=self.pk)
        if locked.status == self.CANCELLED:
            return
        if locked.status != self.SUBMITTED:
            raise ValidationError("Only submitted Daily P&L documents can be cancelled.")
        locked.status = self.CANCELLED
        locked._allow_cancel = True
        try:
            locked.save(update_fields=["status", "updated_at"])
        finally:
            del locked._allow_cancel
        self.status = locked.status

    def amend(self):
        """Copy this P&L's inputs into a new draft."""
        persisted = type(self).objects.only("status", "business_date").get(pk=self.pk)
        if persisted.status != self.CANCELLED:
            raise ValidationError("Only cancelled Daily P&L documents can be amended.")
        copy = DailyPnL(
            business_date=self.business_date,
            electricity_opening=self.electricity_opening,
            electricity_closing=self.electricity_closing,
            employee_cost_override=self.employee_cost_override,
            remarks=self.remarks,
            amended_from=self,
        )
        copy.save()
        for row in self.material_qtys.select_related("material").all():
            DailyPnLMaterialQty.objects.create(pnl=copy, material=row.material, qty=row.qty)
        for row in self.adhoc_rows.all():
            DailyPnLAdHoc.objects.create(
                pnl=copy,
                label=row.label,
                amount=row.amount,
                section=row.section,
                department=row.department,
            )
        return copy


class DailyPnLMaterialQty(BaseModel):
    """Draft input: quantity of a catalog material consumed on the day."""

    pnl = models.ForeignKey(DailyPnL, on_delete=models.CASCADE, related_name="material_qtys")
    material = models.ForeignKey(PnLMaterial, on_delete=models.PROTECT, related_name="pnl_quantities")
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    rate = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)

    class Meta:
        ordering = ["pk"]

    def clean(self):
        super().clean()
        if self.qty < 0:
            raise ValidationError({"qty": "Quantity cannot be negative."})
        if self.material_id and self.material.disabled and not self.pk:
            raise ValidationError({"material": "That material is disabled."})

    def save(self, *args, **kwargs):
        if self.pnl_id and self.pnl.status != DailyPnL.DRAFT:
            raise ValidationError("Only draft Daily P&L documents can have material rows edited.")
        self.full_clean()
        super().save(*args, **kwargs)


class DailyPnLAdHoc(BaseModel):
    """Draft input: a one-off direct or indirect expense on the day."""

    DIRECT = "DIRECT"
    INDIRECT = "INDIRECT"
    SECTION_CHOICES = [(DIRECT, "Direct"), (INDIRECT, "Indirect")]

    pnl = models.ForeignKey(DailyPnL, on_delete=models.CASCADE, related_name="adhoc_rows")
    label = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    section = models.CharField(max_length=10, choices=SECTION_CHOICES)
    department = models.CharField(max_length=10, choices=DEPARTMENT_CHOICES, blank=True)

    class Meta:
        ordering = ["pk"]

    def clean(self):
        super().clean()
        if self.amount <= 0:
            raise ValidationError({"amount": "Amount must be greater than zero."})

    def save(self, *args, **kwargs):
        if self.pnl_id and self.pnl.status != DailyPnL.DRAFT:
            raise ValidationError("Only draft Daily P&L documents can have ad-hoc rows edited.")
        self.full_clean()
        super().save(*args, **kwargs)


class DailyPnLLine(BaseModel):
    """Immutable statement row written on submit."""

    GROSS_SALES = "GROSS_SALES"
    ROUND_OFF = "ROUND_OFF"
    NET_SALES = "NET_SALES"
    COGS = "COGS"
    THEORETICAL_FOOD_COST = "THEORETICAL_FOOD_COST"
    FOOD_COST_VARIANCE = "FOOD_COST_VARIANCE"
    KITCHEN_CONSUMPTION = "KITCHEN_CONSUMPTION"
    DIRECT = "DIRECT"
    GROSS_PROFIT = "GROSS_PROFIT"
    PRIME_COST = "PRIME_COST"
    EMPLOYEE = "EMPLOYEE"
    INDIRECT = "INDIRECT"
    DEPRECIATION = "DEPRECIATION"
    CASH_VARIANCE = "CASH_VARIANCE"
    NET_PROFIT = "NET_PROFIT"
    SECTION_CHOICES = [
        (GROSS_SALES, "Gross sales"),
        (ROUND_OFF, "Round-off"),
        (NET_SALES, "Net sales"),
        (COGS, "COGS"),
        (THEORETICAL_FOOD_COST, "Theoretical food cost"),
        (FOOD_COST_VARIANCE, "Food cost variance"),
        (KITCHEN_CONSUMPTION, "Kitchen consumption"),
        (DIRECT, "Direct"),
        (GROSS_PROFIT, "Gross profit"),
        (PRIME_COST, "Prime cost"),
        (EMPLOYEE, "Employee"),
        (INDIRECT, "Indirect"),
        (DEPRECIATION, "Depreciation"),
        (CASH_VARIANCE, "Cash variance"),
        (NET_PROFIT, "Net profit"),
    ]
    COMPUTED = "COMPUTED"
    SETTINGS = "SETTINGS"
    METER = "METER"
    MATERIAL = "MATERIAL"
    ADHOC = "ADHOC"
    VARIANCE = "VARIANCE"
    SOURCE_CHOICES = [
        (COMPUTED, "Computed"),
        (SETTINGS, "Settings"),
        (METER, "Meter"),
        (MATERIAL, "Material"),
        (ADHOC, "Ad-hoc"),
        (VARIANCE, "Variance"),
    ]

    pnl = models.ForeignKey(DailyPnL, on_delete=models.CASCADE, related_name="lines")
    section = models.CharField(max_length=24, choices=SECTION_CHOICES)
    label = models.CharField(max_length=100)
    amount_food = _money()
    amount_drinks = _money()
    amount_total = _money()
    percent_of_gross = _pct()
    is_memo = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField()
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES, default=COMPUTED)

    class Meta:
        ordering = ["sort_order", "pk"]


class DailyPnLCogsRow(BaseModel):
    """Drink COGS / wastage breakup written on submit."""

    SALE = "SALE"
    RETURN = "RETURN"
    WASTAGE = "WASTAGE"
    KIND_CHOICES = [(SALE, "Sale"), (RETURN, "Return"), (WASTAGE, "Wastage")]

    pnl = models.ForeignKey(DailyPnL, on_delete=models.CASCADE, related_name="cogs_rows")
    item_name = models.CharField(max_length=200)
    department = models.CharField(max_length=10, default="DRINKS")
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)

    class Meta:
        ordering = ["kind", "item_name", "pk"]


class DailyPnLConsumptionRow(BaseModel):
    """Kitchen actual-usage breakup written on submit (consumption + waste)."""

    CONSUMPTION = "CONSUMPTION"
    WASTE = "WASTE"
    KIND_CHOICES = [(CONSUMPTION, "Consumption"), (WASTE, "Waste")]

    pnl = models.ForeignKey(DailyPnL, on_delete=models.CASCADE, related_name="consumption_rows")
    item_name = models.CharField(max_length=200)
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default=CONSUMPTION)

    class Meta:
        ordering = ["kind", "item_name", "pk"]


class DailyPnLTheoreticalRow(BaseModel):
    """Theoretical usage snapshot written on submit — frozen against later recipe edits."""

    pnl = models.ForeignKey(DailyPnL, on_delete=models.CASCADE, related_name="theoretical_rows")
    ingredient_name = models.CharField(max_length=200)
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["ingredient_name", "pk"]


class DailyPnLUnmappedRow(BaseModel):
    """Dishes sold with no active recipe — theoretical cost understated."""

    pnl = models.ForeignKey(DailyPnL, on_delete=models.CASCADE, related_name="unmapped_rows")
    item_name = models.CharField(max_length=200)
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["item_name", "pk"]
