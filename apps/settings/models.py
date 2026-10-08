from django.core.exceptions import ValidationError
from django.db import models

from apps.utils.models import BaseModel


class Restaurant(BaseModel):
    """The single settings record for this installation — identity, menu, stock, and POS behaviour."""

    company = models.CharField(max_length=200)
    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    invoice_series_prefix = models.CharField(max_length=20, default="REST-")
    address = models.TextField(blank=True)
    active_menu = models.ForeignKey(
        "menu.Menu",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_for_restaurants",
    )
    default_warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_restaurants",
        verbose_name="Bar / POS sales warehouse",
        help_text="The warehouse that holds bar stock and supplies drink sales.",
    )
    store_warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="store_for_restaurants",
        verbose_name="Central Store warehouse",
        help_text="The main store where all goods are received before moving to the kitchen or bar.",
    )
    max_open_drafts = models.PositiveIntegerField(
        default=50,
        help_text="The maximum number of open orders allowed on a shift at one time.",
    )
    pos_allow_full_history = models.BooleanField(
        default=False,
        help_text="When enabled, cashiers can use All/Returns/Cancelled history filters. Managers always can.",
    )
    require_payment_reference = models.BooleanField(
        default=False,
        verbose_name="Require payment reference",
        help_text="When enabled, electronic (non-cash) payments must include a reference number at settlement.",
    )

    default_income_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Default income account",
        help_text="The income account used for sales when no category or kitchen/bar account is set.",
    )
    default_sales_returns_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Default sales returns account",
        help_text="The account used to reduce income on refunds when no kitchen/bar account is set.",
    )
    default_expense_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Default expense account",
        help_text="The expense account used to record the cost of drinks sold when no category account is set.",
    )
    round_off_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Round-off account",
    )
    account_for_change_amount = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Account for change amount",
        help_text="The cash account used to record change given back to customers.",
    )
    wastage_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Wastage account",
        help_text="The expense account used when returned drinks cannot be put back into stock.",
    )
    cash_shortage_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Cash shortage account",
    )
    cash_over_short_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Cash over/short account",
    )
    variance_approval_threshold = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Variance approval threshold",
        help_text=(
            "Cash difference that requires a manager note before a shift can be closed."
            " Leave blank to allow any variance without a note."
        ),
    )

    default_payable_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Default payable account",
        help_text="The account used to track amounts owed to suppliers.",
    )
    default_supplier_expense_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Default supplier expense account",
        help_text="The expense account used for non-stock costs on supplier invoices.",
    )

    stock_received_but_not_billed_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Stock received but not billed (GRNI)",
        help_text="The account that holds the value of goods received before the supplier invoice arrives.",
    )
    inventory_price_variance_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Inventory price variance account",
        help_text="The expense account used when cancelled receipts leave a small difference in stock value.",
    )
    stock_adjustment_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Stock adjustment account",
        help_text="The expense account used for stock adjustments (count corrections up or down).",
    )
    temporary_opening_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Temporary opening account",
        help_text="The equity account credited when opening stock is first seeded into a fresh warehouse.",
    )
    petty_cash_expense_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Petty cash expense account",
        help_text="The expense account used for mid-shift cash-outs (transport, ice, petty repairs).",
    )

    class Meta:
        ordering = ["company"]

    def __str__(self):
        return self.company

    @classmethod
    def load(cls):
        """Return the singleton settings record with its direct relations loaded, or None."""
        return cls.objects.select_related("active_menu", "default_warehouse", "store_warehouse").order_by("pk").first()

    @classmethod
    def requires_payment_reference(cls) -> bool:
        """Return whether non-cash payments must carry a reference."""
        return bool(
            cls.objects.only("require_payment_reference").values_list("require_payment_reference", flat=True).first()
        )

    def clean(self):
        super().clean()
        if not self.pk and Restaurant.objects.exists():
            raise ValidationError("Restaurant settings already exist — edit the existing record.")
        if self.default_income_account_id:
            from apps.payments.models import PaymentGLMapping

            if PaymentGLMapping.objects.filter(default_account_id=self.default_income_account_id).exists():
                raise ValidationError(
                    {
                        "default_income_account": "This account is mapped to a payment mode "
                        "and cannot record sales income.",
                    }
                )
        if self.default_sales_returns_account_id:
            from apps.payments.models import PaymentGLMapping

            if PaymentGLMapping.objects.filter(default_account_id=self.default_sales_returns_account_id).exists():
                raise ValidationError(
                    {
                        "default_sales_returns_account": "This account is mapped to a payment mode "
                        "and cannot record sales returns.",
                    }
                )
        if self.max_open_drafts < 1:
            raise ValidationError({"max_open_drafts": "The open-draft limit must be at least 1."})
        if self.default_warehouse_id and self.default_warehouse.disabled:
            raise ValidationError({"default_warehouse": "The Bar / POS sales warehouse must be enabled."})
        if self.store_warehouse_id and self.store_warehouse.disabled:
            raise ValidationError({"store_warehouse": "The central Store warehouse must be enabled."})
        if self.store_warehouse_id and self.store_warehouse_id == self.default_warehouse_id:
            raise ValidationError({"store_warehouse": "The central Store must differ from the Bar / POS warehouse."})

        # Drafts must not silently move to a different stock location when the warehouse changes.
        if self.pk:
            previous = Restaurant.objects.only("default_warehouse_id", "store_warehouse_id").get(pk=self.pk)
            if previous.default_warehouse_id != self.default_warehouse_id:
                from apps.orders.models import DRAFT, Order

                if Order.objects.filter(
                    status=DRAFT,
                    is_return=False,
                    stock_warehouse__isnull=False,
                ).exists():
                    raise ValidationError(
                        {"default_warehouse": "Clear or cancel open POS orders with drink reservations first."}
                    )
            if previous.store_warehouse_id != self.store_warehouse_id:
                from apps.inventory.models import PurchaseReceipt, StockEntry

                if (
                    StockEntry.objects.filter(status="DRAFT").exists()
                    or PurchaseReceipt.objects.filter(status="DRAFT").exists()
                ):
                    raise ValidationError(
                        {"store_warehouse": "Submit or remove draft stock documents before changing the central Store."}
                    )

        # Store receives stock, Kitchen consumes FOOD, Bar/POS supplies DRINKS — warehouses must stay compatible.
        units = ProductionUnit.objects.select_related("warehouse").all()
        drinks_unit = next((unit for unit in units if unit.department == ProductionUnit.DRINKS), None)
        food_unit = next((unit for unit in units if unit.department == ProductionUnit.FOOD), None)
        if self.default_warehouse_id and drinks_unit and drinks_unit.warehouse_id != self.default_warehouse_id:
            raise ValidationError(
                {"default_warehouse": "The Bar / POS warehouse must match the Drinks production unit warehouse."}
            )
        if food_unit:
            if self.store_warehouse_id and food_unit.warehouse_id == self.store_warehouse_id:
                raise ValidationError({"store_warehouse": "The central Store must differ from the Kitchen warehouse."})
            if self.default_warehouse_id and food_unit.warehouse_id == self.default_warehouse_id:
                raise ValidationError({"default_warehouse": "The Bar / POS warehouse must differ from the Kitchen."})


