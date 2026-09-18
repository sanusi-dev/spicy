from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.inventory.models import Item
from apps.payments.models import ModeOfPayment
from apps.utils.models import BaseModel
from apps.utils.rounding import cash_round, money

DRAFT = "DRAFT"
SUBMITTED = "SUBMITTED"
CANCELLED = "CANCELLED"
DISCARDED = "DISCARDED"
STATUS_CHOICES = [
    (DRAFT, "Draft"),
    (SUBMITTED, "Submitted"),
    (CANCELLED, "Cancelled"),
    (DISCARDED, "Discarded"),
]

DINE_IN = "DINE_IN"
TAKE_AWAY = "TAKE_AWAY"
ORDER_TYPE_CHOICES = [
    (DINE_IN, "Dine In"),
    (TAKE_AWAY, "Take Away"),
]

NEW_ORDER = "New Order"
KOT_CANCELLED = "Cancelled"
KOT_TYPE_CHOICES = [
    (NEW_ORDER, "New Order"),
    (KOT_CANCELLED, "Cancelled"),
]

TICKET_KITCHEN = "kitchen"
TICKET_BAR = "bar"
TICKET_TYPE_CHOICES = [
    (TICKET_KITCHEN, "Kitchen"),
    (TICKET_BAR, "Bar"),
]
KOT_PRINT_PENDING = "PENDING"
KOT_PRINTED = "PRINTED"
KOT_PRINT_CANCELLED = "CANCELLED"
KOT_PRINT_STATUS_CHOICES = [
    (KOT_PRINT_PENDING, "Pending"),
    (KOT_PRINTED, "Printed"),
    (KOT_PRINT_CANCELLED, "Cancelled"),
]

CANCEL_REASON_WRONG_ORDER = "wrong_order"
CANCEL_REASON_CUSTOMER_CHANGED_MIND = "customer_changed_mind"
CANCEL_REASON_CASHIER_ERROR = "cashier_error"
CANCEL_REASON_OTHER = "other"
CANCEL_REASON_CHOICES = [
    (CANCEL_REASON_WRONG_ORDER, "Wrong order"),
    (CANCEL_REASON_CUSTOMER_CHANGED_MIND, "Customer changed mind"),
    (CANCEL_REASON_CASHIER_ERROR, "Cashier error"),
    (CANCEL_REASON_OTHER, "Other"),
]


class OrderQuerySet(models.QuerySet):
    def open_drafts(self, shift):
        """Draft orders belonging to a shift."""
        return self.filter(status=DRAFT, is_return=False, opening_entry=shift)

    def open_drafts_for(self, shift, user):
        """Draft orders on the shift the user may work on: own drafts, or all for managers."""
        drafts = self.open_drafts(shift)
        if user.is_manager or user.is_admin:
            return drafts
        return drafts.filter(Q(created_by=user) | Q(created_by__isnull=True))

    def submitted_in_shift(self, shift, period_start, period_end):
        """Submitted non-return orders settled within the period."""
        return self.filter(
            opening_entry=shift,
            status=SUBMITTED,
            is_return=False,
            submitted_at__gte=period_start,
            submitted_at__lte=period_end,
        )


class OrderSequence(BaseModel):
    """Persistent counter for sequential order numbers, one row per series."""

    name = models.CharField(max_length=50, unique=True)
    current_value = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.name} → {self.current_value}"


