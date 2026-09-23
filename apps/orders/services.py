"""Order workflows — drafts, lines, settlement, returns, tickets, and drink reservations."""

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils import timezone

from apps.inventory.models import Bin, Item, StockLedgerEntry
from apps.menu.models import MenuItem
from apps.payments.models import ModeOfPayment, PaymentGLMapping
from apps.utils.rounding import money

from . import printing
from .models import (
    CANCEL_REASON_CHOICES,
    CANCEL_REASON_OTHER,
    CANCELLED,
    DINE_IN,
    DISCARDED,
    DRAFT,
    KOT,
    KOT_CANCELLED,
    KOT_PRINT_PENDING,
    KOT_PRINTED,
    NEW_ORDER,
    ORDER_TYPE_CHOICES,
    SUBMITTED,
    TAKE_AWAY,
    TICKET_BAR,
    TICKET_KITCHEN,
    KOTItem,
    Order,
    OrderItem,
    OrderPayment,
)


@transaction.atomic
def create_draft_order(shift, user, *, order_type=DINE_IN, guest_count=1):
    """Create a draft order on the shift, enforcing the open-draft cap."""
    from apps.settings.models import Restaurant
    from apps.staff.models import POSOpeningEntry

    settings = Restaurant.objects.select_for_update().first()
    if settings is None:
        raise ValidationError("Restaurant settings are not configured.")
    # Lock the shift row so concurrent creations serialize on the draft cap.
    locked_shift = (
        POSOpeningEntry.objects.select_for_update()
        .filter(pk=shift.pk, status=POSOpeningEntry.SUBMITTED, closing_entry__isnull=True)
        .first()
    )

    if locked_shift is None:
        raise ValidationError("Open a shift before taking orders.")
    draft_count = Order.objects.open_drafts(locked_shift).count()

    if draft_count >= settings.max_open_drafts:
        raise ValidationError(f"The active shift already has {settings.max_open_drafts} open drafts.")
    order = Order.objects.create(
        order_type=order_type,
        guest_count=guest_count,
        opening_entry=locked_shift,
        created_by=user,
    )
    order.assign_order_number()
    order.audit("CREATED", actor=user, metadata={"order_type": order_type})
    return order


@transaction.atomic
def update_order_meta(order, *, order_type=None, guest_delta=None, guest_count=None, actor=None):
    """Apply order-type or guest-count edits to a draft order; return the effective guest count."""
    if order_type is None and guest_delta is None and guest_count is None:
        return order.guest_count
    order._ensure_editable()
    if order_type and order_type not in {c[0] for c in ORDER_TYPE_CHOICES}:
        raise ValidationError("Choose a valid order type.")
    if order_type:
        order.order_type = order_type
        order.save(update_fields=["order_type", "updated_at"])
        order.audit("ORDER_TYPE_CHANGED", actor=actor, metadata={"order_type": order_type})
    if guest_delta is not None:
        try:
            guest_count = order.guest_count + int(guest_delta)
        except ValueError, TypeError:
            raise ValidationError("Guest change must be a valid number.") from None
    if guest_count is None:
        return order.guest_count
    try:
        guest_count = int(guest_count)
    except ValueError, TypeError:
        raise ValidationError("Guest count must be a valid number.") from None
    guest_count = max(1, min(50, guest_count))
    if guest_count != order.guest_count:
        order.change_guest_count(guest_count)
        order.audit("GUEST_COUNT_CHANGED", actor=actor, metadata={"guest_count": guest_count})
    return guest_count


