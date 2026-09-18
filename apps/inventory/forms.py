from decimal import Decimal

from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.urls import reverse

from apps.accounting.models import LedgerAccount
from apps.utils.forms import StyledModelForm, active_choices

from .models import (
    UOM,
    Item,
    ItemGroup,
    ItemUOMConversion,
    PurchaseReceipt,
    PurchaseReceiptItem,
    Recipe,
    RecipeItem,
    StockEntry,
    StockEntryDetail,
    StockReconciliation,
    StockReconciliationItem,
    Warehouse,
)


class InventoryModelForm(StyledModelForm):
    """Base ModelForm for inventory forms."""


class UOMForm(InventoryModelForm):
    class Meta:
        model = UOM
        fields = ["name"]


class ItemGroupForm(InventoryModelForm):
    class Meta:
        model = ItemGroup
        fields = ["name", "description"]


class WarehouseForm(InventoryModelForm):
    class Meta:
        model = Warehouse
        fields = ["name", "disabled", "account"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs["placeholder"] = "e.g. Main Store"
        self.fields["disabled"].label = "Disable this warehouse"
        self.fields["disabled"].help_text = (
            "When enabled, this warehouse will be unavailable for new inventory activity. "
            "Configured Store, Bar, or Kitchen warehouses cannot be disabled."
        )
        self.fields["account"].queryset = active_choices(
            LedgerAccount, self.instance.account_id, disabled=False, is_group=False
        )


class ItemForm(InventoryModelForm):
    class Meta:
        model = Item
        fields = [
            "item_name",
            "item_group",
            "stock_uom",
            "department",
            "image",
            "description",
            "disabled",
            "is_stock_item",
            "is_sales_item",
            "is_purchase_item",
            "has_variants",
            "variant_of",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item_name"].widget.attrs["placeholder"] = "e.g. Jollof Rice"
        self.fields["description"].widget.attrs["placeholder"] = (
            "e.g. Long-grain rice served with tomato stew and grilled chicken."
        )
        department_choices = list(self.fields["department"].choices)
        department_choices[0] = ("", "Select a department...")
        self.fields["department"].choices = department_choices

        checkbox_copy = {
            "is_stock_item": (
                "Track stock levels",
                "When enabled, this item’s stock quantity and inventory movements will be tracked automatically.",
            ),
            "is_sales_item": (
                "Sell this item",
                "When enabled, this item can be added to menus and sold through the POS.",
            ),
            "is_purchase_item": (
                "Buy this item",
                "When enabled, this item can be included on purchase receipts.",
            ),
            "disabled": (
                "Pause this item",
                "When enabled, this item will be hidden from new orders and purchases.",
            ),
            "has_variants": (
                "Use variants",
                "When enabled, this item becomes a template for variants and cannot be stocked, sold, or purchased directly.",
            ),
        }
        for field_name, (label, help_text) in checkbox_copy.items():
            self.fields[field_name].label = label
            self.fields[field_name].help_text = help_text

        # Keep any currently-assigned (now disabled) row visible when editing.
        self.fields["variant_of"].queryset = active_choices(
            Item, self.instance.variant_of_id, disabled=False, has_variants=True
        )
        self.fields["is_stock_item"].widget.attrs["x-model"] = "isStock"
        self.fields["is_purchase_item"].widget.attrs["x-model"] = "isPurchase"

    def clean(self):
        cleaned = super().clean()
        dept = cleaned.get("department") or getattr(self.instance, "department", None)
        is_stock = cleaned.get("is_stock_item")
        is_sales = cleaned.get("is_sales_item")
        is_purch = cleaned.get("is_purchase_item")
        has_variants = cleaned.get("has_variants")
        if has_variants:
            return cleaned
        if dept == "DRINKS" and is_stock is not None and is_sales is not None and is_purch is not None:
            if not (is_stock and is_sales and is_purch):
                raise ValidationError("Drinks items must be stock-tracked, sellable, and purchasable.")
        elif dept == "FOOD" and is_stock is not None and is_sales is not None and is_purch is not None:
            if is_sales:
                if is_stock or is_purch:
                    raise ValidationError(
                        "Sellable food items are virtual — they must not be stock-tracked or purchasable."
                    )
            else:
                if not (is_stock and is_purch):
                    raise ValidationError("Non-sellable food items must be stock-tracked and purchasable.")
        return cleaned


class ItemUOMConversionForm(InventoryModelForm):
    class Meta:
        model = ItemUOMConversion
        fields = ["uom", "conversion_factor"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        item = self.instance.item if self.instance.item_id else None
        qs = UOM.objects.all().order_by("name")
        if item and item.stock_uom_id:
            qs = qs.exclude(pk=item.stock_uom_id)
        current_id = self.instance.uom_id
        if current_id:
            qs = qs | UOM.objects.filter(pk=current_id)
        self.fields["uom"].queryset = qs.distinct()
        self.fields["conversion_factor"].label = "Factor"
        self.fields["conversion_factor"].help_text = "Stock units in one of this unit (e.g. 24 bottles per crate)"
        self.fields["conversion_factor"].widget.attrs["placeholder"] = "e.g. 24"

    def clean_conversion_factor(self):
        factor = self.cleaned_data.get("conversion_factor")
        if factor is not None and factor <= 0:
            raise ValidationError("Conversion factor must be greater than zero.")
        return factor


class BaseItemUOMConversionFormSet(BaseInlineFormSet):
    def _construct_form(self, i, **kwargs):
        form = super()._construct_form(i, **kwargs)
        if self.instance:
            form.instance.item = self.instance
        return form

    def clean(self):
        super().clean()
        item = self.instance
        if item is None:
            return
        has_rows = False
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            if form.cleaned_data.get("uom") or form.cleaned_data.get("conversion_factor"):
                has_rows = True
                break
        if not has_rows:
            return
        if item.has_variants or item.disabled or not item.is_stock_item or not item.is_purchase_item:
            raise ValidationError("UOM conversions are only for enabled stock and purchase items.")
        if getattr(item, "department", None) == "FOOD" and item.is_sales_item:
            raise ValidationError("Sellable food items cannot have UOM conversions.")


class StockEntryForm(InventoryModelForm):
    class Meta:
        model = StockEntry
        fields = ["purpose", "posting_date", "mode_of_payment", "remarks"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = list(self.fields["purpose"].choices)
        if choices and choices[0][0] == "":
            choices[0] = ("", "Select purpose...")
        else:
            choices.insert(0, ("", "Select purpose..."))
        self.fields["purpose"].choices = choices
        from apps.payments.models import ModeOfPayment

        self.fields["mode_of_payment"].queryset = active_choices(
            ModeOfPayment, self.instance.mode_of_payment_id, enabled=True
        )
        self.fields["mode_of_payment"].label = "Paid from"
        self.fields["mode_of_payment"].help_text = "Funding account for this market purchase."
        self.fields["mode_of_payment"].required = False
        # Visibility is handled in the template via Alpine x-show on purpose

    def clean(self):
        cleaned = super().clean()
        purpose = cleaned.get("purpose") or getattr(self.instance, "purpose", None)
        mode = cleaned.get("mode_of_payment")
        if purpose == "MATERIAL_RECEIPT" and not mode:
            self.add_error("mode_of_payment", "Select the payment mode that funded this receipt.")
        if purpose == "MATERIAL_TRANSFER" and mode:
            self.add_error("mode_of_payment", "Transfers do not have a funding account.")
        return cleaned


class StockEntryDetailForm(InventoryModelForm):
    class Meta:
        model = StockEntryDetail
        fields = ["item", "qty", "uom", "basic_rate"]

    def _purpose(self):
        purpose = self.data.get("purpose") if self.is_bound else None
        if purpose is None:
            # Meta rebuilds attach an unsaved parent so the line knows its purpose.
            parent = getattr(self.instance, "stock_entry", None)
            if parent is not None:
                purpose = getattr(parent, "purpose", None)
        return purpose

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        purpose = self._purpose()
        self.fields["item"].queryset = active_choices(Item, self.instance.item_id, disabled=False, is_stock_item=True)
        self.fields["basic_rate"].initial = None
        self.fields["basic_rate"].required = purpose != "MATERIAL_TRANSFER"
        self.fields["basic_rate"].widget.attrs["x-bind:disabled"] = "purpose === 'MATERIAL_TRANSFER'"
        if purpose != "MATERIAL_TRANSFER":
            self.fields["basic_rate"].min_value = Decimal("0.01")
        item = self._bound_item()
        self.fields["uom"].queryset = _uoms_for_item(item)
        if purpose == "MATERIAL_TRANSFER":
            # Transfers are always entered in the item's stock unit — offer that only.
            if item:
                self.fields["uom"].queryset = UOM.objects.filter(pk=item.stock_uom_id)
            self.fields["uom"].widget.attrs["x-bind:disabled"] = "purpose === 'MATERIAL_TRANSFER'"
        if item and not self.is_bound and not self.instance.uom_id:
            self.fields["uom"].initial = item.stock_uom_id
        prefix = self.prefix or ""
        wrap_id = f"{prefix}-uom-wrap" if prefix else "uom-wrap"
        preview_id = f"{prefix}-stock-qty-preview" if prefix else "stock-qty-preview"
        self.fields["item"].widget.attrs.update(
            {
                "hx-get": reverse("inventory:stock_entry_item_meta"),
                "hx-target": f"#{wrap_id}",
                "hx-swap": "innerHTML",
                "hx-include": "closest .js-entry-line",
                "hx-trigger": "change",
            }
        )
        preview_attrs = {
            "hx-get": reverse("inventory:stock_entry_stock_qty_preview"),
            "hx-target": f"#{preview_id}",
            "hx-swap": "innerHTML",
            "hx-include": "closest .js-entry-line",
            "hx-trigger": "change, input delay:300ms",
        }
        self.fields["qty"].widget.attrs.update(preview_attrs)
        self.fields["uom"].widget.attrs.update(preview_attrs)

    def _bound_item(self):
        if self.is_bound:
            item_id = self.data.get(self.add_prefix("item"))
            if item_id:
                return Item.objects.filter(pk=item_id).select_related("stock_uom").first()
        if self.instance.item_id:
            return self.instance.item
        return None

    def clean(self):
        cleaned = super().clean()
        purpose = self._purpose()
        item = cleaned.get("item")
        uom = cleaned.get("uom")
        if purpose == "MATERIAL_RECEIPT" and item and uom:
            allowed = {item.stock_uom_id, *item.uom_conversions.values_list("uom_id", flat=True)}
            if uom.pk not in allowed:
                self.add_error("uom", "This unit is not valid for this item.")
        return cleaned


class StockReconciliationForm(InventoryModelForm):
    ACTIVE_REASON_CHOICES = [
        ("OPENING_STOCK", "Opening Stock"),
        ("ADJUSTMENT", "Adjustment"),
        ("CONSUMPTION", "Consumption"),
        ("WASTE_DAMAGE", "Waste / Damage"),
    ]

    class Meta:
        model = StockReconciliation
        fields = ["reason", "posting_date", "warehouse", "remarks"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].queryset = active_choices(Warehouse, self.instance.warehouse_id, disabled=False)
        self.fields["reason"].choices = [("", "Select reason..."), *self.ACTIVE_REASON_CHOICES]
        self.fields["reason"].help_text = (
            "Opening Stock seeds a fresh warehouse. Adjustment makes the bin match what you counted, up or down. "
            "Consumption is the end-of-day count of what is left and cannot exceed the bin. "
            "Waste / Damage records the quantity lost now, not what is left."
        )


class StockReconciliationItemForm(InventoryModelForm):
    class Meta:
        model = StockReconciliationItem
        fields = ["item", "qty", "valuation_rate"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = active_choices(Item, self.instance.item_id, disabled=False, is_stock_item=True)
        self.fields["qty"].label = "Counted quantity"
        self.fields["qty"].help_text = (
            "For Waste / Damage enter the quantity wasted, not what is left. "
            "For Consumption enter the count of what is left — it cannot exceed the bin."
        )
        self.fields["valuation_rate"].label = "Valuation rate"
        self.fields["valuation_rate"].help_text = "Required to seed stock for Opening Stock and empty bins."
        self.fields["valuation_rate"].widget.attrs["x-bind:disabled"] = "reason !== 'OPENING_STOCK'"


class PurchaseReceiptForm(InventoryModelForm):
    class Meta:
        model = PurchaseReceipt
        fields = [
            "supplier_name",
            "supplier",
            "supplier_delivery_note",
            "posting_date",
            "remarks",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.accounting.models import Supplier

        self.fields["supplier"].queryset = active_choices(Supplier, self.instance.supplier_id, disabled=False)
        self.fields["supplier"].required = False
        # Required-ness depends on the supplier choice — enforced in clean().
        self.fields["supplier_name"].required = False

    def clean(self):
        cleaned_data = super().clean()
        from apps.settings.models import Restaurant

        restaurant = Restaurant.load()
        if not restaurant or not restaurant.store_warehouse_id or restaurant.store_warehouse.disabled:
            raise ValidationError("Configure an enabled central Store warehouse before creating a purchase receipt.")
        # Selecting a Supplier master fills supplier_name so the receipt stays readable on its own.
        if not cleaned_data.get("supplier_name") and cleaned_data.get("supplier"):
            cleaned_data["supplier_name"] = cleaned_data["supplier"].supplier_name
        elif not cleaned_data.get("supplier_name"):
            self.add_error("supplier_name", "This field is required.")
        self.instance.warehouse = restaurant.store_warehouse
        return cleaned_data


def _uoms_for_item(item):
    if item is None:
        return UOM.objects.all().order_by("name")
    ids = [item.stock_uom_id, *item.uom_conversions.values_list("uom_id", flat=True)]
    return UOM.objects.filter(pk__in=ids).order_by("name")


class PurchaseReceiptItemForm(InventoryModelForm):
    class Meta:
        model = PurchaseReceiptItem
        fields = ["item", "received_qty", "uom", "rate"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = active_choices(
            Item,
            self.instance.item_id,
            disabled=False,
            is_stock_item=True,
            is_purchase_item=True,
        )
        self.fields["rate"].initial = None
        self.fields["rate"].min_value = Decimal("0.01")
        item = self._bound_item()
        self.fields["uom"].queryset = _uoms_for_item(item)
        if item and not self.is_bound and not self.instance.uom_id:
            self.fields["uom"].initial = item.stock_uom_id
        prefix = self.prefix or ""
        wrap_id = f"{prefix}-uom-wrap" if prefix else "uom-wrap"
        preview_id = f"{prefix}-stock-qty-preview" if prefix else "stock-qty-preview"
        self.fields["item"].widget.attrs.update(
            {
                "hx-get": reverse("inventory:purchase_receipt_item_meta"),
                "hx-target": f"#{wrap_id}",
                "hx-swap": "innerHTML",
                "hx-include": "closest .js-receipt-line",
                "hx-trigger": "change",
            }
        )
        preview_attrs = {
            "hx-get": reverse("inventory:purchase_receipt_stock_qty_preview"),
            "hx-target": f"#{preview_id}",
            "hx-swap": "innerHTML",
            "hx-include": "closest .js-receipt-line",
            "hx-trigger": "change, input delay:300ms",
        }
        self.fields["received_qty"].widget.attrs.update(preview_attrs)
        self.fields["uom"].widget.attrs.update(preview_attrs)

    def _bound_item(self):
        if self.is_bound:
            item_id = self.data.get(self.add_prefix("item"))
            if item_id:
                return Item.objects.filter(pk=item_id).select_related("stock_uom").first()
        if self.instance.item_id:
            return self.instance.item
        return None

    def clean(self):
        cleaned = super().clean()
        item = cleaned.get("item")
        uom = cleaned.get("uom")
        if item and uom:
            allowed = {item.stock_uom_id, *item.uom_conversions.values_list("uom_id", flat=True)}
            if uom.pk not in allowed:
                self.add_error("uom", "This unit is not valid for this item.")
        return cleaned


StockEntryDetailFormSet = inlineformset_factory(
    StockEntry, StockEntryDetail, form=StockEntryDetailForm, extra=1, can_delete=True
)
StockReconciliationItemFormSet = inlineformset_factory(
    StockReconciliation,
    StockReconciliationItem,
    form=StockReconciliationItemForm,
    extra=1,
    can_delete=True,
)
PurchaseReceiptItemFormSet = inlineformset_factory(
    PurchaseReceipt, PurchaseReceiptItem, form=PurchaseReceiptItemForm, extra=1, can_delete=True
)
ItemUOMConversionFormSet = inlineformset_factory(
    Item,
    ItemUOMConversion,
    form=ItemUOMConversionForm,
    formset=BaseItemUOMConversionFormSet,
    extra=1,
    can_delete=True,
)


class RecipeForm(InventoryModelForm):
    class Meta:
        model = Recipe
        fields = ["item", "output_qty", "is_active", "remarks"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = active_choices(
            Item,
            self.instance.item_id,
            disabled=False,
            department="FOOD",
            is_sales_item=True,
            has_variants=False,
        )
        self.fields["output_qty"].help_text = "Portions this card produces (yield is baked into the line qtys)."
        self.fields["is_active"].help_text = "One active recipe per item. Deactivate this one before adding another."

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk and not cleaned.get("is_active", True):
            return cleaned
        item = cleaned.get("item") or getattr(self.instance, "item", None)
        if item is not None and cleaned.get("is_active", True):
            clash = Recipe.objects.filter(item=item, is_active=True)
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error("item", "This item already has an active recipe — deactivate it first.")
        return cleaned


class RecipeItemForm(InventoryModelForm):
    class Meta:
        model = RecipeItem
        fields = ["ingredient", "qty"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ingredient"].queryset = active_choices(
            Item,
            self.instance.ingredient_id,
            disabled=False,
            department="FOOD",
            is_sales_item=False,
            is_stock_item=True,
            is_purchase_item=True,
            has_variants=False,
        )
        self.fields["qty"].label = "Qty (stock UOM)"
        self.fields["qty"].help_text = "Per output above, in the ingredient's stock unit (e.g. 0.125 Kg per plate)."


RecipeItemFormSet = inlineformset_factory(Recipe, RecipeItem, form=RecipeItemForm, extra=1, can_delete=True)