class Order(BaseModel):
    """A POS order — the single source of truth for items, payments, and status."""

    objects = OrderQuerySet.as_manager()

    invoice_number = models.CharField(max_length=50, unique=True, null=True, blank=True, editable=False)
    order_number = models.PositiveIntegerField(db_index=True, null=True, blank=True, editable=False)
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, default=DINE_IN)
    customer_name = models.CharField(max_length=200, default="Walk-in Customer")
    guest_count = models.PositiveIntegerField(default=1)
    cashier = models.ForeignKey(
        "users.CustomUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="settled_orders"
    )
    created_by = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="created_orders",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    is_paid = models.BooleanField(default=False)
    invoice_printed = models.BooleanField(default=False)
    posting_date = models.DateField(default=timezone.localdate)
    posting_time = models.TimeField(default=timezone.localtime)
    net_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    rounded_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    rounding_adjustment = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"), editable=False)
    change_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    cancel_reason = models.CharField(max_length=50, choices=CANCEL_REASON_CHOICES, blank=True)
    cancel_reason_note = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_orders",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    discarded_by = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="discarded_orders",
    )
    discarded_at = models.DateTimeField(null=True, blank=True)
    opening_entry = models.ForeignKey(
        "staff.POSOpeningEntry", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    stock_warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="order_stock_snapshots",
    )
    arrived_time = models.DateTimeField(null=True, blank=True, editable=False)
    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
    invoice_printed_at = models.DateTimeField(null=True, blank=True, editable=False)
    invoice_printed_by = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="printed_orders",
    )
    is_return = models.BooleanField(default=False, editable=False)
    return_against = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="return_orders"
    )

    class Meta:
        ordering = ["-posting_date", "-posting_time"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["posting_date"]),
            models.Index(fields=["status", "updated_at"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(guest_count__gte=1), name="orders_guest_count_gte_one"),
            models.CheckConstraint(
                condition=~Q(status=CANCELLED) | ~Q(cancel_reason=""),
                name="orders_cancelled_has_reason",
            ),
            models.UniqueConstraint(
                fields=["order_number"],
                condition=Q(order_number__isnull=False),
                name="orders_order_number_unique",
            ),
        ]

    def __str__(self):
        return f"{self.invoice_number or f'#{self.pk}'} — {self.customer_name}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = (
                type(self)
                .objects.only(
                    "status",
                    "is_return",
                    "return_against_id",
                    "stock_warehouse_id",
                    "invoice_printed",
                    "order_type",
                    "customer_name",
                    "guest_count",
                )
                .get(pk=self.pk)
            )
            allow_cancellation = getattr(self, "_allow_cancellation", False)
            allow_submit = getattr(self, "_allow_submit", False)
            allow_discard = getattr(self, "_allow_discard", False)

            if previous.status in {SUBMITTED, CANCELLED, DISCARDED} and not allow_cancellation:
                raise ValidationError("Submitted, cancelled or discarded orders cannot be modified.")
            if previous.status == DRAFT and self.status == SUBMITTED and not allow_submit:
                raise ValidationError("Only settlement can submit an order.")
            if previous.status == DRAFT and self.status == CANCELLED and not allow_cancellation:
                raise ValidationError("Use the order cancellation flow to cancel an order.")
            if previous.status == DRAFT and self.status == DISCARDED and not allow_discard:
                raise ValidationError("Use the order discard flow to discard an order.")
            if (self.is_return, self.return_against_id) != (previous.is_return, previous.return_against_id):  # type: ignore
                raise ValidationError("An order's return status and source cannot be changed.")
            if previous.stock_warehouse_id and self.stock_warehouse_id != previous.stock_warehouse_id:  # type: ignore
                raise ValidationError("The stock warehouse snapshot cannot be changed.")
            if previous.invoice_printed and not self.invoice_printed:
                raise ValidationError("A printed receipt cannot be marked as unprinted.")
            if previous.status == DRAFT and not allow_cancellation:
                draft_fields = ("order_type", "customer_name", "guest_count")
                if (
                    any(getattr(self, field) != getattr(previous, field) for field in draft_fields)
                    and KOT.objects.filter(order=self).exists()
                ):
                    raise ValidationError("This order was sent to the kitchen or bar. Cancel it before making changes.")
        is_new = self._state.adding
        if is_new and not self.arrived_time:
            self.arrived_time = timezone.now()
        super().save(*args, **kwargs)
        if is_new and not self.invoice_number:
            from apps.settings.models import Restaurant

            settings = Restaurant.load()
            prefix = settings.invoice_series_prefix if settings else "REST-"
            self.invoice_number = f"{prefix}{self.pk}"
            super().save(update_fields=["invoice_number"])

    def clean(self):
        super().clean()
        if self.status == CANCELLED and not self.cancel_reason:
            raise ValidationError({"cancel_reason": "A cancel reason is required."})
        if self.is_return:
            if not self.return_against_id:
                raise ValidationError({"return_against": "A return order must reference the original order."})
            if self.return_against.is_return:
                raise ValidationError({"return_against": "Cannot return against another return order."})
            if self.return_against.status != SUBMITTED:
                raise ValidationError({"return_against": "A return must reference a submitted order."})

    def delete(self, *args, **kwargs):
        raise ValidationError("Orders cannot be hard-deleted; unsent drafts are abandoned as tombstones instead.")

    def assign_order_number(self):
        """Assign the next sequential order number atomically."""
        if self.order_number is not None:
            return self.order_number
        if self.status != DRAFT:
            raise ValidationError("Only draft orders can receive an order number.")
        with transaction.atomic():
            sequence, _created = OrderSequence.objects.get_or_create(
                name="order",
                defaults={"current_value": 0},
            )
            seq = OrderSequence.objects.select_for_update().get(pk=sequence.pk)
            if _created:
                # A fresh counter must continue from existing orders (e.g. a
                # new database after a restore) instead of starting at 1.
                seq.current_value = (
                    type(self).objects.aggregate(max_number=models.Max("order_number"))["max_number"] or 0
                )
            seq.current_value += 1
            seq.save(update_fields=["current_value"])
            self.order_number = seq.current_value
            super().save(update_fields=["order_number"])
        return self.order_number

    def recalculate_totals(self):
        """Recalculate net_total from items and grand total (no tax)."""
        total = self.items.aggregate(t=Sum("amount"))["t"] or Decimal("0")
        self.net_total = total
        self.grand_total = self.net_total
        self.rounded_total = cash_round(self.grand_total)
        self.rounding_adjustment = self.rounded_total - self.grand_total
        self.save(
            update_fields=[
                "net_total",
                "grand_total",
                "rounded_total",
                "rounding_adjustment",
                "updated_at",
            ]
        )

    def _ensure_editable(self):
        """Reject edits after a kitchen or bar ticket has been created."""
        persisted = type(self).objects.only("status").get(pk=self.pk) if self.pk else self
        if persisted.status != DRAFT:
            raise ValidationError("Cannot modify a submitted or cancelled order.")
        if self.pk and KOT.objects.filter(order=self).exists():
            raise ValidationError("This order was sent to the kitchen or bar. Cancel it before making changes.")

    def can_be_accessed_by(self, user) -> bool:
        """Return True if the user may open or change this draft order."""
        if user.is_manager or user.is_admin:
            return True
        return self.created_by_id is None or self.created_by_id == user.pk

    def _validate_pos_item(self, item):
        if item.disabled or not item.is_sales_item:
            raise ValidationError("That menu item is no longer available.")
        if item.department == "DRINKS":
            if not (item.is_stock_item and item.is_sales_item and item.is_purchase_item):
                raise ValidationError(f"{item.item_name} must be a stock-tracked, sellable, purchasable drink.")
        elif item.department == "FOOD" and item.is_sales_item and (item.is_stock_item or item.is_purchase_item):
            raise ValidationError(
                f"{item.item_name} is a sellable food item and must not be stock-tracked or purchasable."
            )

    def _validate_order_line_availability(self, line):
        """Reject lines whose Item or MenuItem is no longer sellable."""
        item = line.item
        if item.disabled or not item.is_sales_item:
            raise ValidationError(f"{line.item_name} is no longer available.")
        if item.department == "DRINKS":
            if not (item.is_stock_item and item.is_sales_item and item.is_purchase_item):
                raise ValidationError(f"{line.item_name} must be a stock-tracked, sellable, purchasable drink.")
        elif item.department == "FOOD" and item.is_sales_item and (item.is_stock_item or item.is_purchase_item):
            raise ValidationError(
                f"{line.item_name} is a sellable food item and must not be stock-tracked or purchasable."
            )
        menu_item = line.menu_item
        if menu_item is not None and menu_item.disabled:
            raise ValidationError(f"{line.item_name} is no longer available on the active menu.")

    def _validate_current_lines(self):
        """Re-check every line before quantity changes or settlement."""
        for line in self.items.select_related("item", "menu_item").all():
            self._validate_order_line_availability(line)

    def change_guest_count(self, new_count):
        """Set the guest count. Cannot drop below a guest who still has items — that
        would leave orphaned rows tagged to a hidden customer slot and skew per-customer
        analytics. Submits/cancellations stay untouched."""
        self._ensure_editable()
        if new_count < 1 or new_count > 50:
            raise ValidationError("Guest count must be between 1 and 50.")
        max_index = self.items.aggregate(m=models.Max("customer_index"))["m"] or 1
        if new_count < max_index:
            raise ValidationError(f"Remove Customer {max_index}'s items before lowering the guest count.")
        self.guest_count = new_count
        self.save(update_fields=["guest_count", "updated_at"])

    def audit(self, event_type, actor=None, metadata=None):
        """Append an immutable order audit event."""
        return OrderAuditEvent.objects.create(
            order=self,
            event_type=event_type,
            actor=actor,
            metadata=metadata or {},
        )