@transaction.atomic
def update_order_item(order, order_item_pk, *, action="update", qty=None, actor=None):
    """Apply a POS quantity action to a draft line: remove, increment, decrement, or set qty."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    locked._ensure_editable()
    if action == "remove":
        removed = locked.items.filter(pk=order_item_pk).values("item_id", "qty").first()
        remove_order_line(locked, order_item_pk)
        if removed:
            locked.audit(
                "ITEM_REMOVED",
                actor=actor,
                metadata={"item_id": removed["item_id"], "quantity": str(removed["qty"])},
            )
    elif action in {"increment", "decrement"}:
        oi = locked.items.filter(pk=order_item_pk).first()
        if oi is None:
            raise ValidationError("That order line no longer exists.")
        new_qty = oi.qty + (Decimal("1") if action == "increment" else Decimal("-1"))
        update_order_line_quantity(locked, order_item_pk, new_qty)
        locked.audit(
            "ITEM_QUANTITY_CHANGED",
            actor=actor,
            metadata={"item_id": order_item_pk, "quantity": str(new_qty)},
        )
    else:
        try:
            qty = Decimal(str(qty))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ValidationError("Invalid item update.") from exc
        if qty <= 0:
            remove_order_line(locked, order_item_pk)
        else:
            oi = locked.items.filter(pk=order_item_pk).first()
            if oi is not None:
                update_order_line_quantity(locked, order_item_pk, qty)
                locked.audit(
                    "ITEM_QUANTITY_CHANGED",
                    actor=actor,
                    metadata={"item_id": order_item_pk, "quantity": str(qty)},
                )
    locked.recalculate_totals()
    order.refresh_from_db()


@transaction.atomic
def settle_order(order, payments_data, cashier=None, opening_entry=None):
    """Process a normal POS payment and submit the order atomically."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status != DRAFT:
        raise ValidationError("Order is already settled or cancelled.")
    if locked.is_return:
        raise ValidationError("Return orders must use the deferred refund flow.")
    if cashier is not None and not locked.can_be_accessed_by(cashier):
        raise ValidationError("Only the cashier who created this order, or a manager, can settle it.")
    if not locked.items.exists():
        raise ValidationError("Cannot settle an order with no items.")
    locked._validate_current_lines()
    from apps.staff.models import POSOpeningEntry

    active_shift = (
        POSOpeningEntry.objects.select_for_update()
        .filter(pk=locked.opening_entry_id, status=POSOpeningEntry.SUBMITTED, closing_entry__isnull=True)
        .first()
    )
    if active_shift is None:
        raise ValidationError("An active shift is required before settlement.")
    if opening_entry is not None and opening_entry.pk != active_shift.pk:
        raise ValidationError("This order does not belong to the active shift.")
    if cashier:
        locked.cashier = cashier
    locked.recalculate_totals()
    locked.grand_total = locked.rounded_total
    reservations_initialized = _reservations_initialized(locked)
    _snapshot_stock_warehouse(locked)
    _locked_drink_stock(locked, reservations_initialized=reservations_initialized)
    payment_rows = _validate_payment_data(locked, payments_data, active_shift)
    total_paid = sum((row["amount"] for row in payment_rows), Decimal("0"))
    if total_paid < locked.grand_total:
        raise ValidationError("Payment must cover the full total.")
    if total_paid > locked.grand_total and any(row["mode"].type != ModeOfPayment.TYPE_CASH for row in payment_rows):
        raise ValidationError("Only cash payments may include change.")
    if locked.payments.exists():
        raise ValidationError("This draft already has payment rows and requires manager review.")

    if locked.order_number is None:
        locked.assign_order_number()
    if not locked.kots.exists():
        planned_tickets, missing_departments = _plan_tickets(locked)
        if missing_departments:
            labels = ", ".join("Food" if department == "FOOD" else "Drinks" for department in missing_departments)
            raise ValidationError(f"Configure a production unit before sending: {labels}.")
        if planned_tickets:
            created = _build_ticket_snapshots(locked, planned_tickets, created_by=cashier)
            locked.audit("KOTS_CREATED", actor=cashier, metadata={"count": len(created)})
            dispatch_tickets(created)
    locked._settling = True
    try:
        for row in payment_rows:
            try:
                # Savepoint so a constraint failure becomes ValidationError, not a broken transaction.
                with transaction.atomic():
                    OrderPayment.objects.create(
                        order=locked,
                        mode_of_payment=row["mode"],
                        amount=row["amount"],
                        reference_no=row["reference_no"],
                    )
            except IntegrityError as exc:
                raise ValidationError("This electronic payment reference has already been used.") from exc
    finally:
        del locked._settling

    locked.paid_amount = total_paid
    locked.change_amount = max(total_paid - locked.grand_total, Decimal("0"))
    locked.is_paid = True
    locked.status = SUBMITTED
    submitted_at = timezone.now()
    locked.submitted_at = submitted_at
    _stamp_submission(locked, submitted_at)
    locked.invoice_printed = True
    locked.invoice_printed_at = submitted_at
    locked.invoice_printed_by = cashier
    with _transition(locked, flag="_allow_submit"):
        locked.save()
    _convert_drink_reservations(locked, reservations_initialized=reservations_initialized)
    from apps.accounting.services import post_order_gl

    post_order_gl(locked)
    locked.audit("SUBMITTED", actor=cashier, metadata={"paid_amount": str(total_paid)})
    order.refresh_from_db()


@transaction.atomic
def cancel_sent_order(order, reason, reason_note="", cancelled_by=None):
    """Cancel a sent draft order (KOT exists), releasing reservations and tickets."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status != DRAFT:
        raise ValidationError("Only draft orders can be cancelled.")
    if locked.is_paid:
        raise ValidationError("Paid orders cannot be cancelled; use the refund flow.")
    if not locked.kots.exists():
        raise ValidationError("This order was never sent — delete it instead of cancelling.")
    if cancelled_by is not None and not locked.can_be_accessed_by(cancelled_by):
        raise ValidationError("Only the cashier who created this order, or a manager, can cancel it.")
    if not reason or not reason.strip():
        raise ValidationError("A cancel reason is required.")
    reason = reason.strip()
    reason_note = reason_note.strip()
    if reason not in dict(CANCEL_REASON_CHOICES):
        reason_note = reason_note or reason
        reason = CANCEL_REASON_OTHER

    locked.status = CANCELLED
    locked.cancel_reason = reason
    locked.cancel_reason_note = reason_note.strip()
    locked.cancelled_by = cancelled_by
    locked.cancelled_at = timezone.now()
    release_drink_reservations(locked)
    cancellation_kots = _cancel_kots(locked)
    with _transition(locked, flag="_allow_cancellation"):
        locked.save(
            update_fields=[
                "status",
                "cancel_reason",
                "cancel_reason_note",
                "cancelled_by",
                "cancelled_at",
                "updated_at",
            ]
        )
    locked.audit(
        "CANCELLED",
        actor=cancelled_by,
        metadata={"reason": reason, "ticket_count": len(cancellation_kots)},
    )
    order.refresh_from_db()
    return cancellation_kots


@transaction.atomic
def discard_order(order, discarded_by=None):
    """Mark an empty, untouched draft as discarded instead of cancelling it."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status != DRAFT:
        raise ValidationError("Only draft orders can be discarded.")
    if locked.items.exists():
        raise ValidationError("Only empty orders can be discarded.")
    if locked.invoice_printed or locked.kots.exists() or locked.is_paid:
        raise ValidationError("Printed, sent or paid orders cannot be discarded.")
    release_drink_reservations(locked)
    locked.status = DISCARDED
    locked.discarded_by = discarded_by
    locked.discarded_at = timezone.now()
    with _transition(locked, flag="_allow_discard"):
        locked.save(update_fields=["status", "discarded_by", "discarded_at", "updated_at"])
    locked.audit("DISCARDED", actor=discarded_by)
    order.refresh_from_db()


