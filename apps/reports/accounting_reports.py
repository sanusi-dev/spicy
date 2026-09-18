"""Query-based GL, trial balance, and simple P&L reports."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.urls import reverse

from apps.accounting.models import GLEntry, LedgerAccount
from apps.orders.models import Order
from apps.settings.models import ProductionUnit, Restaurant
from apps.utils.rounding import money

from .models import DRINKS, FOOD

ZERO = Decimal("0")


def _q2(value):
    return money(value or ZERO)


def _gl_queryset(*, fiscal_year=None, date_from=None, date_to=None, account_id=None):
    qs = GLEntry.objects.select_related("account", "fiscal_year")
    if fiscal_year is not None:
        qs = qs.filter(fiscal_year=fiscal_year)
    if date_from:
        qs = qs.filter(posting_date__gte=date_from)
    if date_to:
        qs = qs.filter(posting_date__lte=date_to)
    if account_id:
        qs = qs.filter(account_id=account_id)
    return qs


def _shift_cash_out_url(voucher_no):
    from apps.staff.models import ShiftCashOut

    cash_out = ShiftCashOut.objects.filter(pk=int(voucher_no)).only("opening_entry_id").first()
    if cash_out is None:
        return None
    return reverse("staff:opening_entry_detail", args=[cash_out.opening_entry_id])


def voucher_url(voucher_type, voucher_no):
    """Return the backoffice URL for a GL voucher, or None."""
    if not voucher_type or not voucher_no:
        return None
    try:
        if voucher_type == "Order":
            order = Order.objects.filter(invoice_number=voucher_no).only("pk").first()
            return reverse("orders:order_detail", args=[order.pk]) if order else None
        if voucher_type == "Journal Entry":
            return reverse("accounting:journal_entry_detail", args=[int(voucher_no)])
        if voucher_type == "Supplier Invoice":
            from apps.accounting.payables_models import SupplierInvoice

            invoice = SupplierInvoice.objects.filter(invoice_number=voucher_no).only("pk").first()
            return reverse("accounting:supplier_invoice_detail", args=[invoice.pk]) if invoice else None
        if voucher_type == "Supplier Payment":
            from apps.accounting.payables_models import SupplierPayment

            payment = SupplierPayment.objects.filter(payment_number=voucher_no).only("pk").first()
            return reverse("accounting:supplier_payment_detail", args=[payment.pk]) if payment else None
        if voucher_type == "Purchase Receipt":
            return reverse("inventory:purchase_receipt_detail", args=[int(voucher_no)])
        if voucher_type == "Stock Entry":
            return reverse("inventory:stock_entry_detail", args=[int(voucher_no)])
        if voucher_type == "Stock Reconciliation":
            return reverse("inventory:reconciliation_detail", args=[int(voucher_no)])
        if voucher_type == "Shift Cash-Out":
            return _shift_cash_out_url(voucher_no)
    except TypeError, ValueError:
        return None
    return None


def _attach_voucher_urls(rows):
    """Bulk-resolve voucher links for GL rows."""
    by_type = defaultdict(set)
    for row in rows:
        if row.get("voucher_no"):
            by_type[row["voucher_type"]].add(row["voucher_no"])
    urls = {}
    order_nos = by_type.get("Order") or set()
    if order_nos:
        for pk, invoice in Order.objects.filter(invoice_number__in=order_nos).values_list("pk", "invoice_number"):
            urls[("Order", invoice)] = reverse("orders:order_detail", args=[pk])
    for no in by_type.get("Journal Entry") or set():
        try:
            urls[("Journal Entry", no)] = reverse("accounting:journal_entry_detail", args=[int(no)])
        except TypeError, ValueError:
            continue
    if by_type.get("Supplier Invoice"):
        from apps.accounting.payables_models import SupplierInvoice

        for pk, number in SupplierInvoice.objects.filter(invoice_number__in=by_type["Supplier Invoice"]).values_list(
            "pk", "invoice_number"
        ):
            urls[("Supplier Invoice", number)] = reverse("accounting:supplier_invoice_detail", args=[pk])
    if by_type.get("Supplier Payment"):
        from apps.accounting.payables_models import SupplierPayment

        for pk, number in SupplierPayment.objects.filter(payment_number__in=by_type["Supplier Payment"]).values_list(
            "pk", "payment_number"
        ):
            urls[("Supplier Payment", number)] = reverse("accounting:supplier_payment_detail", args=[pk])
    for voucher_type, url_name in (
        ("Purchase Receipt", "inventory:purchase_receipt_detail"),
        ("Stock Entry", "inventory:stock_entry_detail"),
        ("Stock Reconciliation", "inventory:reconciliation_detail"),
    ):
        for no in by_type.get(voucher_type) or set():
            try:
                urls[(voucher_type, no)] = reverse(url_name, args=[int(no)])
            except TypeError, ValueError:
                continue
    cash_out_nos = by_type.get("Shift Cash-Out") or set()
    if cash_out_nos:
        from apps.staff.models import ShiftCashOut

        pks = []
        for no in cash_out_nos:
            try:
                pks.append(int(no))
            except TypeError, ValueError:
                continue
        for pk, opening_id in ShiftCashOut.objects.filter(pk__in=pks).values_list("pk", "opening_entry_id"):
            urls[("Shift Cash-Out", str(pk))] = reverse("staff:opening_entry_detail", args=[opening_id])
    for row in rows:
        row["voucher_url"] = urls.get((row["voucher_type"], row["voucher_no"]))
    return rows


def _brought_forward(*, fiscal_year, date_from, account_id):
    if not account_id or not date_from:
        return ZERO
    prior = GLEntry.objects.filter(account_id=account_id, posting_date__lt=date_from)
    if fiscal_year is not None:
        prior = prior.filter(fiscal_year=fiscal_year, posting_date__gte=fiscal_year.year_start_date)
    totals = prior.aggregate(debit=Coalesce(Sum("debit"), ZERO), credit=Coalesce(Sum("credit"), ZERO))
    return _q2(totals["debit"] - totals["credit"])


def gl_report(*, fiscal_year=None, date_from=None, date_to=None, account_id=None):
    """Chronological GL rows. Running balance is per-account and includes brought-forward."""
    qs = _gl_queryset(fiscal_year=fiscal_year, date_from=date_from, date_to=date_to, account_id=account_id).order_by(
        "posting_date", "pk"
    )
    per_account = bool(account_id)
    running = (
        _brought_forward(fiscal_year=fiscal_year, date_from=date_from, account_id=account_id) if per_account else None
    )
    rows = []
    if per_account and date_from is not None:
        account = LedgerAccount.objects.filter(pk=account_id).only("name").first()
        rows.append(
            {
                "pk": None,
                "posting_date": date_from,
                "account_name": account.name if account else "",
                "debit": ZERO,
                "credit": ZERO,
                "running_balance": running,
                "voucher_type": "Brought forward",
                "voucher_no": "",
                "is_cancelled": False,
                "is_brought_forward": True,
            }
        )
    for entry in qs.iterator():
        if per_account:
            running = _q2(running + entry.debit - entry.credit)
        rows.append(
            {
                "pk": entry.pk,
                "posting_date": entry.posting_date,
                "account_name": entry.account.name,
                "debit": _q2(entry.debit),
                "credit": _q2(entry.credit),
                "running_balance": running,
                "voucher_type": entry.voucher_type,
                "voucher_no": entry.voucher_no,
                "is_cancelled": entry.is_cancelled,
                "is_brought_forward": False,
            }
        )
    _attach_voucher_urls(rows)
    period_rows = [row for row in rows if not row.get("is_brought_forward")]
    totals = {
        "debit": _q2(sum((row["debit"] for row in period_rows), ZERO)),
        "credit": _q2(sum((row["credit"] for row in period_rows), ZERO)),
        "running_balance": running,
    }
    return rows, totals


def trial_balance(*, fiscal_year, date_to=None):
    """Leaf accounts with a non-zero balance, grouped by account type. Opening entries included."""
    qs = GLEntry.objects.filter(fiscal_year=fiscal_year, account__is_group=False)
    if date_to:
        qs = qs.filter(posting_date__lte=date_to)
    qs = qs.filter(posting_date__gte=fiscal_year.year_start_date)
    buckets = (
        qs.values("account_id", "account__name", "account__account_type")
        .annotate(
            debit=Coalesce(Sum("debit"), ZERO),
            credit=Coalesce(Sum("credit"), ZERO),
        )
        .order_by("account__account_type", "account__name")
    )
    by_type = defaultdict(list)
    for bucket in buckets:
        debit = _q2(bucket["debit"])
        credit = _q2(bucket["credit"])
        balance = _q2(debit - credit)
        if balance == ZERO:
            continue
        by_type[bucket["account__account_type"]].append(
            {
                "account_id": bucket["account_id"],
                "account_name": bucket["account__name"],
                "account_type": bucket["account__account_type"],
                "debit": debit,
                "credit": credit,
                "balance": balance,
            }
        )
    type_order = [
        LedgerAccount.ASSET,
        LedgerAccount.LIABILITY,
        LedgerAccount.EQUITY,
        LedgerAccount.INCOME,
        LedgerAccount.EXPENSE,
    ]
    labels = dict(LedgerAccount.ACCOUNT_TYPE_CHOICES)
    totals = {"debit": ZERO, "credit": ZERO, "balance": ZERO}
    groups = []
    for account_type in type_order:
        rows = by_type.get(account_type) or []
        if not rows:
            continue
        group_totals = {
            "debit": _q2(sum((row["debit"] for row in rows), ZERO)),
            "credit": _q2(sum((row["credit"] for row in rows), ZERO)),
            "balance": _q2(sum((row["balance"] for row in rows), ZERO)),
        }
        groups.append(
            {
                "account_type": account_type,
                "label": labels.get(account_type, account_type),
                "rows": rows,
                "totals": group_totals,
            }
        )
        totals["debit"] += group_totals["debit"]
        totals["credit"] += group_totals["credit"]
        totals["balance"] += group_totals["balance"]
    totals = {key: _q2(value) for key, value in totals.items()}
    return groups, totals


def _sales_account_ids():
    """Map income accounts to FOOD/DRINKS using production units, then the restaurant default."""
    mapping = {}
    mapped_depts = set()
    for unit in ProductionUnit.objects.filter(income_account_id__isnull=False).only("department", "income_account_id"):
        if unit.department in (FOOD, DRINKS):
            mapping[unit.income_account_id] = unit.department
            mapped_depts.add(unit.department)
    missing = {FOOD, DRINKS} - mapped_depts
    if len(missing) == 1:
        restaurant = Restaurant.load()
        default_id = restaurant.default_income_account_id if restaurant else None
        if default_id and default_id not in mapping:
            mapping[default_id] = next(iter(missing))
    return mapping


def simple_pnl(*, fiscal_year=None, date_from=None, date_to=None):
    """P&L over report_type=PROFIT_AND_LOSS. Cancelled originals and reversals net. No typed costs."""
    qs = GLEntry.objects.filter(account__report_type=LedgerAccount.PROFIT_AND_LOSS, account__is_group=False)
    if fiscal_year is not None:
        qs = qs.filter(fiscal_year=fiscal_year)
    if date_from:
        qs = qs.filter(posting_date__gte=date_from)
    if date_to:
        qs = qs.filter(posting_date__lte=date_to)
    buckets = (
        qs.values("account_id", "account__name", "account__account_type")
        .annotate(
            debit=Coalesce(Sum("debit"), ZERO),
            credit=Coalesce(Sum("credit"), ZERO),
        )
        .order_by("account__account_type", "account__name")
    )
    sales_accounts = _sales_account_ids()
    income_rows = []
    expense_rows = []
    food_sales = ZERO
    drinks_sales = ZERO
    for bucket in buckets:
        debit = _q2(bucket["debit"])
        credit = _q2(bucket["credit"])
        account_type = bucket["account__account_type"]
        if account_type == LedgerAccount.INCOME:
            amount = _q2(credit - debit)
            if amount == ZERO:
                continue
            dept = sales_accounts.get(bucket["account_id"])
            if dept == FOOD:
                food_sales += amount
            elif dept == DRINKS:
                drinks_sales += amount
            income_rows.append(
                {
                    "account_id": bucket["account_id"],
                    "account_name": bucket["account__name"],
                    "amount": amount,
                    "department": dept,
                }
            )
        else:
            amount = _q2(debit - credit)
            if amount == ZERO:
                continue
            expense_rows.append(
                {
                    "account_id": bucket["account_id"],
                    "account_name": bucket["account__name"],
                    "amount": amount,
                }
            )
    total_income = _q2(sum((row["amount"] for row in income_rows), ZERO))
    total_expenses = _q2(sum((row["amount"] for row in expense_rows), ZERO))
    return {
        "income_rows": income_rows,
        "expense_rows": expense_rows,
        "food_sales": _q2(food_sales),
        "drinks_sales": _q2(drinks_sales),
        "total_income": total_income,
        "gross_profit": total_income,
        "total_expenses": total_expenses,
        "net_profit": _q2(total_income - total_expenses),
    }