class OrderItem(BaseModel):
    """A single line item in an order."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="items")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="order_items")
    item_name = models.CharField(max_length=200)
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), editable=False)
    department = models.CharField(
        max_length=10,
        choices=[("FOOD", "Food"), ("DRINKS", "Drinks")],
        null=True,
        blank=True,
    )
    stock_item = models.BooleanField(null=True, blank=True, editable=False)
    customer_index = models.PositiveIntegerField(default=1)
    comments = models.CharField(max_length=200, blank=True)
    menu_item = models.ForeignKey("menu.MenuItem", on_delete=models.SET_NULL, null=True, blank=True)
    return_against_item = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="return_items",
    )
    not_restockable = models.BooleanField(
        default=False,
        help_text="For returns only: when set, the returned items are treated as wastage and not put back into stock.",
    )

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(qty__gt=0) | Q(return_against_item__isnull=False),
                name="orders_item_qty_valid",
            ),
            models.CheckConstraint(condition=Q(rate__gte=0), name="orders_item_rate_gte_zero"),
            models.CheckConstraint(condition=Q(customer_index__gte=1), name="orders_item_customer_gte_one"),
            models.CheckConstraint(
                condition=Q(department__in=("FOOD", "DRINKS")),
                name="orders_item_department_valid",
            ),
            models.CheckConstraint(
                condition=~Q(not_restockable=True) | Q(return_against_item__isnull=False),
                name="orders_item_not_restockable_return_only",
            ),
        ]

    def __str__(self):
        return f"{self.item_name}"

    def _validate_return_line(self, order):
        if not order.is_return:
            if self.return_against_item_id:
                raise ValidationError("Only return lines may reference an original order item.")
            return
        if not self.return_against_item_id:
            raise ValidationError("A return line must reference an original order item.")
        if self.qty >= 0:
            raise ValidationError("Return quantities must be negative.")

        source_item = type(self).objects.select_related("order").filter(pk=self.return_against_item_id).first()
        if source_item is None or source_item.order_id != order.return_against_id:
            raise ValidationError("Return line must reference an item from the original order.")
        if source_item.qty <= 0:
            raise ValidationError("Only positive quantities from the original order can be returned.")

        returned_query = (
            type(self)
            .objects.filter(
                return_against_item_id=source_item.pk,
                order__is_return=True,
            )
            .exclude(order__status=CANCELLED)
        )
        if self.pk:
            returned_query = returned_query.exclude(pk=self.pk)
        returned_total = returned_query.aggregate(total=Sum("qty"))["total"] or Decimal("0")
        if abs(returned_total) + abs(self.qty) > source_item.qty:
            raise ValidationError("Return quantity cannot exceed the quantity sold on the original order.")

    def clean(self):
        super().clean()
        if self.order_id:
            order = Order.objects.get(pk=self.order_id)
            self._validate_return_line(order)
            if self.not_restockable and not (order.is_return and order.status == DRAFT):
                raise ValidationError({"not_restockable": "Only return drafts can mark lines as not restockable."})

    def save(self, *args, **kwargs):
        if self.order_id:
            order = Order.objects.get(pk=self.order_id)
            order._ensure_editable()
            self._validate_return_line(order)
            if self.not_restockable and not (order.is_return and order.status == DRAFT):
                raise ValidationError("Only return drafts can mark lines as not restockable.")
        if not self.item_name and self.item_id:
            self.item_name = self.item.item_name
        if self.item_id and not self.department:
            # Snapshot the item's department and stock flag at order time so
            # later edits to the Item can't rewrite historical lines.
            self.department = self.item.department
        if self.item_id and self.stock_item is None:
            self.stock_item = self.item.is_stock_item
        try:
            qty = Decimal(str(self.qty))
            rate = Decimal(str(self.rate))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ValidationError("Quantity and rate must be valid decimals.") from exc
        if not qty.is_finite() or not rate.is_finite() or rate < 0:
            raise ValidationError("Quantity and rate must be finite; rate cannot be negative.")
        if self.order.is_return:
            if qty >= 0:
                raise ValidationError("Return quantities must be negative.")
        elif qty <= 0:
            raise ValidationError("Quantity must be greater than zero.")
        self.qty = qty
        self.rate = rate
        self.amount = money(self.qty * self.rate)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.order_id:
            order = Order.objects.get(pk=self.order_id)
            order._ensure_editable()
        return super().delete(*args, **kwargs)


class OrderPayment(BaseModel):
    """A payment line within an order."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    mode_of_payment = models.ForeignKey(ModeOfPayment, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference_no = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["mode_of_payment", "reference_no"],
                condition=~Q(reference_no=""),
                name="orders_payment_reference_unique",
            ),
        ]

    def __str__(self):
        return f"{self.mode_of_payment.name}: {self.amount}"

    def save(self, *args, **kwargs):
        try:
            amount = Decimal(str(self.amount))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ValidationError("Payment amount must be a valid decimal.") from exc
        if not amount.is_finite() or amount != money(amount):
            raise ValidationError("Payment amount must be finite and valid to 2 decimal places.")
        order = self.order if self.order_id else None
        if order is None:
            order = Order.objects.only("status", "is_return").get(pk=self.order_id)
        if not order.is_return and amount <= 0:
            raise ValidationError("Payment amount must be greater than zero.")
        self.amount = amount
        self.reference_no = (self.reference_no or "").strip()
        if self.mode_of_payment_id:
            mode = (
                self.mode_of_payment
                if hasattr(self, "mode_of_payment")
                else ModeOfPayment.objects.get(pk=self.mode_of_payment_id)
            )
            if mode.type != ModeOfPayment.TYPE_CASH:
                if not self.reference_no and not order.is_return:
                    from apps.settings.models import Restaurant

                    if Restaurant.requires_payment_reference():
                        raise ValidationError(f"A reference is required for {mode.name} payments.")
                if self.reference_no:
                    duplicate = (
                        OrderPayment.objects.filter(
                            mode_of_payment_id=self.mode_of_payment_id,
                            reference_no=self.reference_no,
                        )
                        .exclude(pk=self.pk)
                        .exists()
                    )
                    if duplicate:
                        raise ValidationError("This electronic payment reference has already been used.")
        order = self.order if self.order_id else None
        if not order or not getattr(order, "_settling", False):
            # Outside the settlement flow, payments are immutable once the
            # order leaves draft or a KOT has been created.
            order = Order.objects.only("status", "is_return").get(pk=self.order_id)
            if order.status != DRAFT:
                raise ValidationError("Payments on submitted or cancelled orders cannot be modified.")
            if KOT.objects.filter(order=order).exists():
                raise ValidationError("Payments cannot be edited after a KOT has been created.")
        try:
            super().save(*args, **kwargs)
        except IntegrityError as exc:
            raise ValidationError("This electronic payment reference has already been used.") from exc

    def delete(self, *args, **kwargs):
        order = Order.objects.only("status").get(pk=self.order_id)  # type: ignore
        if order.status != DRAFT:
            raise ValidationError("Payments on submitted or cancelled orders cannot be deleted.")
        if KOT.objects.filter(order=order).exists():
            raise ValidationError("Payments cannot be deleted after a KOT has been created.")
        return super().delete(*args, **kwargs)