@transaction.atomic
def delete_unsent_draft(order, deleted_by=None):
    """Abandon an unsent draft as a tombstone: DISCARDED status with items and audit trail kept."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status != DRAFT:
        raise ValidationError("Only draft orders can be deleted.")
    if locked.invoice_printed or locked.kots.exists() or locked.is_paid:
        raise ValidationError("Printed, sent or paid orders cannot be deleted; cancel the order instead.")
    release_drink_reservations(locked)
    snapshot = [
        {"item": line.item_name, "qty": str(line.qty), "amount": str(line.amount)} for line in locked.items.all()
    ]
    locked.status = DISCARDED
    locked.discarded_by = deleted_by
    locked.discarded_at = timezone.now()
    with _transition(locked, flag="_allow_discard"):
        locked.save(update_fields=["status", "discarded_by", "discarded_at", "updated_at"])
    locked.audit("ORDER_DELETED", actor=deleted_by, metadata={"items": snapshot})
    order.refresh_from_db()


@contextmanager
def _transition(order, *, flag):
    """Set a private lifecycle flag for one guarded save, removing it on exit."""
    setattr(order, flag, True)
    try:
        yield
    finally:
        delattr(order, flag)


def _stamp_submission(order, submitted_at):
    """Move an order's posting date and time to the moment it is submitted."""
    order.posting_date = timezone.localdate(submitted_at)
    order.posting_time = timezone.localtime(submitted_at).time()


@transaction.atomic
def add_order_line(order, item, qty=1, customer_index=1, comments="", rate=None, menu_item=None, item_name=None):
    """Add a line to a draft order, merging identical lines and reserving drink stock."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    locked._ensure_editable()
    try:
        qty = Decimal(str(qty))
        rate = Decimal(str(rate)) if rate is not None else None
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValidationError("Quantity and rate must be valid decimals.") from exc
    if not qty.is_finite() or qty <= 0:
        raise ValidationError("Quantity must be greater than zero.")
    if rate is None or not rate.is_finite() or rate < 0:
        raise ValidationError("A valid non-negative selling rate is required.")
    if customer_index < 1 or customer_index > locked.guest_count:
        raise ValidationError("The customer card is outside this order's guest count.")
    locked._validate_pos_item(item)
    drink_qty = drink_quantities(locked)
    if item.department == "DRINKS":
        drink_qty[item.pk] = drink_qty.get(item.pk, Decimal("0")) + qty
    reserve_drink_stock(locked, drink_qty)
    existing = locked.items.filter(item=item, customer_index=customer_index, comments=comments or "").first()
    if existing:
        existing.qty += qty
        existing.save()
        order.refresh_from_db()
        return existing
    created = OrderItem.objects.create(
        order=locked,
        item=item,
        qty=qty,
        customer_index=customer_index,
        comments=comments,
        rate=rate,
        item_name=item_name or item.item_name,
        menu_item=menu_item,
    )
    order.refresh_from_db()
    return created


@transaction.atomic
def update_order_line_quantity(order, order_item_pk, qty):
    """Set a draft line quantity, reserving or releasing drink stock atomically."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    locked._ensure_editable()
    try:
        qty = Decimal(str(qty))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValidationError("Quantity must be a valid decimal.") from exc
    if not qty.is_finite():
        raise ValidationError("Quantity must be a valid decimal.")
    line = locked.items.select_related("item", "menu_item").filter(pk=order_item_pk).first()
    if line is None:
        raise ValidationError("That order line no longer exists.")
    if qty > 0:
        locked._validate_order_line_availability(line)
    drink_qty = drink_quantities(locked)
    if line.department == "DRINKS":
        drink_qty[line.item_id] -= line.qty
        if qty > 0:
            drink_qty[line.item_id] += qty
        if drink_qty[line.item_id] == 0:
            del drink_qty[line.item_id]
    reserve_drink_stock(locked, drink_qty)
    if qty <= 0:
        line.delete()
        order.refresh_from_db()
        return None
    line.qty = qty
    line.save()
    order.refresh_from_db()
    return line


@transaction.atomic
def remove_order_line(order, order_item_pk):
    """Remove a line from a draft order."""
    return update_order_line_quantity(order, order_item_pk, Decimal("0"))


