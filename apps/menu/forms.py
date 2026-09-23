from django import forms

from apps.inventory.models import Item
from apps.utils.forms import StyledModelForm

from .models import ItemAddOn, ItemVariant, Menu, MenuItem


class MenuModelForm(StyledModelForm):
    pass


class MenuForm(MenuModelForm):
    class Meta:
        model = Menu
        fields = ["name", "enabled"]


class MenuItemForm(MenuModelForm):
    class Meta:
        model = MenuItem
        fields = ["menu", "item", "item_name", "rate", "special_dish", "disabled"]
        help_texts = {
            "rate": "Customer-facing price shown on the POS.",
            "special_dish": "Show this line in the POS specials filter.",
            "disabled": "Hide this line from the POS without deleting the inventory item.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item_name"].required = False
        self.fields["item_name"].widget = forms.HiddenInput()
        menu_qs = Menu.objects.filter(enabled=True).order_by("name")
        if self.instance and self.instance.menu_id:
            menu_qs = menu_qs | Menu.objects.filter(pk=self.instance.menu_id)
        self.fields["menu"].queryset = menu_qs.distinct().order_by("name")
        # Sellable, non-template, active items only; keep the current selection.
        qs = Item.objects.filter(is_sales_item=True, has_variants=False, disabled=False).order_by("item_name")
        if self.instance and self.instance.item_id:
            qs = qs | Item.objects.filter(pk=self.instance.item_id)
        self.fields["item"].queryset = qs.distinct().order_by("item_name")


class ItemAddOnForm(MenuModelForm):
    class Meta:
        model = ItemAddOn
        fields = ["parent_item", "add_on_item"]
        help_texts = {
            "parent_item": "The sellable item that offers this extra.",
            "add_on_item": "The sellable item offered as a separate POS line; its menu line owns the price.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sellable = Item.objects.filter(is_sales_item=True, has_variants=False, disabled=False).order_by("item_name")
        parent_qs = sellable
        add_on_qs = sellable
        if self.instance and self.instance.pk:
            if self.instance.parent_item_id:
                parent_qs = (parent_qs | Item.objects.filter(pk=self.instance.parent_item_id)).distinct()
            if self.instance.add_on_item_id:
                add_on_qs = (add_on_qs | Item.objects.filter(pk=self.instance.add_on_item_id)).distinct()
        self.fields["parent_item"].queryset = parent_qs.order_by("item_name")
        self.fields["add_on_item"].queryset = add_on_qs.order_by("item_name")


class ItemVariantForm(MenuModelForm):
    class Meta:
        model = ItemVariant
        fields = ["parent_item", "variant_item"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        items = Item.objects.order_by("item_name")
        self.fields["parent_item"].queryset = items
        self.fields["variant_item"].queryset = items
