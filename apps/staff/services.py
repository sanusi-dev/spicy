"""Staff workflow services — shift closing logic shared across apps."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import OuterRef, Subquery, Sum
from django.utils import timezone

from apps.orders.models import SUBMITTED, Order, OrderItem, OrderPayment
from apps.payments.models import ModeOfPayment

from .models import ClosingPayment, OpeningPayment, POSClosingEntry, POSOpeningEntry, ShiftCashOut


def collect_submitted_payment_totals(submitted_orders, payment_rows) -> dict[int, Decimal]:
    """Return submitted payment totals per mode, net of cash change."""

    mode_ids = {payment.mode_of_payment_id for payment in payment_rows}
    if not mode_ids:
        return {}

    payment_totals = {
        row["mode_of_payment_id"]: row["total"] or Decimal("0")
        for row in OrderPayment.objects.filter(
            order__in=submitted_orders,
            mode_of_payment_id__in=mode_ids,
        )
        .values("mode_of_payment_id")
        .annotate(total=Sum("amount"))
    }
    cash_mode_ids = {
        payment.mode_of_payment_id
        for payment in payment_rows
        if payment.mode_of_payment.type == ModeOfPayment.TYPE_CASH
    }
    if not cash_mode_ids:
        return payment_totals

    cash_change_totals: dict[int, Decimal] = {}
    cash_orders = (
        submitted_orders.filter(payments__mode_of_payment_id__in=cash_mode_ids)
        .values("payments__mode_of_payment_id", "pk", "change_amount")
        .distinct()
    )
    for row in cash_orders:
        mode_id = row["payments__mode_of_payment_id"]
        cash_change_totals[mode_id] = cash_change_totals.get(mode_id, Decimal("0")) + row["change_amount"]
    for mode_id, change_total in cash_change_totals.items():
        payment_totals[mode_id] = payment_totals.get(mode_id, Decimal("0")) - change_total
    return payment_totals


def expected_closing_amounts(open_shift, period_start, period_end) -> list[dict[str, Any]]:
    """Compute expected drawer amounts: opening float + collected (net of change) - refunds - cash-outs."""
    submitted_orders = Order.objects.submitted_in_shift(open_shift, period_start, period_end)
    opening_payments = list(open_shift.opening_payments.select_related("mode_of_payment").all())
    collected_by_mode = collect_submitted_payment_totals(submitted_orders, opening_payments)
    refunded_orders = Order.objects.filter(
        opening_entry=open_shift,
        status=SUBMITTED,
        is_return=True,
        submitted_at__gte=period_start,
        submitted_at__lte=period_end,
    )
    refund_rows = OrderPayment.objects.filter(order__in=refunded_orders)
    refund_by_mode = {
        row["mode_of_payment_id"]: abs(row["total"] or Decimal("0"))
        for row in refund_rows.values("mode_of_payment_id").annotate(total=Sum("amount")).order_by()
    }
    cash_out_by_mode = {
        row["mode_of_payment_id"]: row["total"] or Decimal("0")
        for row in ShiftCashOut.objects.filter(opening_entry=open_shift, status=ShiftCashOut.SUBMITTED)
        .values("mode_of_payment_id")
        .annotate(total=Sum("amount"))
        .order_by()
    }
    rows = []
    for opening_payment in opening_payments:
        collected = collected_by_mode.get(opening_payment.mode_of_payment_id, Decimal("0"))
        refunded = refund_by_mode.get(opening_payment.mode_of_payment_id, Decimal("0"))
        cash_out = cash_out_by_mode.get(opening_payment.mode_of_payment_id, Decimal("0"))
        rows.append(
            {
                "mode": opening_payment.mode_of_payment,
                "opening_amount": opening_payment.opening_amount,
                "expected_amount": opening_payment.opening_amount + collected - refunded - cash_out,
            }
        )
    return rows


def ensure_closing_draft(open_shift, cashier) -> POSClosingEntry:
    """Return the draft closing entry for this shift, creating it only when needed."""
    closing = POSClosingEntry.objects.filter(
        opening_entry=open_shift,
        status=POSClosingEntry.DRAFT,
    ).first()
    if closing is not None:
        return closing
    closing = POSClosingEntry.objects.create(opening_entry=open_shift, cashier=cashier)
    ClosingPayment.objects.bulk_create(
        [
            ClosingPayment(
                closing_entry=closing,
                mode_of_payment=opening_payment.mode_of_payment,
                opening_amount=opening_payment.opening_amount,
                expected_amount=opening_payment.opening_amount,
                closing_amount=Decimal("0"),
            )
            for opening_payment in open_shift.opening_payments.all()
        ]
    )
    return closing


@transaction.atomic
def open_shift(cashier, opening_amounts, remarks="") -> POSOpeningEntry:
    """Open a shift with the declared opening float per payment mode."""
    from apps.settings.models import Restaurant

    settings = Restaurant.objects.select_for_update().first()
    if settings is None:
        raise ValidationError("Restaurant settings are not configured.")
    open_exists = (
        POSOpeningEntry.objects.select_for_update()
        .filter(status=POSOpeningEntry.SUBMITTED, closing_entry__isnull=True)
        .order_by("period_start_date")
        .first()
    )
    if open_exists is not None:
        raise ValidationError("A shift is already open.")
    entry = POSOpeningEntry.objects.create(cashier=cashier, remarks=remarks.strip())
    OpeningPayment.objects.bulk_create(
        [
            OpeningPayment(opening_entry=entry, mode_of_payment=mode, opening_amount=amount)
            for mode, amount in opening_amounts.items()
        ]
    )
    entry.full_clean()
    entry.submit()
    return entry


@transaction.atomic
def submit_closing_entry(closing, actor=None) -> None:
    """Compute expected amounts, validate, and close the opening entry."""
    if closing.status != POSClosingEntry.DRAFT:
        return
    from apps.settings.models import Restaurant

    settings = Restaurant.objects.select_for_update().first()
    if settings is None:
        raise ValidationError("Restaurant settings are not configured.")
    locked = POSClosingEntry.objects.select_for_update().select_related("opening_entry").get(pk=closing.pk)
    opening = POSOpeningEntry.objects.select_for_update().get(pk=locked.opening_entry_id)
    if not opening.is_open:
        raise ValidationError("The opening shift is no longer open.")
    if actor is not None and not opening.can_be_closed_by(actor):
        raise ValidationError("Only the cashier who opened this shift, or a manager, can close it.")
    # Cut off at submit time so orders settled after the draft was opened are included.
    locked.period_end_date = timezone.now()
    opening_payments = list(opening.opening_payments.select_related("mode_of_payment").all())
    opening_modes = {op.mode_of_payment_id: op for op in opening_payments}
    closing_payments = list(locked.closing_payments.select_related("mode_of_payment").all())

    draft_count = Order.objects.open_drafts(opening).count()
    if draft_count:
        raise ValidationError(
            f"Close or settle {draft_count} open order{'s' if draft_count != 1 else ''} before closing the shift."
        )

    submitted_orders = Order.objects.submitted_in_shift(opening, locked.period_start_date, locked.period_end_date)
    # Returns are excluded — their refunds don't touch drawer sales.
    item_totals = (
        OrderItem.objects.filter(order_id=OuterRef("pk")).values("order_id").annotate(total=Sum("qty")).values("total")
    )
    order_totals = submitted_orders.aggregate(
        total_quantity=Sum(Subquery(item_totals)),
        net_total=Sum("net_total"),
        grand_total=Sum("grand_total"),
    )
    locked.bill_count = submitted_orders.count()
    locked.total_quantity = order_totals["total_quantity"] or Decimal("0")
    locked.net_total = order_totals["net_total"] or Decimal("0")
    locked.grand_total = order_totals["grand_total"] or Decimal("0")
    refunded_grands = Order.objects.filter(
        opening_entry=opening,
        status=SUBMITTED,
        is_return=True,
        submitted_at__gte=locked.period_start_date,
        submitted_at__lte=locked.period_end_date,
    ).values_list("grand_total", flat=True)
    locked.refunded_total = sum((abs(total or Decimal("0")) for total in refunded_grands), Decimal("0"))

    for cp in closing_payments:
        if cp.mode_of_payment_id not in opening_modes:
            raise ValidationError({"mode_of_payment": (f"{cp.mode_of_payment} was not declared at shift open.")})

    expected_by_mode = {
        row["mode"].pk: row
        for row in expected_closing_amounts(opening, locked.period_start_date, locked.period_end_date)
    }
    for cp in closing_payments:
        expected = expected_by_mode[cp.mode_of_payment_id]
        cp.opening_amount = expected["opening_amount"]
        cp.expected_amount = expected["expected_amount"]
        if cp.mode_of_payment.type != ModeOfPayment.TYPE_CASH and cp.closing_amount > cp.expected_amount:
            raise ValidationError(
                f"Counted {cp.mode_of_payment.name} amount is above the expected {cp.expected_amount}. "
                "A non-cash total can't exceed what was processed. Reconcile it before closing."
            )
        cp.difference = cp.closing_amount - cp.expected_amount
        cp.save(
            update_fields=[
                "opening_amount",
                "expected_amount",
                "difference",
                "updated_at",
            ]
        )
    locked.total_short_excess = sum((cp.difference for cp in closing_payments), Decimal("0"))

    threshold = settings.variance_approval_threshold
    if threshold is not None and abs(locked.total_short_excess) > threshold:
        is_manager_actor = actor is not None and (actor.is_manager or actor.is_admin or actor.is_superuser)
        if not locked.variance_note.strip() or not is_manager_actor:
            raise ValidationError(
                "The cash variance exceeds the approval threshold. "
                "A manager must provide a variance note to close the shift."
            )

    locked.status = POSClosingEntry.SUBMITTED
    locked._allow_submit = True
    try:
        locked.save(
            update_fields=[
                "period_end_date",
                "bill_count",
                "total_quantity",
                "net_total",
                "grand_total",
                "refunded_total",
                "total_short_excess",
                "variance_note",
                "status",
                "updated_at",
            ]
        )
    finally:
        del locked._allow_submit
    opening.closing_entry = locked
    opening.period_end_date = locked.period_end_date
    opening.save(update_fields=["closing_entry", "period_end_date", "updated_at"])

    if locked.total_short_excess or any(cp.difference for cp in closing_payments):
        from apps.accounting.services import post_cash_variance_gl

        journal = post_cash_variance_gl(locked)
        if journal is not None:
            locked.variance_journal_entry = journal
            locked._allow_submit = True
            try:
                locked.save(update_fields=["variance_journal_entry", "updated_at"])
            finally:
                del locked._allow_submit
    closing.refresh_from_db()


@transaction.atomic
def record_cash_out(opening, *, mode, amount, reason, note="", actor=None) -> ShiftCashOut:
    """Record a submitted cash-out voucher and post its GL legs."""
    from apps.accounting.services import post_shift_cash_out_gl

    locked = POSOpeningEntry.objects.select_for_update().get(pk=opening.pk)
    row = ShiftCashOut(
        opening_entry=locked,
        mode_of_payment=mode,
        amount=amount,
        reason=reason,
        note=(note or "").strip(),
        status=ShiftCashOut.SUBMITTED,
        recorded_by=actor,
    )
    row.full_clean()
    row.save()
    post_shift_cash_out_gl(row)
    return row


@transaction.atomic
def cancel_cash_out(row, *, actor=None) -> ShiftCashOut:
    """Cancel a cash-out voucher with a mirrored GL reversal."""
    is_manager = actor is not None and (actor.is_manager or actor.is_admin or actor.is_superuser)
    if not is_manager:
        raise ValidationError("Only a manager or admin can cancel a cash-out.")
    locked = ShiftCashOut.objects.select_for_update().select_related("opening_entry").get(pk=row.pk)
    if locked.status == ShiftCashOut.CANCELLED:
        raise ValidationError("This cash-out has already been cancelled.")
    if not locked.opening_entry.is_open:
        raise ValidationError("Cash-outs can only be cancelled while the shift is open.")
    locked.status = ShiftCashOut.CANCELLED
    locked.cancelled_by = actor
    locked.cancelled_at = timezone.now()
    locked.full_clean()
    locked.save(update_fields=["status", "cancelled_by", "cancelled_at", "updated_at"])

    from apps.accounting.services import _reverse_gl

    _reverse_gl(
        "Shift Cash-Out",
        str(locked.pk),
        remarks=f"Cancelled shift cash-out #{locked.pk}",
    )
    row.refresh_from_db()
    return row