@transaction.atomic
def clear_order_lines(order):
    """Remove every editable draft line and release its drink reservations."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    locked._ensure_editable()
    reserve_drink_stock(locked, {})
    locked.items.all().delete()
    locked.recalculate_totals()
    order.refresh_from_db()


@transaction.atomic
def make_return(order):
    """Create a manager-reviewed draft return with negative item quantities."""
    source = Order.objects.select_for_update().prefetch_related("items", "payments").get(pk=order.pk)
    if source.status != SUBMITTED:
        raise ValidationError("Can only return submitted orders.")
    if source.is_return:
        raise ValidationError("Cannot return a return order.")
    if not source.is_paid:
        raise ValidationError("Only paid orders can be returned.")
    # One active return draft at a time.
    if source.return_orders.filter(status=DRAFT).exists():
        raise ValidationError("This order already has an active return.")

    # Mirrors only the still-returnable remainder — submitted returns reduce it.
    previously_returned = {}
    returned_rows = (
        OrderItem.objects.filter(
            return_against_item_id__in=source.items.values_list("pk", flat=True),
            order__is_return=True,
        )
        .exclude(order__status=CANCELLED)
        .values("return_against_item_id")
        .annotate(total=Sum("qty"))
    )
    for row in returned_rows:
        previously_returned[row["return_against_item_id"]] = abs(row["total"] or Decimal("0"))
    return_order = Order.objects.create(
        order_type=source.order_type,
        customer_name=source.customer_name,
        guest_count=source.guest_count,
        cashier=source.cashier,
        opening_entry=source.opening_entry,
        is_return=True,
        return_against=source,
        stock_warehouse=source.stock_warehouse,
    )
    return_order.assign_order_number()
    for oi in source.items.all():
        remaining = oi.qty - previously_returned.get(oi.pk, Decimal("0"))
        if remaining <= 0:
            continue
        OrderItem.objects.create(
            order=return_order,
            item=oi.item,
            item_name=oi.item_name,
            qty=-remaining,
            rate=oi.rate,
            department=oi.department,
            stock_item=oi.stock_item,
            customer_index=oi.customer_index,
            comments=oi.comments,
            menu_item=oi.menu_item,
            return_against_item=oi,
        )
    return_order.recalculate_totals()
    return_order.audit("RETURN_CREATED", actor=source.cashier, metadata={"source_order": source.pk})
    return return_order


@transaction.atomic
def submit_return(order, actor=None):
    """Submit a return draft, restoring stock and mirroring refund payments."""
    locked = Order.objects.select_for_update().prefetch_related("items").get(pk=order.pk)
    if locked.status != DRAFT:
        raise ValidationError("Only draft returns can be submitted.")
    if not locked.is_return:
        raise ValidationError("Only return orders can be submitted through this flow.")
    source = locked.return_against
    if source is None or source.status != SUBMITTED or source.is_return:
        raise ValidationError("A return must reference a submitted non-return order.")
    for line in locked.items.select_related("item").all():
        line.full_clean()
    locked.recalculate_totals()
    # Stamp before the stock restore so return SLEs share the submission date with GL.
    submitted_at = timezone.now()
    locked.submitted_at = submitted_at
    _stamp_submission(locked, submitted_at)

    _restore_stock(locked, voucher_type="POS Return")
    refund_total = abs(locked.grand_total)
    if refund_total == 0:
        raise ValidationError("The return has no refundable value.")
    shares = _refund_payment_shares(source, refund_total)
    for payment, amount in shares:
        OrderPayment.objects.create(
            order=locked,
            mode_of_payment=payment.mode_of_payment,
            amount=-amount,
            reference_no="",
        )
    locked.paid_amount = -sum((amount for _, amount in shares), Decimal("0"))
    locked.is_paid = False
    locked.status = SUBMITTED
    with _transition(locked, flag="_allow_submit"):
        locked.save()
    # Refund GL mirrors the source settle legs for the refunded portion.
    from apps.accounting.services import post_refund_gl

    post_refund_gl(locked)
    locked.audit("RETURN_SUBMITTED", actor=actor, metadata={"source_order": source.pk})
    order.refresh_from_db()
    return order


@transaction.atomic
def create_tickets(order, created_by=None):
    """Create one immutable kitchen ticket and one bar ticket from the order snapshot."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status != DRAFT:
        raise ValidationError("Only draft orders can be sent to the kitchen or bar.")
    if locked.kots.exists():
        raise ValidationError("This order has already been sent to the kitchen or bar.")
    if not locked.items.exists():
        raise ValidationError("Add at least one item before sending the order.")

    if locked.order_number is None:
        locked.assign_order_number()

    planned_tickets, missing_departments = _plan_tickets(locked)

    if missing_departments:
        labels = ", ".join("Food" if department == "FOOD" else "Drinks" for department in missing_departments)
        raise ValidationError(f"Configure a production unit before sending: {labels}.")
    if not planned_tickets:
        raise ValidationError("No kitchen or bar ticket is required for this order.")
    created = _build_ticket_snapshots(locked, planned_tickets, created_by=created_by)

    locked.audit("KOTS_CREATED", actor=created_by, metadata={"count": len(created)})
    return created


def _plan_tickets(locked):
    """Group an order's lines by department and resolve each department's production unit."""
    from apps.settings.models import ProductionUnit

    items_by_department = {}
    for order_item in locked.items.select_related("item"):
        department = order_item.department or order_item.item.department
        items_by_department.setdefault(department, []).append(order_item)
    production_units = {pu.department: pu for pu in ProductionUnit.objects.all()}
    planned_tickets = []
    missing_departments = []
    for department, order_items in items_by_department.items():
        production_unit = production_units.get(department)
        if locked.order_type == TAKE_AWAY and production_unit and production_unit.block_takeaway_kot:
            continue
        if not production_unit:
            missing_departments.append(department)
            continue
        planned_tickets.append((department, order_items, production_unit))
    return planned_tickets, missing_departments


def _build_ticket_snapshots(locked, planned_tickets, *, created_by):
    """Create the immutable KOT/KOTItem snapshots for the planned tickets."""
    created = []
    for department, order_items, production_unit in planned_tickets:
        ticket_type = _ticket_type_for_department(department)
        # Temp number first — the final KOT-*/BOT-* embeds the pk.
        kot = KOT.objects.create(
            order=locked,
            production_unit=production_unit,
            type=NEW_ORDER,
            ticket_type=ticket_type,
            print_status=KOT_PRINT_PENDING,
            created_by=created_by,
            kot_number=f"TMP-{uuid4().hex}",
            order_number=locked.order_number,
        )
        kot.kot_number = f"{_ticket_prefix_for_type(ticket_type)}-{kot.pk:04d}"
        KOT.objects.filter(pk=kot.pk).update(kot_number=kot.kot_number)
        KOTItem.objects.bulk_create(
            [
                KOTItem(
                    kot=kot,
                    item=order_item.item,
                    item_name=order_item.item_name,
                    qty=order_item.qty,
                    comments=order_item.comments,
                    customer_index=order_item.customer_index,
                )
                for order_item in order_items
            ]
        )
        created.append(kot)
    return created


def dispatch_tickets(tickets):
    """Print each ticket, persist its print status, and return failed ticket types."""
    print_failures = []
    for kot in tickets:
        with transaction.atomic():
            ticket = KOT.objects.select_for_update().get(pk=kot.pk)
            if ticket.status != SUBMITTED:
                continue
            result = printing.print_ticket(ticket)
            if result.success:
                ticket.print_status = KOT_PRINTED
            else:
                ticket.print_status = KOT_PRINT_PENDING
                print_failures.append(result.ticket_type)
            ticket.save(update_fields=["print_status", "updated_at"])
    return print_failures


def drink_quantities(order):
    """Aggregate per-item drink quantities on a draft order ({item_id: qty})."""
    if order.is_return:
        return {}
    rows = (
        order.items.filter(Q(department="DRINKS") | Q(department__isnull=True, item__department="DRINKS"))
        .values("item_id")
        .annotate(qty=Sum("qty"))
        .order_by("item_id")
    )
    return {row["item_id"]: row["qty"] for row in rows if row["qty"] > 0}


