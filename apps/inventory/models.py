from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.utils.models import BaseModel
from apps.utils.rounding import TWO_PLACES, money


class InsufficientStock(ValidationError):
    """Raised when an outbound would drive Bin.actual_qty negative."""


def _assert_document_is_draft(document, *, action="modify"):
    """Reject mutations on submitted or cancelled inventory documents."""
    if document is None or not document.pk:
        return
    status = type(document).objects.only("status").get(pk=document.pk).status
    if status != "DRAFT":
        raise ValidationError(f"Cannot {action} a {status.lower()} inventory document.")


class UOM(BaseModel):
    """Unit of measure (e.g. Nos, Kg, Litre, Box)."""

    name = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ItemGroup(BaseModel):
    """A product category (e.g. Food, Drinks, Proteins)."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Warehouse(BaseModel):
    """A stock location (e.g. Main Store, Kitchen Store, Bar Store)."""

    name = models.CharField(max_length=100, unique=True)
    disabled = models.BooleanField(default=False)
    account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Account",
        help_text="The account that holds the value of stock in this warehouse.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not self.disabled or not self.pk:
            return

        from apps.settings.models import ProductionUnit, Restaurant

        configured_as = []
        if Restaurant.objects.filter(default_warehouse_id=self.pk).exists():
            configured_as.append("Bar / POS sales warehouse")
        if Restaurant.objects.filter(store_warehouse_id=self.pk).exists():
            configured_as.append("central Store warehouse")
        if ProductionUnit.objects.filter(warehouse_id=self.pk).exists():
            configured_as.append("production unit warehouse")
        if configured_as:
            raise ValidationError({"disabled": f"Cannot disable a configured {', '.join(configured_as)}."})


class Item(BaseModel):
    """A product or material tracked in inventory and sold via POS."""

    item_code = models.CharField(max_length=50, unique=True, blank=True)
    item_name = models.CharField(max_length=200)
    item_group = models.ForeignKey(ItemGroup, on_delete=models.PROTECT, related_name="items")
    stock_uom = models.ForeignKey(UOM, on_delete=models.PROTECT, related_name="items")
    department = models.CharField(
        max_length=10,
        choices=[("FOOD", "Food"), ("DRINKS", "Drinks")],
    )
    image = models.ImageField(upload_to="items/", default="items/default-item.png", blank=True)
    description = models.TextField(blank=True)
    disabled = models.BooleanField(default=False)
    is_stock_item = models.BooleanField(default=True)
    is_sales_item = models.BooleanField(
        default=False,
        help_text="Allow this item to be added to menus and sold at the POS.",
    )
    is_purchase_item = models.BooleanField(
        default=False,
        help_text="Allow this item to be added to purchase receipts.",
    )
    has_variants = models.BooleanField(default=False)
    variant_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="variants",
    )
    last_purchase_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["item_name"]

    def __str__(self):
        return self.item_name or self.item_code

    def save(self, *args, **kwargs):
        if self.has_variants:
            # Templates are structure only — never stocked or sold as a line.
            self.is_stock_item = False
            self.is_sales_item = False
            self.is_purchase_item = False
        if not self.pk and not self.item_code:
            with transaction.atomic():
                last = Item.objects.select_for_update().filter(item_code__regex=r"^ITEM-\d+$").order_by("-pk").first()
                num = 1 if last is None else int(last.item_code.split("-")[1]) + 1
                self.item_code = f"ITEM-{num:04d}"
            super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)
        # Non-sellable items cannot remain POS add-ons (price/sales path is invalid).
        if not self.is_sales_item and self.pk:
            from apps.menu.models import ItemAddOn

            ItemAddOn.objects.filter(add_on_item_id=self.pk).delete()

    def uom_factor(self, uom) -> Decimal:
        """Return stock units per one of ``uom`` (1 when ``uom`` is the stock unit)."""
        uom_id = getattr(uom, "pk", uom)
        if uom_id is None or uom_id == self.stock_uom_id:
            return Decimal("1")
        row = self.uom_conversions.filter(uom_id=uom_id).first()
        if row is None:
            raise ValidationError("No conversion is defined for this unit.")
        return row.conversion_factor

    def clean(self):
        super().clean()
        if self.has_variants and self.is_stock_item:
            raise ValidationError("Template items with variants cannot maintain stock")
        if self.has_variants and (self.is_sales_item or self.is_purchase_item):
            raise ValidationError("Template items cannot be sold or purchased — sell/buy the size variants instead.")
        if self.variant_of_id and not self.variant_of.has_variants:
            raise ValidationError("Parent item must have has_variants=True")
        if not self.has_variants and not self.variant_of_id:
            if self.department == "DRINKS":
                if not (self.is_stock_item and self.is_sales_item and self.is_purchase_item):
                    raise ValidationError(
                        {"is_stock_item": "Drinks items must be stock-tracked, sellable, and purchasable."}
                    )
            elif self.department == "FOOD":
                if self.is_sales_item:
                    if self.is_stock_item or self.is_purchase_item:
                        raise ValidationError(
                            {
                                "is_stock_item": (
                                    "Sellable food items are virtual — they must not be stock-tracked or purchasable."
                                )
                            }
                        )
                else:
                    if not (self.is_stock_item and self.is_purchase_item):
                        raise ValidationError(
                            {"is_stock_item": "Non-sellable food items must be stock-tracked and purchasable."}
                        )
        if self.pk and not self.is_sales_item:
            from apps.menu.models import MenuItem

            if MenuItem.objects.filter(item_id=self.pk, disabled=False).exists():
                raise ValidationError(
                    {
                        "is_sales_item": (
                            "This item is still on an enabled menu. "
                            "Disable or remove those menu lines before turning off Sellable."
                        )
                    }
                )
        if self.pk and self.uom_conversions.exists():
            previous = type(self).objects.only("stock_uom_id", "is_stock_item", "is_purchase_item").get(pk=self.pk)
            if self.stock_uom_id != previous.stock_uom_id:
                raise ValidationError({"stock_uom": "Cannot change the stock unit while UOM conversions exist."})
            if not self.is_stock_item:
                raise ValidationError({"is_stock_item": "Cannot turn off stock tracking while UOM conversions exist."})
            if not self.is_purchase_item:
                raise ValidationError({"is_purchase_item": "Cannot turn off purchasable while UOM conversions exist."})
        if self.pk and (self.recipes.exists() or RecipeItem.objects.filter(ingredient_id=self.pk).exists()):
            previous = type(self).objects.only("stock_uom_id").get(pk=self.pk)
            if self.stock_uom_id != previous.stock_uom_id:
                raise ValidationError({"stock_uom": "Cannot change the stock unit while recipes use this item."})
        if (
            self.pk
            and RecipeItem.objects.filter(ingredient_id=self.pk).exists()
            and (self.is_sales_item or not self.is_stock_item)
        ):
            raise ValidationError("This item is a recipe ingredient and must stay stock-tracked and non-sellable.")


class ItemUOMConversion(BaseModel):
    """One bulk purchase unit for an item, converting to that item's stock UOM."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="uom_conversions")
    uom = models.ForeignKey(UOM, on_delete=models.PROTECT, related_name="+")
    conversion_factor = models.DecimalField(max_digits=10, decimal_places=4)

    class Meta:
        unique_together = ("item", "uom")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(conversion_factor__gt=0),
                name="inventory_itemuomconversion_factor_gt_0",
            ),
        ]

    def __str__(self):
        stock = self.item.stock_uom.name if self.item_id else ""
        return f"1 {self.uom} = {self.conversion_factor} {stock}"

    def clean(self):
        super().clean()
        if self.conversion_factor is not None and self.conversion_factor <= 0:
            raise ValidationError({"conversion_factor": "Conversion factor must be greater than zero."})
        if not self.item_id:
            return
        item = self.item
        if item.disabled:
            raise ValidationError("UOM conversions require an enabled item.")
        if item.has_variants:
            raise ValidationError("Template items cannot have UOM conversions.")
        if not item.is_stock_item or not item.is_purchase_item:
            raise ValidationError("UOM conversions are only for stock-tracked, purchasable items.")
        if item.department == "FOOD" and item.is_sales_item:
            raise ValidationError("Sellable food items cannot have UOM conversions.")
        if self.uom_id and item.stock_uom_id and self.uom_id == item.stock_uom_id:
            raise ValidationError({"uom": "Cannot convert the stock unit to itself."})


