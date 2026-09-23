"""Source queries for Daily P&L: orders, stock, variance, meter, templates."""

import calendar
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from apps.inventory.models import StockLedgerEntry
from apps.orders.models import SUBMITTED, Order, OrderItem
from apps.orders.services import settle_time_rate
from apps.staff.models import POSClosingEntry
from apps.utils.rounding import money

from .models import DRINKS, FOOD, DailyPnLCogsRow, PnLRecurringExpense

ZERO = Decimal("0")


def business_day_window(business_date, start_hour):
    """Return aware [start, end) covering business_date at start_hour."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(business_date, datetime.min.time().replace(hour=start_hour)), tz)
    return start, start + timedelta(days=1)


def order_datetime(order):
    tz = timezone.get_current_timezone()
    naive = datetime.combine(order.posting_date, order.posting_time)
    if timezone.is_naive(naive):
        return timezone.make_aware(naive, tz)
    return naive


def orders_in_window(start, end):
    dates = {start.date(), (end - timedelta(microseconds=1)).date()}
    dates.add(min(dates) - timedelta(days=1))
    orders = list(Order.objects.filter(status=SUBMITTED, posting_date__in=dates))
    return [o for o in orders if start <= order_datetime(o) < end]


def sales_by_department(orders):
    """Food and drinks gross from line amounts; NULL department snapshots fall back to the item's department."""
    if not orders:
        return ZERO, ZERO
    rows = (
        OrderItem.objects.filter(order_id__in=[o.pk for o in orders])
        .values("department", "item__department")
        .annotate(total=Sum("amount"))
    )
    by_dept = {}
    for row in rows:
        department = row["department"] or row["item__department"]
        by_dept[department] = by_dept.get(department, ZERO) + (row["total"] or ZERO)
    return by_dept.get(FOOD, ZERO), by_dept.get(DRINKS, ZERO)


def round_off(orders):
    return money(sum((o.rounding_adjustment for o in orders), ZERO))


def drink_cogs(start, end, orders):
    del start, end
    rows = []
    total = ZERO
    sale_orders = {str(o.pk) for o in orders if not o.is_return}
    return_orders = {str(o.pk) for o in orders if o.is_return}
    if sale_orders:
        sales = StockLedgerEntry.objects.filter(
            voucher_no__in=sale_orders,
            voucher_type="POS Order",
            quantity__lt=0,
            item__department=DRINKS,
        ).select_related("item")
        for sle in sales:
            qty = abs(sle.quantity)
            amount = money(abs(sle.stock_value_change))
            total += amount
            rows.append(
                {
                    "item_name": sle.item.item_name,
                    "qty": qty,
                    "rate": sle.unit_rate,
                    "amount": amount,
                    "kind": DailyPnLCogsRow.SALE,
                }
            )
    if return_orders:
        returns = StockLedgerEntry.objects.filter(
            voucher_no__in=return_orders,
            voucher_type="POS Return",
            quantity__gt=0,
            item__department=DRINKS,
        ).select_related("item")
        for sle in returns:
            qty = sle.quantity
            amount = money(sle.stock_value_change)
            total -= amount
            rows.append(
                {
                    "item_name": sle.item.item_name,
                    "qty": qty,
                    "rate": sle.unit_rate,
                    "amount": -amount,
                    "kind": DailyPnLCogsRow.RETURN,
                }
            )
    for order in orders:
        if not order.is_return:
            continue
        for line in order.items.select_related("item").filter(not_restockable=True, department=DRINKS):
            rate = settle_time_rate(order.return_against, line.item)
            qty = abs(line.qty)
            amount = money(qty * rate)
            item_name = line.item_name or line.item.item_name
            # Reverse the sale's cost, then re-add it as wastage: the bottle stays
            # costed once, it only changes label.
            total -= amount
            rows.append(
                {
                    "item_name": item_name,
                    "qty": qty,
                    "rate": rate,
                    "amount": -amount,
                    "kind": DailyPnLCogsRow.RETURN,
                }
            )
            total += amount
            rows.append(
                {
                    "item_name": item_name,
                    "qty": qty,
                    "rate": rate,
                    "amount": amount,
                    "kind": DailyPnLCogsRow.WASTAGE,
                }
            )
    return money(total), rows


def cash_variance(start, end, include):
    if not include:
        return ZERO
    closings = POSClosingEntry.objects.filter(
        status=POSClosingEntry.SUBMITTED,
        period_end_date__gte=start,
        period_end_date__lt=end,
    )
    native = sum((c.total_short_excess for c in closings), ZERO)
    return money(-native)


def electricity(pnl, rate):
    if pnl.electricity_opening is None and pnl.electricity_closing is None:
        return ZERO
    if pnl.electricity_opening is None or pnl.electricity_closing is None:
        raise ValidationError("Enter both electricity readings, or leave both blank.")
    if rate <= 0:
        raise ValidationError("Set the electricity rate in P&L settings.")
    units = pnl.electricity_closing - pnl.electricity_opening
    return money(units * rate)


def recurring_amount(expense, business_date, gross_sales):
    if expense.kind in {
        PnLRecurringExpense.DIRECT_DAILY,
        PnLRecurringExpense.INDIRECT_DAILY,
        PnLRecurringExpense.EMPLOYEE_DAILY,
    }:
        return money(expense.amount)
    if expense.kind in {PnLRecurringExpense.INDIRECT_MONTHLY, PnLRecurringExpense.EMPLOYEE_MONTHLY}:
        days = calendar.monthrange(business_date.year, business_date.month)[1]
        daily = money(expense.amount / Decimal(days))
        if business_date.day == days:
            # The last day absorbs the rounding remainder so the month sums exactly.
            return money(expense.amount - daily * (days - 1))
        return daily
    return money((expense.percent / Decimal("100")) * gross_sales)
