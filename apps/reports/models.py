"""P&L configuration — singleton, material catalog, recurring expense templates."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from apps.utils.models import BaseModel

FOOD = "FOOD"
DRINKS = "DRINKS"
DEPARTMENT_CHOICES: list[tuple[str, str]] = [
    (FOOD, "Food"),
    (DRINKS, "Drinks"),
]


class PnLConfiguration(BaseModel):
    """Singleton Daily P&L settings: day boundary, meter rate, depreciation, variance toggle."""

    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    business_day_start_hour = models.PositiveSmallIntegerField(
        default=0,
        help_text=(
            "The hour your business day starts (0 is midnight). For example, 6 means the day runs from 6am to 6am."
        ),
    )
    electricity_rate = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal("0"),
        help_text="Cost in naira for each unit of electricity.",
    )
    daily_depreciation = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
        help_text="A fixed amount recorded each day for depreciation.",
    )
    include_cash_variance = models.BooleanField(
        default=True,
        help_text="When enabled, cash shortages or excesses at shift close appear on the Daily P&L.",
    )

    class Meta:
        verbose_name = "P&L configuration"
        verbose_name_plural = "P&L configuration"

    def __str__(self):
        return "P&L configuration"

    @classmethod
    def load(cls):
        """Return the singleton, creating an empty row on first visit."""
        obj, _created = cls.objects.get_or_create(singleton_key=1)
        return obj

    def clean(self):
        super().clean()
        if self.business_day_start_hour > 23:
            raise ValidationError({"business_day_start_hour": "Start hour must be between 0 and 23."})
        if self.electricity_rate < 0:
            raise ValidationError({"electricity_rate": "Electricity rate cannot be negative."})
        if self.daily_depreciation < 0:
            raise ValidationError({"daily_depreciation": "Depreciation cannot be negative."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PnLMaterial(BaseModel):
    """A consumable in the Daily P&L catalog (name, unit, rate)."""

    name = models.CharField(max_length=100, unique=True)
    unit = models.CharField(max_length=20)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    disabled = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.rate < 0:
            raise ValidationError({"rate": "Rate cannot be negative."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PnLRecurringExpense(BaseModel):
    """A remembered Daily P&L expense template (daily, monthly, percent, or employee)."""

    DIRECT_DAILY = "DIRECT_DAILY"
    INDIRECT_DAILY = "INDIRECT_DAILY"
    INDIRECT_MONTHLY = "INDIRECT_MONTHLY"
    INDIRECT_PERCENT = "INDIRECT_PERCENT"
    EMPLOYEE_DAILY = "EMPLOYEE_DAILY"
    EMPLOYEE_MONTHLY = "EMPLOYEE_MONTHLY"
    KIND_CHOICES = [
        (DIRECT_DAILY, "Direct — daily"),
        (INDIRECT_DAILY, "Indirect — daily"),
        (INDIRECT_MONTHLY, "Indirect — monthly"),
        (INDIRECT_PERCENT, "Indirect — % of gross sales"),
        (EMPLOYEE_DAILY, "Employee — daily"),
        (EMPLOYEE_MONTHLY, "Employee — monthly"),
    ]

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    percent = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0"))
    department = models.CharField(max_length=10, choices=DEPARTMENT_CHOICES, blank=True)
    disabled = models.BooleanField(default=False)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"

    def clean(self):
        super().clean()
        if self.amount < 0:
            raise ValidationError({"amount": "Amount cannot be negative."})
        if self.percent < 0:
            raise ValidationError({"percent": "Percent cannot be negative."})
        if self.kind == self.INDIRECT_PERCENT and self.percent <= 0:
            raise ValidationError({"percent": "Percentage expenses need a percent greater than zero."})
        if self.kind != self.INDIRECT_PERCENT and self.amount <= 0 and not self.disabled:
            raise ValidationError({"amount": "Amount must be greater than zero."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


from .pnl_models import (  # noqa: E402,F401
    DailyPnL,
    DailyPnLAdHoc,
    DailyPnLCogsRow,
    DailyPnLConsumptionRow,
    DailyPnLLine,
    DailyPnLMaterialQty,
    DailyPnLTheoreticalRow,
    DailyPnLUnmappedRow,
)