class Bin(BaseModel):
    """The current stock snapshot for a single item in a single warehouse."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="bins")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="bins")
    actual_qty = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    reserved_qty = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    valuation_rate = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))

    class Meta:
        unique_together = [("item", "warehouse")]

    def __str__(self):
        return f"{self.item.item_code} @ {self.warehouse.name}: {self.actual_qty}"

    @property
    def stock_value(self):
        """Derived stock value (qty × WAC) for template/admin compatibility."""
        qty = self.actual_qty or Decimal("0")
        wac = self.valuation_rate or Decimal("0")
        return qty * wac

    @classmethod
    def get_or_create_bin(cls, item, warehouse):
        """Return the existing Bin for item+warehouse, or create a new one."""
        bin_obj, _created = cls.objects.get_or_create(item=item, warehouse=warehouse)
        return bin_obj

    @classmethod
    def get_or_create_bin_id(cls, item_id, warehouse_id):
        """Same as get_or_create_bin but takes ids directly — skips FK instance loads."""
        bin_obj, _created = cls.objects.get_or_create(item_id=item_id, warehouse_id=warehouse_id)
        return bin_obj


class StockLedgerEntry(BaseModel):
    """An immutable record of a single stock movement for one item in one warehouse."""

    VARIANCE_CHOICES = [
        ("CANCELLATION_WAC", "Cancellation WAC"),
        ("SALE_RETURN", "Sale Return"),
    ]

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="stock_ledger_entries")
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="stock_ledger_entries",
    )
    # Business date — always pass the voucher's posting_date explicitly; default is fallback.
    posting_date = models.DateField(default=timezone.localdate, editable=False)
    posting_datetime = models.DateTimeField(auto_now_add=True, editable=False)
    voucher_type = models.CharField(max_length=50)
    voucher_no = models.CharField(max_length=100)
    voucher_detail_no = models.CharField(max_length=100, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, editable=False)
    unit_rate = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"), editable=False)
    stock_value_change = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), editable=False)
    variance_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), editable=False)
    variance_type = models.CharField(
        max_length=20,
        choices=VARIANCE_CHOICES,
        blank=True,
        default="",
        editable=False,
    )
    reversal_of_sle = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reversals",
        editable=False,
    )

    class Meta:
        ordering = ["-posting_datetime"]
        indexes = [models.Index(fields=["item", "warehouse", "-posting_datetime"])]

    def __str__(self):
        sign = "+" if self.quantity >= 0 else ""
        return f"{sign}{self.quantity} {self.item.item_code} @ {self.warehouse.name}"

    @classmethod
    def create_entry(
        cls,
        item,
        warehouse,
        quantity=None,
        voucher_type="",
        voucher_no="",
        *,
        unit_rate=None,
        voucher_detail_no="",
        prevent_negative=False,
        posting_date=None,
        variance_amount=Decimal("0"),
        variance_type="",
        reversal_of_sle_id=None,
        inbound_value=None,
    ):
        """Create a ledger entry and update the corresponding Bin.

        ``quantity`` is signed: positive for receipts, negative for issues.
        ``unit_rate`` is the inbound rate (ignored for outbound where WAC supplies it).
        """
        if quantity is None:
            raise ValidationError("quantity is required")
        quantity = Decimal(str(quantity))
        if unit_rate is not None:
            unit_rate = Decimal(str(unit_rate))
        if variance_amount is not None:
            variance_amount = Decimal(str(variance_amount))
        with transaction.atomic():
            bin_obj = Bin.get_or_create_bin(item, warehouse)
            bin_obj = Bin.objects.select_for_update().get(pk=bin_obj.pk)
            return cls._create_entry_locked(
                item=item,
                warehouse=warehouse,
                quantity=quantity,
                voucher_type=voucher_type,
                voucher_no=voucher_no,
                unit_rate=unit_rate,
                voucher_detail_no=voucher_detail_no,
                prevent_negative=prevent_negative,
                posting_date=posting_date,
                variance_amount=variance_amount,
                variance_type=variance_type,
                reversal_of_sle_id=reversal_of_sle_id,
                inbound_value=inbound_value,
                bin_obj=bin_obj,
            )

    @classmethod
    def _create_entry_locked(
        cls,
        *,
        item,
        warehouse,
        quantity,
        voucher_type,
        voucher_no,
        unit_rate,
        voucher_detail_no,
        prevent_negative,
        bin_obj,
        posting_date=None,
        variance_amount=Decimal("0"),
        variance_type="",
        reversal_of_sle_id=None,
        inbound_value=None,
    ):
        quantity = Decimal(str(quantity))
        if unit_rate is not None:
            unit_rate = Decimal(str(unit_rate))
        variance_amount = Decimal("0") if variance_amount is None else Decimal(str(variance_amount))
        variance_type = variance_type or ""
        if inbound_value is not None:
            inbound_value = Decimal(str(inbound_value))

        # Posting date is business/audit date only — valuation always blends at current WAC.
        if posting_date is None:
            posting_date = timezone.localdate()
        if isinstance(posting_date, str):
            posting_date = date.fromisoformat(posting_date)
        if posting_date > timezone.localdate():
            raise ValidationError("Posting date cannot be in the future.")

        current_qty = bin_obj.actual_qty or Decimal("0")
        wac = bin_obj.valuation_rate or Decimal("0")
        quantity = Decimal(quantity)

        # Enforce non-negative stock everywhere (perpetual WAC invariant).
        new_qty = current_qty + quantity
        if new_qty < 0:
            raise InsufficientStock(f"Insufficient stock for {item.item_name} in {warehouse.name}.")

        stock_value_change = Decimal("0")
        resolved_rate = Decimal("0")

        if quantity > 0:
            # Inbound: blend at resolved_rate; missing rate falls back to current WAC (identity blend).
            resolved_rate = wac if unit_rate is None else unit_rate
            if resolved_rate < 0:
                raise ValidationError("Unit rate cannot be negative.")
            if inbound_value is None:
                inbound_value = quantity * resolved_rate
            elif inbound_value < 0:
                raise ValidationError("Inbound value cannot be negative.")
            new_wac = (current_qty * wac + inbound_value) / new_qty if new_qty != 0 else Decimal("0")
            stock_value_change = inbound_value
            bin_obj.valuation_rate = new_wac
        elif quantity < 0:
            # Outbound: always at current WAC; WAC unchanged.
            resolved_rate = wac
            stock_value_change = quantity * wac
        else:
            raise ValidationError("Quantity cannot be zero.")

        sle = cls.objects.create(
            item=item,
            warehouse=warehouse,
            quantity=quantity,
            unit_rate=resolved_rate,
            stock_value_change=stock_value_change,
            variance_amount=variance_amount,
            variance_type=variance_type,
            reversal_of_sle_id=reversal_of_sle_id,
            voucher_type=voucher_type,
            voucher_no=voucher_no,
            voucher_detail_no=voucher_detail_no,
            posting_date=posting_date,
        )

        bin_obj.actual_qty = new_qty
        bin_obj.save(update_fields=["actual_qty", "valuation_rate", "reserved_qty", "updated_at"])

        return sle


class StockEntry(BaseModel):
    """A stock receipt or Store-to-production-unit transfer."""

    purpose = models.CharField(
        max_length=30,
        choices=[
            ("MATERIAL_RECEIPT", "Material Receipt"),
            ("MATERIAL_TRANSFER", "Material Transfer"),
        ],
    )
    posting_date = models.DateField(default=timezone.now)
    status = models.CharField(
        max_length=10,
        choices=[("DRAFT", "Draft"), ("SUBMITTED", "Submitted"), ("CANCELLED", "Cancelled")],
        default="DRAFT",
    )
    mode_of_payment = models.ForeignKey(
        "payments.ModeOfPayment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stock_entries",
    )
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-posting_date", "-created_at"]

    def __str__(self):
        return f"{self.purpose} - {self.posting_date}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.only("status").get(pk=self.pk)
            if previous.status != "DRAFT" and self.status == previous.status:
                raise ValidationError(f"Cannot modify a {previous.status.lower()} stock entry.")
            if previous.status != "DRAFT" and self.status not in {"SUBMITTED", "CANCELLED"}:
                raise ValidationError(f"Cannot modify a {previous.status.lower()} stock entry.")
            if previous.status == "SUBMITTED" and self.status not in {"SUBMITTED", "CANCELLED"}:
                raise ValidationError("Submitted stock entries can only be cancelled.")
            if previous.status == "CANCELLED" and self.status != "CANCELLED":
                raise ValidationError("Cancelled stock entries cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk:
            _assert_document_is_draft(self, action="delete")
        return super().delete(*args, **kwargs)

    @staticmethod
    def stock_ledger_entries_for_voucher(voucher_no):
        return StockLedgerEntry.objects.select_related("item", "warehouse").filter(
            voucher_type__in=["Stock Entry", "Stock Entry Cancellation"], voucher_no=voucher_no
        )


class StockEntryDetail(BaseModel):
    """A single line item of a stock entry."""

    stock_entry = models.ForeignKey(StockEntry, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="stock_entry_details")
    source_warehouse = models.ForeignKey(
        Warehouse,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="outgoing_details",
    )
    target_warehouse = models.ForeignKey(
        Warehouse,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="incoming_details",
    )
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    uom = models.ForeignKey(
        UOM,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Unit the quantity is entered in — the stock unit or a bulk unit from the item's conversion table.",
    )
    conversion_factor = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("1"))
    basic_rate = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), editable=False)

    def __str__(self):
        return f"{self.item.item_code} x{self.qty}"

    def stock_qty(self):
        """Entered quantity converted to the item's stock UOM (2 dp)."""
        if self.stock_entry_id and self.stock_entry.purpose == "MATERIAL_TRANSFER":
            return self.qty
        qty = (Decimal(str(self.qty)) * Decimal(str(self.conversion_factor))).quantize(TWO_PLACES)
        if qty <= 0:
            raise ValidationError(f"Stock quantity for {self.item.item_name} must be greater than zero.")
        return qty

    def stock_unit_rate(self):
        """As-bought amount per stock UOM (2 dp)."""
        qty = self.stock_qty()
        amount = self.amount if self.amount else (Decimal(str(self.qty)) * Decimal(str(self.basic_rate)))
        amount = money(amount)
        return money(amount / qty)

    def _derive_conversion(self):
        """Populate uom/conversion_factor from the line's item, transfer-safe."""
        if not self.item_id:
            return
        item = self.item
        purpose = self.stock_entry.purpose if self.stock_entry_id else None
        if purpose == "MATERIAL_TRANSFER":
            if self.uom_id not in {None, item.stock_uom_id}:
                raise ValidationError("Transfer lines are entered in the item's stock unit.")
            self.uom_id = item.stock_uom_id
            self.conversion_factor = Decimal("1")
            return
        if not self.uom_id:
            self.uom_id = item.stock_uom_id
        if self.uom_id == item.stock_uom_id:
            self.conversion_factor = Decimal("1")
            return
        row = ItemUOMConversion.objects.filter(item_id=item.pk, uom_id=self.uom_id).first()
        if row is None:
            raise ValidationError(f"{item.item_name} has no conversion for the selected unit.")
        self.conversion_factor = row.conversion_factor

    def save(self, *args, **kwargs):
        if self.stock_entry_id:
            _assert_document_is_draft(self.stock_entry, action="modify lines on")
        # Submit-time warehouse saves must not refresh the frozen draft snapshot.
        update_fields = kwargs.get("update_fields")
        if update_fields is None or self._state.adding or {"item_id", "uom_id"} & set(update_fields):
            self._derive_conversion()
            if self.stock_entry_id and self.stock_entry.purpose == "MATERIAL_RECEIPT":
                self.amount = money(Decimal(str(self.qty)) * Decimal(str(self.basic_rate)))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.stock_entry_id:
            _assert_document_is_draft(self.stock_entry, action="delete lines from")
        return super().delete(*args, **kwargs)

    def clean(self):
        super().clean()
        if not self.stock_entry_id:
            return
        purpose = self.stock_entry.purpose
        if purpose == "MATERIAL_RECEIPT":
            if self.source_warehouse_id:
                raise ValidationError({"source_warehouse": "Material Receipt should not have a source warehouse."})
        elif (
            purpose == "MATERIAL_TRANSFER"
            and self.source_warehouse_id == self.target_warehouse_id
            and self.source_warehouse_id
        ):
            raise ValidationError("Source and target warehouses must differ.")

    def validate_for_submission(self, *, restaurant, targets):
        if self.qty <= 0:
            raise ValidationError(f"Quantity for {self.item.item_name} must be greater than zero.")
        if self.item.disabled or not self.item.is_stock_item or self.item.has_variants:
            raise ValidationError(f"{self.item.item_name} is not an enabled stock item.")
        item = self.item
        if self.stock_entry.purpose == "MATERIAL_RECEIPT":
            if not item.is_purchase_item:
                raise ValidationError(f"{item.item_name} is not purchasable.")
            if (self.basic_rate or Decimal("0")) <= 0:
                raise ValidationError(f"Rate for {item.item_name} must be greater than zero.")
            if item.department == "DRINKS":
                if not (item.is_stock_item and item.is_sales_item and item.is_purchase_item):
                    raise ValidationError(f"{item.item_name} must be a stock-tracked, sellable, purchasable drink.")
            elif item.department == "FOOD":
                if item.is_sales_item:
                    raise ValidationError(
                        f"{item.item_name} is a sellable food item and cannot be received into stock."
                    )
                if not (item.is_stock_item and item.is_purchase_item):
                    raise ValidationError(f"{item.item_name} must be a stock-tracked, purchasable food ingredient.")
            if self.source_warehouse_id:
                raise ValidationError("Material Receipt cannot have a source warehouse.")
            if self.target_warehouse_id and self.target_warehouse_id != restaurant.store_warehouse_id:
                raise ValidationError("Material Receipt target must be the configured central Store.")
        else:
            if item.department == "DRINKS":
                if not (item.is_stock_item and item.is_sales_item and item.is_purchase_item):
                    raise ValidationError(f"{item.item_name} must be a stock-tracked, sellable, purchasable drink.")
            elif item.department == "FOOD":
                if item.is_sales_item:
                    raise ValidationError(f"{item.item_name} is a sellable food item and cannot be transferred.")
                if not (item.is_stock_item and item.is_purchase_item):
                    raise ValidationError(f"{item.item_name} must be a stock-tracked, purchasable food ingredient.")
            target = targets[item.department]
            if self.source_warehouse_id and self.source_warehouse_id != restaurant.store_warehouse_id:
                raise ValidationError("Material Transfer source must be the configured central Store.")
            if self.target_warehouse_id and self.target_warehouse_id != target.pk:
                raise ValidationError(f"{item.item_name} must transfer to {target.name}.")