def reserve_drink_stock(order, target_quantities):
    """Synchronize this draft's aggregate drink reservation to target quantities."""
    if order.is_return:
        return
    initialized = _reservations_initialized(order)
    current_quantities = drink_quantities(order) if initialized else {}
    item_ids = set(current_quantities) | set(target_quantities)
    if not item_ids:
        return
    if initialized and order.stock_warehouse_id:
        warehouse = order.stock_warehouse
        increasing = any(
            target_quantities.get(item_id, Decimal("0")) > current_quantities.get(item_id, Decimal("0"))
            for item_id in item_ids
        )
        if warehouse.disabled and increasing:
            raise ValidationError("The order's Bar/POS warehouse snapshot is disabled.")
    else:
        warehouse = _reservation_warehouse(order, required=bool(target_quantities))
    items = {item.pk: item for item in Item.objects.filter(pk__in=item_ids).order_by("pk")}
    for item_id in target_quantities:
        item = items.get(item_id)
        if (
            item is None
            or item.department != "DRINKS"
            or not (item.is_stock_item and item.is_sales_item and item.is_purchase_item)
        ):
            name = item.item_name if item else "This drink"
            raise ValidationError(f"{name} is not configured as a stock-tracked, sellable, purchasable drink.")
    if warehouse is None:
        return
    bins = _locked_drink_bins(item_ids, warehouse)
    for item_id in sorted(item_ids):
        bin_obj = bins[item_id]
        owned = current_quantities.get(item_id, Decimal("0"))
        target = target_quantities.get(item_id, Decimal("0"))
        increase = target - owned
        available_for_order = bin_obj.actual_qty - bin_obj.reserved_qty + owned
        if increase > 0 and target > available_for_order:
            raise ValidationError(f"Insufficient stock for {items[item_id].item_name} in {warehouse.name}.")
        new_reserved = bin_obj.reserved_qty + increase
        if new_reserved < 0:
            raise ValidationError("Stock reservation data is inconsistent; manager review is required.")
        bin_obj.reserved_qty = new_reserved
        bin_obj.save(update_fields=["reserved_qty", "updated_at"])
    snapshot_id = warehouse.pk if target_quantities else None
    if order.stock_warehouse_id != snapshot_id:
        Order.objects.filter(pk=order.pk).update(stock_warehouse_id=snapshot_id, updated_at=timezone.now())
        order.stock_warehouse_id = snapshot_id


def release_drink_reservations(order):
    """Release the order's drink reservations."""
    if not _reservations_initialized(order):
        return
    reserve_drink_stock(order, {})


def drink_stock_available(menu_items, settings):
    """Set POS availability (unreserved stock) on each menu item."""
    drink_item_ids = [mi.item_id for mi in menu_items if mi.item.department == "DRINKS"]
    drink_bins = (
        {
            bin_obj.item_id: bin_obj
            for bin_obj in Bin.objects.filter(
                item_id__in=drink_item_ids,
                warehouse=settings.default_warehouse,
            )
        }
        if settings and settings.default_warehouse_id
        else {}
    )
    for menu_item in menu_items:
        menu_item.stock_unavailable = False
        menu_item.stock_message = ""
        if menu_item.item.department != "DRINKS":
            continue
        if not settings or not settings.default_warehouse_id or settings.default_warehouse.disabled:
            menu_item.stock_unavailable = True
            menu_item.stock_message = "Setup required: configure the Bar/POS warehouse"
            continue
        if not (menu_item.item.is_stock_item and menu_item.item.is_sales_item and menu_item.item.is_purchase_item):
            menu_item.stock_unavailable = True
            menu_item.stock_message = "Setup required: configure this drink as stock-tracked, sellable, and purchasable"
            continue
        drink_bin = drink_bins.get(menu_item.item_id)
        available_qty = drink_bin.actual_qty - drink_bin.reserved_qty if drink_bin is not None else Decimal("0")
        if available_qty <= 0:
            menu_item.stock_unavailable = True
            menu_item.stock_message = "Out of stock"


def order_history_rows(filters):
    """Build the POS history queryset from parsed filters."""
    payment_filter = filters.get("payment", "")
    status_filter = filters.get("status", "sales")
    order_type_filter = filters.get("order_type", "")
    search = filters.get("search", "")
    posting_date = filters.get("posting_date")

    orders = Order.objects.select_related("cashier").annotate(item_count=Count("items", distinct=True))
    if search:
        search_query = Q(invoice_number__icontains=search)
        if search.isdigit():
            search_query |= Q(order_number=int(search))
        orders = orders.filter(search_query)
    if status_filter == "all":
        orders = orders.filter(
            Q(status=SUBMITTED, is_return=False, is_paid=True)
            | Q(status=SUBMITTED, is_return=True)
            | Q(status=CANCELLED, is_return=False)
            | Q(status=DISCARDED, is_return=False)
        )
    elif status_filter == "returns":
        orders = orders.filter(status=SUBMITTED, is_return=True)
    elif status_filter == "cancelled":
        orders = orders.filter(status=CANCELLED, is_return=False)
    elif status_filter == "discarded":
        orders = orders.filter(status=DISCARDED, is_return=False)
    else:
        orders = orders.filter(status=SUBMITTED, is_paid=True, is_return=False)
    if status_filter == "sales" and payment_filter == "cash":
        orders = orders.filter(
            status=SUBMITTED,
            is_return=False,
            is_paid=True,
            payments__mode_of_payment__type="CASH",
        )
    elif status_filter == "sales" and payment_filter == "electronic":
        orders = orders.filter(
            status=SUBMITTED,
            is_return=False,
            is_paid=True,
            payments__mode_of_payment__type__in=["BANK", "PHONE"],
        )
    if order_type_filter in {DINE_IN, TAKE_AWAY}:
        orders = orders.filter(order_type=order_type_filter)
    if posting_date is not None:
        orders = orders.filter(posting_date=posting_date)
    return orders.distinct().order_by("-posting_date", "-posting_time")


class _DraftOrder(Protocol):
    item_count: int
    item_preview: object
    minutes_ago: int


