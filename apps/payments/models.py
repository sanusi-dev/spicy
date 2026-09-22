from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.utils.models import BaseModel


def _sales_account_ids():
    """Accounts that record sales or reduce them on refunds — production units or Restaurant defaults."""
    from apps.settings.models import ProductionUnit, Restaurant

    ids = set(ProductionUnit.objects.filter(income_account__isnull=False).values_list("income_account_id", flat=True))
    ids |= set(
        ProductionUnit.objects.filter(sales_returns_account__isnull=False).values_list(
            "sales_returns_account_id", flat=True
        )
    )
    restaurant = Restaurant.objects.first()
    if restaurant is not None:
        if restaurant.default_income_account_id:
            ids.add(restaurant.default_income_account_id)
        if restaurant.default_sales_returns_account_id:
            ids.add(restaurant.default_sales_returns_account_id)
    return ids


class ModeOfPayment(BaseModel):
    """A payment method the restaurant accepts."""

    TYPE_CASH = "CASH"
    TYPE_BANK = "BANK"
    TYPE_GENERAL = "GENERAL"
    TYPE_PHONE = "PHONE"
    TYPE_CHOICES = [
        (TYPE_CASH, "Cash"),
        (TYPE_BANK, "Bank"),
        (TYPE_GENERAL, "General"),
        (TYPE_PHONE, "Phone"),
    ]

    name = models.CharField(max_length=50, unique=True)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    enabled = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="payments_one_default_mode",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.is_default:
            clash = ModeOfPayment.objects.filter(is_default=True).exclude(pk=self.pk).exists()
            if clash:
                raise ValidationError({"is_default": "Another payment method is already the default."})
        elif self.pk:
            # Unsetting the only default would leave the POS with no
            # preselected payment mode, so a replacement must be chosen first.
            was_default = ModeOfPayment.objects.filter(pk=self.pk, is_default=True).exists()
            if was_default and not ModeOfPayment.objects.filter(is_default=True).exclude(pk=self.pk).exists():
                raise ValidationError(
                    {"is_default": "Choose another default payment method before unsetting this one."}
                )

    def save(self, *args, **kwargs):
        # Model.save() does not call clean(), so ordinary admin/script saves
        # must enforce the same invariant as form validation.
        if self.pk and not self.is_default:
            was_default = ModeOfPayment.objects.filter(pk=self.pk, is_default=True).exists()
            if was_default and not ModeOfPayment.objects.filter(is_default=True).exclude(pk=self.pk).exists():
                raise ValidationError("Choose another default payment method before unsetting this one.")
        super().save(*args, **kwargs)

    @property
    def can_dispense_change(self):
        """Return True if this payment mode can dispense physical change."""
        return self.type == self.TYPE_CASH


class PaymentGLMapping(BaseModel):
    """Maps a ModeOfPayment to a General Ledger account."""

    mode_of_payment = models.OneToOneField(
        ModeOfPayment,
        on_delete=models.PROTECT,
        related_name="gl_mapping",
    )
    default_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="payment_gl_mappings",
        verbose_name="Default account",
        help_text="The account where payments made with this method are recorded.",
    )

    class Meta:
        ordering = ["mode_of_payment__name"]

    def __str__(self):
        return f"{self.mode_of_payment.name} → {self.default_account}"

    def clean(self):
        super().clean()
        if self.mode_of_payment_id and not self.default_account_id:
            raise ValidationError({"default_account": "A default GL account is required for the mapping."})
        if self.default_account_id and not self.default_account.is_leaf:
            raise ValidationError({"default_account": "Only leaf accounts can be mapped to payment modes."})
        if self.default_account_id and self.default_account_id in _sales_account_ids():
            raise ValidationError({"default_account": "Payment modes cannot be mapped to a sales account."})