class StockReconciliation(BaseModel):
    """A document that adjusts stock to match a physical count."""

    OPENING_STOCK = "OPENING_STOCK"
    ADJUSTMENT = "ADJUSTMENT"
    CONSUMPTION = "CONSUMPTION"
    WASTE_DAMAGE = "WASTE_DAMAGE"

    reason = models.CharField(
        max_length=20,
        choices=[
            ("OPENING_STOCK", "Opening Stock"),
            ("ADJUSTMENT", "Adjustment"),
            ("CONSUMPTION", "Consumption"),
            ("WASTE_DAMAGE", "Waste / Damage"),
        ],
    )
    posting_date = models.DateField(default=timezone.now)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="reconciliations")
    status = models.CharField(
        max_length=10,
        choices=[("DRAFT", "Draft"), ("SUBMITTED", "Submitted"), ("CANCELLED", "Cancelled")],
        default="DRAFT",
    )
    remarks = models.TextField(blank=True)
    submitted_by = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="submitted_reconciliations",
    )
    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_by = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="cancelled_reconciliations",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-posting_date", "-created_at"]

    def __str__(self):
        return f"{self.get_reason_display()} - {self.warehouse.name} - {self.posting_date}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.only("status").get(pk=self.pk)
            if previous.status != "DRAFT" and self.status == previous.status:
                raise ValidationError(f"Cannot modify a {previous.status.lower()} stock reconciliation.")
            if previous.status == "SUBMITTED" and self.status not in {"SUBMITTED", "CANCELLED"}:
                raise ValidationError("Submitted reconciliations can only be cancelled.")
            if previous.status == "CANCELLED" and self.status != "CANCELLED":
                raise ValidationError("Cancelled reconciliations cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk:
            _assert_document_is_draft(self, action="delete")
        return super().delete(*args, **kwargs)


class StockReconciliationItem(BaseModel):
    """A single line item of a stock reconciliation (one item's counted qty)."""

    reconciliation = models.ForeignKey(
        StockReconciliation,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    current_qty = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        editable=False,
    )
    valuation_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"{self.item.item_code}: {self.qty}"

    def save(self, *args, **kwargs):
        if self.reconciliation_id:
            _assert_document_is_draft(self.reconciliation, action="modify lines on")
        if not self.pk and self.item_id and self.reconciliation_id:
            bin_obj = Bin.get_or_create_bin_id(self.item_id, self.reconciliation.warehouse_id)
            self.current_qty = bin_obj.actual_qty
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.reconciliation_id:
            _assert_document_is_draft(self.reconciliation, action="delete lines from")
        return super().delete(*args, **kwargs)


class PurchaseReceipt(BaseModel):
    """Records the receipt of goods from a supplier."""

    supplier_name = models.CharField(max_length=200)
    supplier = models.ForeignKey(
        "accounting.Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_receipts",
        verbose_name="Supplier",
        help_text="Link this receipt to a supplier record, if you keep one. The supplier name is still saved either way.",
    )
    supplier_delivery_note = models.CharField(max_length=100, blank=True)
    posting_date = models.DateField(default=timezone.now)
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="purchase_receipts",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=10,
        choices=[("DRAFT", "Draft"), ("SUBMITTED", "Submitted"), ("CANCELLED", "Cancelled")],
        default="DRAFT",
    )
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), editable=False)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-posting_date", "-created_at"]

    def __str__(self):
        return f"#{self.id} - {self.supplier_name} - {self.posting_date}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.only("status").get(pk=self.pk)
            if previous.status != "DRAFT" and self.status == previous.status:
                raise ValidationError(f"Cannot modify a {previous.status.lower()} purchase receipt.")
            if previous.status == "SUBMITTED" and self.status not in {"SUBMITTED", "CANCELLED"}:
                raise ValidationError("Submitted purchase receipts can only be cancelled.")
            if previous.status == "CANCELLED" and self.status != "CANCELLED":
                raise ValidationError("Cancelled purchase receipts cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk:
            _assert_document_is_draft(self, action="delete")
        return super().delete(*args, **kwargs)

    def clean(self):
        super().clean()
        from apps.settings.models import Restaurant

        restaurant = Restaurant.load()
        if (
            self.warehouse_id
            and restaurant
            and restaurant.store_warehouse_id
            and self.warehouse_id != restaurant.store_warehouse_id
        ):
            raise ValidationError({"warehouse": "Purchase Receipt warehouse must be the configured central Store."})


class PurchaseReceiptItem(BaseModel):
    """A single line item of a purchase receipt — warehouse is on the parent."""

    purchase_receipt = models.ForeignKey(
        PurchaseReceipt,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="purchase_receipt_items")
    uom = models.ForeignKey(UOM, on_delete=models.PROTECT)
    conversion_factor = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("1"))
    received_qty = models.DecimalField(max_digits=10, decimal_places=2)
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), editable=False)

    def __str__(self):
        return f"{self.item.item_code} x{self.received_qty}"

    def stock_qty(self):
        """Received quantity converted to the item's stock UOM."""
        qty = (Decimal(str(self.received_qty)) * Decimal(str(self.conversion_factor))).quantize(TWO_PLACES)
        if qty <= 0:
            raise ValidationError(f"Stock quantity for {self.item.item_name} must be greater than zero.")
        return qty

    def stock_unit_rate(self):
        """As-bought amount per stock UOM (2 dp)."""
        qty = self.stock_qty()
        amount = self.amount if self.amount else (Decimal(str(self.received_qty)) * Decimal(str(self.rate)))
        amount = money(amount)
        return money(amount / qty)

    def _derive_conversion_factor(self):
        if not self.item_id:
            return
        item = self.item
        if not self.uom_id:
            self.uom_id = item.stock_uom_id
        if self.uom_id == item.stock_uom_id:
            self.conversion_factor = Decimal("1")
            return
        row = ItemUOMConversion.objects.filter(item_id=item.pk, uom_id=self.uom_id).first()
        if row is None:
            raise ValidationError(f"{item.item_name} has no conversion for the selected unit.")
        self.conversion_factor = row.conversion_factor

    def save(self, *args, **kwargs):
        if self.purchase_receipt_id:
            _assert_document_is_draft(self.purchase_receipt, action="modify lines on")
        self._derive_conversion_factor()
        self.amount = money(Decimal(str(self.received_qty)) * Decimal(str(self.rate)))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.purchase_receipt_id:
            _assert_document_is_draft(self.purchase_receipt, action="delete lines from")
        return super().delete(*args, **kwargs)

    def validate_for_submission(self):
        if self.received_qty <= 0:
            raise ValidationError(f"Received quantity for {self.item.item_name} must be greater than zero.")
        if self.rate <= 0:
            raise ValidationError(f"Rate for {self.item.item_name} must be greater than zero.")
        if (
            self.item.disabled
            or self.item.has_variants
            or not self.item.is_stock_item
            or not self.item.is_purchase_item
        ):
            raise ValidationError(f"{self.item.item_name} is not an enabled stock and purchase item.")
        item = self.item
        if item.department == "DRINKS":
            if not (item.is_stock_item and item.is_sales_item and item.is_purchase_item):
                raise ValidationError(f"{item.item_name} must be a stock-tracked, sellable, purchasable drink.")
        elif item.department == "FOOD":
            if item.is_sales_item:
                raise ValidationError(f"{item.item_name} is a sellable food item and cannot be received into stock.")
            if not (item.is_stock_item and item.is_purchase_item):
                raise ValidationError(f"{item.item_name} must be a stock-tracked, purchasable food ingredient.")