class ProductionUnit(BaseModel):
    """A station that produces items — kitchen or bar — with printer routing."""

    FOOD = "FOOD"
    DRINKS = "DRINKS"
    DEPARTMENT_CHOICES = [(FOOD, "Food"), (DRINKS, "Drinks")]

    WIDTH_58MM = "WIDTH_58MM"
    WIDTH_80MM = "WIDTH_80MM"
    PAPER_WIDTH_CHOICES = [
        (WIDTH_58MM, "58mm"),
        (WIDTH_80MM, "80mm"),
    ]

    FULL_CUT = "FULL_CUT"
    PARTIAL_CUT = "PARTIAL_CUT"
    NO_CUT = "NO_CUT"
    CUT_MODE_CHOICES = [
        (FULL_CUT, "Full Cut"),
        (PARTIAL_CUT, "Partial Cut"),
        (NO_CUT, "No Cut"),
    ]

    name = models.CharField(max_length=100, unique=True)
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="production_units")
    department = models.CharField(max_length=10, choices=DEPARTMENT_CHOICES)
    block_takeaway_kot = models.BooleanField(default=False)
    printer_ip = models.CharField(max_length=50, blank=True)
    printer_paper_width = models.CharField(max_length=10, choices=PAPER_WIDTH_CHOICES, default=WIDTH_80MM)
    printer_cut_mode = models.CharField(max_length=15, choices=CUT_MODE_CHOICES, default=FULL_CUT)
    income_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Income account",
        help_text="The income account used for sales from this station.",
    )
    sales_returns_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Sales returns account",
        help_text="The account debited when a sale from this station is refunded.",
    )
    expense_account = models.ForeignKey(
        "accounting.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Expense account",
        help_text="The expense account used for cost of goods sold from this station.",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["department"], name="settings_one_production_unit_per_department"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.warehouse_id and self.warehouse.disabled:
            raise ValidationError({"warehouse": "The production unit warehouse must be enabled."})
        if self.income_account_id:
            from apps.payments.models import PaymentGLMapping

            if PaymentGLMapping.objects.filter(default_account_id=self.income_account_id).exists():
                raise ValidationError(
                    {"income_account": "This account is mapped to a payment mode and cannot record sales income."}
                )
        if self.sales_returns_account_id:
            from apps.payments.models import PaymentGLMapping

            if PaymentGLMapping.objects.filter(default_account_id=self.sales_returns_account_id).exists():
                raise ValidationError(
                    {
                        "sales_returns_account": "This account is mapped to a payment mode "
                        "and cannot record sales returns."
                    }
                )

        restaurant = Restaurant.load()
        if not restaurant or not self.warehouse_id:
            return
        if (
            self.department == self.DRINKS
            and restaurant.default_warehouse_id
            and self.warehouse_id != restaurant.default_warehouse_id
        ):
            raise ValidationError({"warehouse": "Drinks must use the configured Bar / POS sales warehouse."})
        if self.department == self.FOOD:
            if restaurant.store_warehouse_id and self.warehouse_id == restaurant.store_warehouse_id:
                raise ValidationError({"warehouse": "The Kitchen warehouse must differ from the central Store."})
            if restaurant.default_warehouse_id and self.warehouse_id == restaurant.default_warehouse_id:
                raise ValidationError({"warehouse": "The Kitchen warehouse must differ from the Bar / POS warehouse."})