def open_draft_orders(shift, user, order_filter="all", order_search=""):
    """Return the user's visible draft orders for the POS home screen, with item previews attached."""
    draft_orders_queryset = (
        Order.objects.open_drafts_for(shift, user)
        .prefetch_related("items")
        .annotate(has_sent_ticket=Exists(KOT.objects.filter(order_id=OuterRef("pk"), status=SUBMITTED)))
        .order_by("-updated_at")
        .only(
            "pk",
            "invoice_number",
            "order_number",
            "order_type",
            "guest_count",
            "grand_total",
            "arrived_time",
            "updated_at",
            "invoice_printed",
        )
    )
    if order_filter == "draft":
        draft_orders_queryset = draft_orders_queryset.filter(has_sent_ticket=False)
    elif order_filter == "sent":
        draft_orders_queryset = draft_orders_queryset.filter(has_sent_ticket=True)
    if order_search:
        search_query = Q(items__item_name__icontains=order_search)
        if order_search.isdigit():
            search_query |= Q(order_number=int(order_search))
        draft_orders_queryset = draft_orders_queryset.filter(search_query).distinct()
    draft_orders = list(draft_orders_queryset)
    for order in draft_orders:
        draft_order = cast(_DraftOrder, order)
        items = list(order.items.all())
        draft_order.item_count = len(items)
        draft_order.item_preview = items[:3]
        draft_order.minutes_ago = max(int((timezone.now() - order.updated_at).total_seconds() // 60), 0)
    return draft_orders


def apply_add_on_line(order, item, add_on_ids, qty, customer_index, comments=""):
    """Price and merge an item's add-on lines into the order."""
    from apps.settings.models import Restaurant

    settings = Restaurant.load()
    active_menu = settings.active_menu if settings and settings.active_menu and settings.active_menu.enabled else None
    menu_item = MenuItem.objects.filter(item=item, menu=active_menu, disabled=False).first()
    if menu_item is None:
        raise ValidationError("That menu item is not on the active menu.")
    add_ons = []
    if add_on_ids:
        add_ons = list(item.add_ons.select_related("add_on_item").filter(add_on_item_id__in=add_on_ids))
        if len(add_ons) != len(set(add_on_ids)):
            raise ValidationError("One or more selected add-ons are not available for this item.")
        add_on_menu_items = {
            candidate.item_id: candidate
            for candidate in MenuItem.objects.filter(
                item_id__in=[add_on.add_on_item_id for add_on in add_ons],
                menu=active_menu,
                disabled=False,
                item__disabled=False,
                item__is_sales_item=True,
            ).select_related("item")
        }
        if len(add_on_menu_items) != len(add_ons):
            raise ValidationError("One or more selected add-ons are not on the active menu.")
    with transaction.atomic():
        add_order_line(
            order,
            item=item,
            qty=qty,
            customer_index=customer_index,
            comments=comments,
            rate=menu_item.rate,
            menu_item=menu_item,
            item_name=menu_item.item_name,
        )
        for add_on in add_ons:
            add_order_line(
                order,
                item=add_on.add_on_item,
                qty=qty,
                customer_index=customer_index,
                comments="",
                rate=add_on_menu_items[add_on.add_on_item_id].rate,
                menu_item=add_on_menu_items[add_on.add_on_item_id],
                item_name=add_on_menu_items[add_on.add_on_item_id].item_name,
            )
        order.recalculate_totals()


def _validate_payment_data(order, payments_data, opening_entry):
    """Resolve and validate payment rows before changing the order."""
    from apps.settings.models import Restaurant

    if not isinstance(payments_data, (list, tuple)) or not payments_data:
        raise ValidationError("At least one payment is required.")
    require_reference = Restaurant.requires_payment_reference()

    opening_mode_ids = set(opening_entry.opening_payments.values_list("mode_of_payment_id", flat=True))
    payment_rows = []
    for row_number, entry in enumerate(payments_data, start=1):
        if not isinstance(entry, dict):
            raise ValidationError(f"Payment row {row_number} is malformed.")

        try:
            amount = Decimal(str(entry.get("amount")))
            if not amount.is_finite() or amount != money(amount):
                raise InvalidOperation
        except InvalidOperation, TypeError, ValueError:
            raise ValidationError(f"Payment row {row_number} has a malformed amount.") from None
        if amount < 0:
            raise ValidationError(f"Payment row {row_number} amount must not be negative.")
        if amount == 0:
            continue

        mode_reference = entry.get("mode_of_payment")
        if mode_reference is None:
            mode_reference = entry.get("mode_of_payment_id")
        if isinstance(mode_reference, ModeOfPayment):
            mode_pk = mode_reference.pk
        else:
            try:
                if isinstance(mode_reference, bool):
                    raise ValueError
                mode_pk = int(mode_reference)
            except TypeError, ValueError:
                raise ValidationError(f"Payment row {row_number} has an invalid payment mode.") from None

        mode = ModeOfPayment.objects.select_related("gl_mapping").filter(pk=mode_pk).first()
        if mode is None:
            raise ValidationError(f"Payment row {row_number} has an invalid payment mode.")
        if not mode.enabled:
            raise ValidationError(f"Payment mode {mode.name} is disabled.")
        if mode.pk not in opening_mode_ids:
            raise ValidationError(f"Payment mode {mode.name} was not declared when the shift opened.")
        try:
            mapping = mode.gl_mapping
        except PaymentGLMapping.DoesNotExist:
            raise ValidationError(f"Payment mode {mode.name} has no GL mapping.") from None
        if not mapping.default_account_id:
            raise ValidationError(f"Payment mode {mode.name} has no GL mapping.")

        reference_no = str(entry.get("reference_no", "") or "").strip()
        if len(reference_no) > 100:
            raise ValidationError(f"Payment row {row_number} has a reference that is too long.")
        if require_reference and mode.type != ModeOfPayment.TYPE_CASH and not reference_no:
            raise ValidationError(f"Payment row {row_number} requires a reference for {mode.name}.")

        payment_rows.append(
            {
                "mode": mode,
                "amount": amount,
                "reference_no": reference_no,
            }
        )
    if not payment_rows:
        raise ValidationError("At least one payment is required.")
    return payment_rows


def _snapshot_stock_warehouse(order):
    """Validate or capture the configured warehouse for this transaction."""
    from apps.settings.models import Restaurant

    settings = Restaurant.objects.select_for_update().first()
    warehouse = settings.default_warehouse if settings else None
    if warehouse is not None and warehouse.disabled:
        raise ValidationError("Restaurant.default_warehouse is disabled.")
    has_drinks = order.items.filter(
        Q(department="DRINKS") | Q(department__isnull=True, item__department="DRINKS")
    ).exists()
    if has_drinks and warehouse is None:
        raise ValidationError("Configure the Bar/POS warehouse before settling drinks.")
    if has_drinks and order.stock_warehouse_id:
        if order.stock_warehouse_id != warehouse.pk:
            raise ValidationError("The Bar/POS warehouse changed while this order was open. Clear or cancel the order.")
        if order.stock_warehouse.disabled:
            raise ValidationError("The order's Bar/POS warehouse snapshot is disabled.")
        return order.stock_warehouse
    order.stock_warehouse = warehouse if has_drinks else None
    return order.stock_warehouse


def _locked_drink_stock(order, *, reservations_initialized):
    """Lock and return the order's drink stock rows for settlement.

    reservations_initialized: when False, the order predates reservations and draws all qty from unreserved stock.
    """
    drink_items = list(
        order.items.select_related("item")
        .filter(Q(department="DRINKS") | Q(department__isnull=True, item__department="DRINKS"))
        .order_by("item_id", "pk")
    )
    if not drink_items:
        return [], {}
    if not order.stock_warehouse_id:
        raise ValidationError("This order has no stock warehouse snapshot.")
    warehouse = order.stock_warehouse
    quantities = {}
    for order_item in drink_items:
        if not (order_item.item.is_stock_item and order_item.item.is_sales_item and order_item.item.is_purchase_item):
            raise ValidationError(f"{order_item.item_name} must be a stock-tracked, sellable, purchasable drink.")
        quantities[order_item.item_id] = quantities.get(order_item.item_id, Decimal("0")) + order_item.qty
    owned_quantities = quantities if reservations_initialized else {}
    bins = _locked_drink_bins(quantities, warehouse)
    for item_id, qty in sorted(quantities.items()):
        bin_obj = bins[item_id]
        owned = owned_quantities.get(item_id, Decimal("0"))
        if bin_obj.reserved_qty < owned:
            raise ValidationError("Stock reservation data is inconsistent; manager review is required.")
        if bin_obj.actual_qty - bin_obj.reserved_qty + owned < qty:
            item = next(line.item for line in drink_items if line.item_id == item_id)
            raise ValidationError(f"Insufficient stock for {item.item_name} in {warehouse.name}.")
    return drink_items, bins


def _convert_drink_reservations(order, *, reservations_initialized):
    """Atomically convert this order's drink reservations into stock ledger issues."""
    voucher_no = str(order.pk)
    drink_items, bins = _locked_drink_stock(order, reservations_initialized=reservations_initialized)
    if not drink_items:
        return
    quantities = {}
    for order_item in drink_items:
        quantities[order_item.item_id] = quantities.get(order_item.item_id, Decimal("0")) + order_item.qty
    owned_quantities = quantities if reservations_initialized else {}
    for item_id in sorted(quantities):
        bin_obj = bins[item_id]
        owned = owned_quantities.get(item_id, Decimal("0"))
        bin_obj.reserved_qty -= owned
        bin_obj.save(update_fields=["reserved_qty", "updated_at"])
    for oi in drink_items:
        StockLedgerEntry._create_entry_locked(
            item=oi.item,
            warehouse=order.stock_warehouse,
            quantity=-oi.qty,
            voucher_type="POS Order",
            voucher_no=voucher_no,
            voucher_detail_no=str(oi.pk),
            unit_rate=None,
            posting_date=order.posting_date,
            bin_obj=bins[oi.item_id],
        )


def _refund_payment_shares(source, refund_total):
    """Split a refund across source payment modes in proportion to net tenders."""
    from apps.settings.models import Restaurant

    settings = Restaurant.load()
    change_account_id = settings.account_for_change_amount_id if settings else None
    change_left = source.change_amount or Decimal("0")
    nets = []
    for payment in source.payments.select_related("mode_of_payment").all():
        amount = payment.amount
        if change_left and change_account_id:
            mapping = PaymentGLMapping.objects.filter(mode_of_payment=payment.mode_of_payment).first()
            if mapping and mapping.default_account_id == change_account_id:
                cut = min(amount, change_left)
                amount -= cut
                change_left -= cut
        if amount:
            nets.append((payment, amount))
    net_sum = sum((net for _, net in nets), Decimal("0"))
    if net_sum <= 0:
        raise ValidationError("The source order has no net payments to refund.")
    source_total = source.grand_total or Decimal("0")
    if source_total <= 0:
        raise ValidationError("The source order has no refundable value.")
    ratio = refund_total / source_total
    target = money(net_sum * ratio)
    shares = []
    remaining = target
    for index, (payment, net) in enumerate(nets):
        if index == len(nets) - 1:
            share = remaining
        else:
            share = money(target * net / net_sum)
            remaining -= share
        if share:
            shares.append((payment, share))
    return shares


@transaction.atomic
def update_return_line(order, line_pk, *, qty=None, not_restockable=None):
    """Edit a return-draft line: reduce qty, drop the line, or mark wastage."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status != DRAFT or not locked.is_return:
        raise ValidationError("Only return drafts can be edited.")
    line = locked.items.select_related("item").get(pk=line_pk)
    if qty is not None:
        qty = Decimal(str(qty))
        if qty == 0:
            line.delete()
            locked.recalculate_totals()
            order.refresh_from_db()
            return order
        if qty > 0:
            qty = -qty
        line.qty = qty
    if not_restockable is not None:
        line.not_restockable = bool(not_restockable)
    line.save()
    locked.recalculate_totals()
    order.refresh_from_db()
    return order


def settle_time_rate(source_order, item):
    """Weighted-average WAC at which the source order issued the item."""
    sles = StockLedgerEntry.objects.filter(
        voucher_type="POS Order",
        voucher_no=str(source_order.pk),
        item=item,
        quantity__lt=0,
    )
    total_qty = sum((abs(s.quantity) for s in sles), Decimal("0"))
    total_value = sum((abs(s.quantity) * s.unit_rate for s in sles), Decimal("0"))
    if total_qty == 0:
        raise ValidationError(f"Settle-time valuation rate for {item.item_name} cannot be resolved.")
    return total_value / total_qty


def _restore_stock(order, voucher_type="POS Return"):
    """Restore restockable return lines to stock; not_restockable lines post as wastage."""
    voucher_no = str(order.pk)
    stock_items = order.items.select_related("item").filter(
        Q(department="DRINKS") | Q(department__isnull=True, item__department="DRINKS")
    )
    if not stock_items.exists():
        return
    if not order.stock_warehouse_id:
        raise ValidationError("This order has no stock warehouse snapshot for reversal.")
    warehouse = order.stock_warehouse
    source = order.return_against
    for oi in stock_items.only("item__is_stock_item", "qty", "not_restockable"):
        if oi.not_restockable:
            continue
        StockLedgerEntry.create_entry(
            item=oi.item,
            warehouse=warehouse,
            quantity=abs(oi.qty),
            unit_rate=settle_time_rate(source, oi.item),
            voucher_type=voucher_type,
            voucher_no=voucher_no,
            voucher_detail_no=str(oi.pk),
            posting_date=order.posting_date,
        )


def _cancel_kots(order):
    """Create one cancellation KOT per station and close the source tickets."""
    active_kots = list(
        order.kots.filter(status=SUBMITTED, type=NEW_ORDER)
        .select_related("production_unit")
        .prefetch_related("items__item")
    )
    if not active_kots:
        return []
    created = []
    # One cancellation sheet per production unit.
    by_station = {}
    for original_kot in active_kots:
        station = by_station.setdefault(
            original_kot.production_unit_id,
            {"production_unit": original_kot.production_unit, "kots": [], "items": []},
        )
        station["kots"].append(original_kot)
        station["items"].extend(original_kot.items.all())

    for station in by_station.values():
        original_kots = station["kots"]
        production_unit = station["production_unit"]
        original_names = [kot.kot_number for kot in original_kots]
        ticket_type = original_kots[0].ticket_type
        # Temp number first — the final CNCL-* embeds the pk and kot_number is unique.
        kot = KOT.objects.create(
            order=order,
            production_unit=production_unit,
            type=KOT_CANCELLED,
            ticket_type=ticket_type,
            print_status=KOT_PRINT_PENDING,
            created_by=order.cancelled_by,
            kot_number=f"TMP-{uuid4().hex}",
            order_number=order.order_number,
            original_kots=",".join(original_names),
        )
        kot.kot_number = f"CNCL-{_ticket_prefix_for_type(kot.ticket_type)}-{kot.pk:04d}"
        KOT.objects.filter(pk=kot.pk).update(kot_number=kot.kot_number)
        kot_items = [
            # qty stays 0 so the cancellation sheet shows what was taken off the order.
            KOTItem(
                kot=kot,
                item=ticket_item.item,
                item_name=ticket_item.item_name,
                qty=Decimal("0"),
                cancelled_qty=abs(ticket_item.qty),
                comments=ticket_item.comments,
                customer_index=ticket_item.customer_index,
            )
            for ticket_item in station["items"]
        ]
        KOTItem.objects.bulk_create(kot_items)
        order.kots.filter(pk__in=[original.pk for original in original_kots]).update(
            status=CANCELLED,
            cancelled_by=order.cancelled_by_id,
            cancelled_at=timezone.now(),
            updated_at=timezone.now(),
        )
        created.append(kot)
    return created


def _ticket_type_for_department(department):
    return TICKET_KITCHEN if department == "FOOD" else TICKET_BAR


def _ticket_prefix_for_type(ticket_type):
    return "KOT" if ticket_type == TICKET_KITCHEN else "BOT"


def _reservations_initialized(order):
    return bool(order.stock_warehouse_id) and not order.is_return


def _reservation_warehouse(order, *, required):
    # The order's stock_warehouse snapshot is pinned at first reservation.
    from apps.settings.models import Restaurant

    restaurant = Restaurant.objects.select_for_update().first()
    warehouse = (
        type(restaurant).objects.select_related("default_warehouse").get(pk=restaurant.pk).default_warehouse
        if restaurant
        else None
    )
    if required and warehouse is None:
        raise ValidationError("Configure the Bar/POS warehouse before selling drinks.")
    if warehouse is not None and warehouse.disabled:
        raise ValidationError("The configured Bar/POS warehouse is disabled.")
    if order.stock_warehouse_id and warehouse and order.stock_warehouse_id != warehouse.pk:
        raise ValidationError("The Bar/POS warehouse changed while this order was open. Clear or cancel the order.")
    return order.stock_warehouse or warehouse


def _locked_drink_bins(item_ids, warehouse):
    # Pre-create bins so select_for_update locks a stable row set.
    item_ids = sorted(set(item_ids))
    existing_ids = set(Bin.objects.filter(item_id__in=item_ids, warehouse=warehouse).values_list("item_id", flat=True))
    for item_id in item_ids:
        if item_id not in existing_ids:
            Bin.objects.get_or_create(item_id=item_id, warehouse=warehouse)
    return {
        bin_obj.item_id: bin_obj
        for bin_obj in Bin.objects.select_for_update()
        .filter(item_id__in=item_ids, warehouse=warehouse)
        .order_by("item_id")
    }