class Recipe(BaseModel):
    """Ingredient card for one sellable FOOD item — quantities bake in the yield."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="recipes")
    output_qty = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1"))
    is_active = models.BooleanField(default=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["item__item_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["item"],
                condition=models.Q(is_active=True),
                name="inventory_recipe_one_active_per_item",
            ),
        ]

    def __str__(self):
        return f"Recipe for {self.item.item_name}"

    def clean(self):
        super().clean()
        if not self.item_id:
            return
        item = self.item
        if item.department != "FOOD" or not item.is_sales_item or item.has_variants:
            raise ValidationError({"item": "Recipes are only for sellable FOOD items."})
        if self.output_qty is not None and self.output_qty <= 0:
            raise ValidationError({"output_qty": "Output quantity must be greater than zero."})
        if (
            self.is_active
            and type(self).objects.filter(item_id=self.item_id, is_active=True).exclude(pk=self.pk).exists()
        ):
            raise ValidationError({"item": "This item already has an active recipe — deactivate it first."})


class RecipeItem(BaseModel):
    """One ingredient line on a recipe — qty is per recipe output, in ingredient stock UOM."""

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="items")
    ingredient = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="recipe_usages")
    qty = models.DecimalField(max_digits=10, decimal_places=4)

    class Meta:
        unique_together = ("recipe", "ingredient")

    def __str__(self):
        return f"{self.ingredient.item_name} x{self.qty}"

    def clean(self):
        super().clean()
        if not self.ingredient_id or not self.recipe_id:
            return
        ingredient = self.ingredient
        if (
            ingredient.disabled
            or ingredient.has_variants
            or ingredient.is_sales_item
            or not ingredient.is_stock_item
            or not ingredient.is_purchase_item
            or ingredient.department != "FOOD"
        ):
            raise ValidationError(
                {"ingredient": "Ingredients must be enabled, stock-tracked, purchasable, non-sellable FOOD items."}
            )
        if self.ingredient_id == self.recipe.item_id:
            raise ValidationError({"ingredient": "An item cannot be an ingredient of its own recipe."})
        if self.qty is not None and self.qty <= 0:
            raise ValidationError({"qty": "Quantity must be greater than zero."})
