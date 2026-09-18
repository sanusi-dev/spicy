"""Query-based sales reports. No persistent aggregates."""

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, ExtractMonth, ExtractYear

from apps.orders.models import CANCELLED, SUBMITTED, Order, OrderItem
from apps.utils.rounding import money

from .models import DRINKS, FOOD

ZERO = Decimal("0")


def _q2(value):
    return money(value or ZERO)


def submitted_orders(date_from=None, date_to=None):
    """SUBMITTED sales and returns in the calendar posting_date window."""
    qs = Order.objects.filter(status=SUBMITTED)
    if date_from:
        qs = qs.filter(posting_date__gte=date_from)
    if date_to:
        qs = qs.filter(posting_date__lte=date_to)
    return qs


def _order_buckets(qs, group_fields):
    return (
        qs.values(*group_fields)
        .annotate(
            bills=Count("pk"),
            rounding=Coalesce(Sum("rounding_adjustment"), ZERO),
            net=Coalesce(Sum("rounded_total"), ZERO),
            refunded=Coalesce(Sum("grand_total", filter=Q(is_return=True)), ZERO),
        )
        .order_by(*group_fields)
    )


def _dept_totals(order_qs, order_field_map):
    """Sum OrderItem.amount by department, keyed by the mapped order fields."""
    values = [*order_field_map.values(), "department"]
    rows = OrderItem.objects.filter(order__in=order_qs).values(*values).annotate(amount=Coalesce(Sum("amount"), ZERO))
    by_key = defaultdict(lambda: {FOOD: ZERO, DRINKS: ZERO})
    for row in rows:
        key = tuple(row[field] for field in order_field_map.values())
        dept = row["department"]
        if dept in (FOOD, DRINKS):
            by_key[key][dept] += row["amount"] or ZERO
    return by_key


def _sales_row(bucket, dept, extra):
    food = _q2(dept.get(FOOD, ZERO))
    drinks = _q2(dept.get(DRINKS, ZERO))
    rounding = _q2(bucket["rounding"])
    refunded = _q2(-(bucket["refunded"] or ZERO))
    return {
        **extra,
        "bills": bucket["bills"],
        "gross_food": food,
        "gross_drinks": drinks,
        "refunded": refunded,
        "rounding": rounding,
        "net": _q2(bucket["net"] or ZERO),
    }


def _sum_rows(rows, keys=("bills", "gross_food", "gross_drinks", "refunded", "rounding", "net")):
    totals = {key: ZERO for key in keys}
    totals["bills"] = 0
    for row in rows:
        for key in keys:
            totals[key] += row.get(key) or ZERO
    for key in keys:
        if key != "bills":
            totals[key] = _q2(totals[key])
    totals["bills"] = int(totals["bills"])
    return totals


def daywise(date_from=None, date_to=None):
    """One row per posting_date."""
    qs = submitted_orders(date_from, date_to)
    dept = _dept_totals(qs, {"posting_date": "order__posting_date"})
    rows = []
    for bucket in _order_buckets(qs, ("posting_date",)):
        key = (bucket["posting_date"],)
        rows.append(_sales_row(bucket, dept[key], {"posting_date": bucket["posting_date"]}))
    return rows, _sum_rows(rows)


def monthwise(date_from=None, date_to=None):
    """One row per calendar month."""
    qs = submitted_orders(date_from, date_to).annotate(
        year=ExtractYear("posting_date"),
        month=ExtractMonth("posting_date"),
    )
    item_qs = OrderItem.objects.filter(order__in=submitted_orders(date_from, date_to)).annotate(
        year=ExtractYear("order__posting_date"),
        month=ExtractMonth("order__posting_date"),
    )
    dept_rows = item_qs.values("year", "month", "department").annotate(amount=Coalesce(Sum("amount"), ZERO))
    dept = defaultdict(lambda: {FOOD: ZERO, DRINKS: ZERO})
    for row in dept_rows:
        if row["department"] in (FOOD, DRINKS):
            dept[(row["year"], row["month"])][row["department"]] += row["amount"] or ZERO
    rows = []
    for bucket in _order_buckets(qs, ("year", "month")):
        year, month = int(bucket["year"]), int(bucket["month"])
        period = date(year, month, 1)
        rows.append(
            _sales_row(
                bucket,
                dept[(year, month)],
                {
                    "year": year,
                    "month": month,
                    "period": period,
                    "period_end": date(year, month, calendar.monthrange(year, month)[1]),
                },
            )
        )
    return rows, _sum_rows(rows)


def cancelled_invoices(date_from=None, date_to=None, *, reason=None):
    """One row per CANCELLED order. Returns never appear."""
    qs = Order.objects.filter(status=CANCELLED, is_return=False).select_related("cashier")
    if date_from:
        qs = qs.filter(posting_date__gte=date_from)
    if date_to:
        qs = qs.filter(posting_date__lte=date_to)
    if reason:
        qs = qs.filter(cancel_reason=reason)
    qs = qs.order_by("posting_date", "pk")
    rows = [
        {
            "pk": order.pk,
            "invoice_number": order.invoice_number or f"#{order.pk}",
            "posting_date": order.posting_date,
            "cashier_name": order.cashier.get_display_name() if order.cashier_id else "—",
            "order_type": order.get_order_type_display(),
            "total": _q2(order.grand_total),
            "reason": order.get_cancel_reason_display(),
            "note": order.cancel_reason_note,
        }
        for order in qs
    ]
    totals = {
        "bills": len(rows),
        "lost_sales": _q2(sum((row["total"] for row in rows), ZERO)),
    }
    return rows, totals


def average_bill(date_from=None, date_to=None, *, grouping="day"):
    """Net sales / bill count per day or month, plus overall."""
    qs = submitted_orders(date_from, date_to)
    if grouping == "month":
        qs = qs.annotate(year=ExtractYear("posting_date"), month=ExtractMonth("posting_date"))
        buckets = (
            qs.values("year", "month")
            .annotate(
                bills=Count("pk"),
                net=Coalesce(Sum("rounded_total"), ZERO),
            )
            .order_by("year", "month")
        )
        rows = []
        for bucket in buckets:
            bills = bucket["bills"]
            net = _q2(bucket["net"])
            year, month = int(bucket["year"]), int(bucket["month"])
            period = date(year, month, 1)
            rows.append(
                {
                    "period": period,
                    "period_end": date(year, month, calendar.monthrange(year, month)[1]),
                    "bills": bills,
                    "net": net,
                    "average": _q2(net / bills) if bills else ZERO,
                }
            )
    else:
        buckets = (
            qs.values("posting_date")
            .annotate(
                bills=Count("pk"),
                net=Coalesce(Sum("rounded_total"), ZERO),
            )
            .order_by("posting_date")
        )
        rows = []
        for bucket in buckets:
            bills = bucket["bills"]
            net = _q2(bucket["net"])
            rows.append(
                {
                    "period": bucket["posting_date"],
                    "bills": bills,
                    "net": net,
                    "average": _q2(net / bills) if bills else ZERO,
                }
            )
    totals = _sum_rows(rows, keys=("bills", "net"))
    totals["average"] = _q2(totals["net"] / totals["bills"]) if totals["bills"] else ZERO
    return rows, totals
