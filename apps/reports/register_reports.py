"""POS register — submitted shift closes, displaying stored netting."""

from decimal import Decimal

from apps.staff.models import POSClosingEntry
from apps.users.models import CustomUser
from apps.utils.rounding import money

ZERO = Decimal("0")


def _q2(value):
    return money(value or ZERO)


def pos_register(date_from=None, date_to=None, *, cashier_id=None):
    """One row per SUBMITTED POSClosingEntry, with ClosingPayment detail."""
    qs = (
        POSClosingEntry.objects.filter(status=POSClosingEntry.SUBMITTED)
        .select_related("cashier", "opening_entry")
        .prefetch_related("closing_payments__mode_of_payment")
        .order_by("posting_date", "pk")
    )
    if date_from:
        qs = qs.filter(posting_date__gte=date_from)
    if date_to:
        qs = qs.filter(posting_date__lte=date_to)
    if cashier_id:
        qs = qs.filter(cashier_id=cashier_id)
    rows = []
    for closing in qs:
        payments = [
            {
                "mode": payment.mode_of_payment.name,
                "expected": _q2(payment.expected_amount),
                "counted": _q2(payment.closing_amount),
                "difference": _q2(payment.difference),
            }
            for payment in closing.closing_payments.all()
        ]
        rows.append(
            {
                "pk": closing.pk,
                "shift": closing.opening_entry_id,
                "posting_date": closing.posting_date,
                "cashier_id": closing.cashier_id,
                "cashier_name": closing.cashier.get_display_name() if closing.cashier_id else "—",
                "payments": payments,
                "total_short_excess": _q2(closing.total_short_excess),
            }
        )
    totals = {
        "closes": len(rows),
        "total_short_excess": _q2(sum((row["total_short_excess"] for row in rows), ZERO)),
    }
    return rows, totals


def register_cashiers():
    """Cashiers who have submitted a closing entry."""
    return (
        CustomUser.objects.filter(
            pos_closing_entries__status=POSClosingEntry.SUBMITTED,
        )
        .distinct()
        .order_by("username")
    )