class KOT(BaseModel):
    """Kitchen Order Ticket — immutable once generated."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="kots")
    production_unit = models.ForeignKey("settings.ProductionUnit", on_delete=models.PROTECT, related_name="kots")
    type = models.CharField(max_length=25, choices=KOT_TYPE_CHOICES)
    kot_number = models.CharField(max_length=50, unique=True)
    ticket_type = models.CharField(max_length=10, choices=TICKET_TYPE_CHOICES, default=TICKET_KITCHEN)
    status = models.CharField(
        max_length=15, choices=[(SUBMITTED, "Submitted"), (CANCELLED, "Cancelled")], default=SUBMITTED
    )
    print_status = models.CharField(max_length=15, choices=KOT_PRINT_STATUS_CHOICES, default=KOT_PRINT_PENDING)
    created_by = models.ForeignKey(
        "users.CustomUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="created_kots"
    )
    posting_datetime = models.DateTimeField(auto_now_add=True)
    order_number = models.PositiveIntegerField(null=True, blank=True)
    original_kots = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(
        "users.CustomUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="cancelled_kots"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-posting_datetime"]
        indexes = [models.Index(fields=["status", "print_status", "-posting_datetime"])]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            immutable_fields = (
                "order_id",
                "production_unit_id",
                "type",
                "kot_number",
                "ticket_type",
                "created_by_id",
                "order_number",
                "original_kots",
            )
            if any(getattr(self, field) != getattr(previous, field) for field in immutable_fields):
                raise ValidationError("KOT snapshots cannot be edited after creation.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.kot_number} — {self.type}"


class KOTItem(BaseModel):
    """An item line on a KOT."""

    kot = models.ForeignKey(KOT, on_delete=models.PROTECT, related_name="items")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    item_name = models.CharField(max_length=200)
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    cancelled_qty = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    comments = models.CharField(max_length=200, blank=True)
    customer_index = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(qty__gte=0), name="orders_kot_item_qty_gte_zero"),
            models.CheckConstraint(condition=Q(cancelled_qty__gte=0), name="orders_kot_cancelled_qty_gte_zero"),
            models.CheckConstraint(condition=Q(customer_index__gte=1), name="orders_kot_customer_gte_one"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            immutable_fields = (
                "kot_id",
                "item_id",
                "item_name",
                "qty",
                "cancelled_qty",
                "comments",
                "customer_index",
            )
            if any(getattr(self, field) != getattr(previous, field) for field in immutable_fields):
                raise ValidationError("KOT item snapshots cannot be edited after creation.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("KOT item snapshots cannot be deleted after creation.")

    def __str__(self):
        return f"{self.item_name} x{self.qty}"


class OrderAuditEvent(BaseModel):
    """Immutable audit event for an order lifecycle or mutation."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="audit_events")
    event_type = models.CharField(max_length=50)
    actor = models.ForeignKey(
        "users.CustomUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_audit_events"
    )
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ["created_at", "pk"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Order audit events cannot be edited.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Order audit events cannot be deleted.")
