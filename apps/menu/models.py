from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.inventory.models import Item
from apps.utils.departments import department_rule_breach
from apps.utils.models import BaseModel


class Menu(BaseModel):
    """A named menu for the restaurant."""

    name = models.CharField(max_length=100, unique=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class MenuItem(BaseModel):
    """A line on a menu: an Item sold at a specific rate."""

    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="menu_items")
    item_name = models.CharField(max_length=200)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    special_dish = models.BooleanField(default=False)
    disabled = models.BooleanField(default=False)

    class Meta:
        unique_together = [("menu", "item")]
        ordering = ["item_name"]
        constraints = [
            models.CheckConstraint(condition=Q(rate__gte=0), name="menu_item_rate_gte_zero"),
        ]

    def __str__(self):
        return self.item_name or self.item.item_code

    def save(self, *args, **kwargs):
        if not self.item_name and self.item:
            self.item_name = self.item.item_name
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.item_id:
            if self.item.has_variants:
                raise ValidationError(
                    {"item": "Template items cannot be added to a menu — add the size variants instead."}
                )
            if not self.item.is_sales_item:
                raise ValidationError({"item": "Only sellable items can be added to a menu."})
            if self.item.disabled:
                raise ValidationError({"item": "Disabled items cannot be added to a menu."})
            breach = department_rule_breach(self.item)
            if breach:
                raise ValidationError({"item": breach})
        if not self.rate and self.item and self.item.last_purchase_rate:
            self.rate = self.item.last_purchase_rate


class ItemAddOn(BaseModel):
    """An add-on that can be upsold alongside a parent item."""

    parent_item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="add_ons")
    add_on_item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="add_on_for")

    class Meta:
        unique_together = [("parent_item", "add_on_item")]

    def __str__(self):
        return f"{self.parent_item.item_name} + {self.add_on_item.item_name}"

    def clean(self):
        super().clean()
        if not self.add_on_item_id:
            return
        add_on = self.add_on_item
        if add_on.has_variants:
            raise ValidationError(
                {"add_on_item": "Template items cannot be used as add-ons — use a sellable size variant."}
            )
        if not add_on.is_sales_item:
            raise ValidationError({"add_on_item": "Only sellable items can be used as add-ons."})
        if add_on.disabled:
            raise ValidationError({"add_on_item": "Disabled items cannot be used as add-ons."})
        if add_on.department == "DRINKS":
            if not (add_on.is_stock_item and add_on.is_sales_item and add_on.is_purchase_item):
                raise ValidationError(
                    {"add_on_item": "Drinks add-ons must be stock-tracked, sellable, and purchasable."}
                )
        elif add_on.department == "FOOD" and add_on.is_sales_item:
            if add_on.is_stock_item or add_on.is_purchase_item:
                raise ValidationError(
                    {
                        "add_on_item": (
                            "Sellable food add-ons are virtual — they must not be stock-tracked or purchasable."
                        )
                    }
                )
        if not MenuItem.objects.filter(item=add_on, disabled=False).exists():
            raise ValidationError(
                {"add_on_item": "Add-on item must be on an enabled menu line to have a resolvable POS price."}
            )


class ItemVariant(BaseModel):
    """A POS-level variant of a parent item (e.g. small / large size)."""

    parent_item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="pos_variants")
    variant_item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="pos_variant_of")

    class Meta:
        unique_together = [("parent_item", "variant_item")]

    def __str__(self):
        return f"{self.parent_item.item_name} → {self.variant_item.item_name}"

    def clean(self):
        super().clean()
        if not MenuItem.objects.filter(item=self.variant_item).exists():
            raise ValidationError("Variant item must be a member of at least one menu to have a resolvable POS price.")
