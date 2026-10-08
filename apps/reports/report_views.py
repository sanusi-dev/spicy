"""Query-report views. Manager/Admin only. GET filters, no writes."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.users.decorators import manager_required

from . import accounting_reports, register_reports, report_filters, sales_breakdown_reports, sales_reports


def _month_range(request):
    default_from, default_to = report_filters.month_defaults()
    return report_filters.date_range(request, default_from=default_from, default_to=default_to)


def _page(request, template, title, description, date_from, date_to, **extra):
    return render(
        request,
        template,
        report_filters.report_context(
            title=title,
            description=description,
            date_from=date_from,
            date_to=date_to,
            extra=extra,
        ),
    )


@manager_required
def sales_today(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    date_from, date_to = report_filters.date_range(request, default_from=today, default_to=today)
    rows, totals = sales_reports.daywise(date_from, date_to)
    return _page(
        request,
        "backoffice/reports/sales_period.html",
        "Today",
        "Submitted sales for the calendar posting date, not the Daily P&L business day.",
        date_from,
        date_to,
        rows=rows,
        totals=totals,
        period_kind="day",
    )


@manager_required
def sales_daywise(request: HttpRequest) -> HttpResponse:
    date_from, date_to = _month_range(request)
    rows, totals = sales_reports.daywise(date_from, date_to)
    return _page(
        request,
        "backoffice/reports/sales_period.html",
        "Daywise sales",
        "One row per calendar posting date. Returns net on the return date.",
        date_from,
        date_to,
        rows=rows,
        totals=totals,
        period_kind="day",
    )


@manager_required
def sales_monthwise(request: HttpRequest) -> HttpResponse:
    fy, date_from, date_to = report_filters.monthwise_period(request)
    rows, totals = sales_reports.monthwise(date_from, date_to)
    return _page(
        request,
        "backoffice/reports/sales_period.html",
        "Monthwise sales",
        "One row per calendar month. Choose a fiscal year or a from/to range.",
        date_from,
        date_to,
        rows=rows,
        totals=totals,
        period_kind="month",
        fiscal_year=fy,
        show_fiscal_year=True,
    )


@manager_required
def sales_itemwise(request: HttpRequest) -> HttpResponse:
    date_from, date_to = _month_range(request)
    department = request.GET.get("department") or None
    item_group_id = report_filters.int_param(request, "item_group")
    rows, totals = sales_breakdown_reports.itemwise(
        date_from, date_to, department=department, item_group_id=item_group_id
    )
    return _page(
        request,
        "backoffice/reports/sales_items.html",
        "Item-wise sales",
        "Quantity, gross, refunded, and net per item. Returns net on the return date.",
        date_from,
        date_to,
        rows=rows,
        totals=totals,
        department=department or "",
        item_group_id=str(item_group_id or ""),
        show_department=True,
        show_item_group=True,
    )


@manager_required
def sales_employeewise(request: HttpRequest) -> HttpResponse:
    date_from, date_to = _month_range(request)
    rows, totals = sales_breakdown_reports.employeewise(date_from, date_to)
    return _page(
        request,
        "backoffice/reports/sales_employees.html",
        "Employee-wise sales",
        "Submitted bills and net sales per cashier. Unset cashier is a blank row.",
        date_from,
        date_to,
        rows=rows,
        totals=totals,
    )


@manager_required
def sales_servicewise(request: HttpRequest) -> HttpResponse:
    date_from, date_to = _month_range(request)
    rows, totals = sales_breakdown_reports.servicewise(date_from, date_to)
    return _page(
        request,
        "backoffice/reports/sales_service.html",
        "Service-wise sales",
        "Dine-in and take-away bills with net sales by department.",
        date_from,
        date_to,
        rows=rows,
        totals=totals,
    )


@manager_required
def sales_timewise(request: HttpRequest) -> HttpResponse:
    date_from, date_to = _month_range(request)
    rows, totals = sales_breakdown_reports.timewise(date_from, date_to)
    return _page(
        request,
        "backoffice/reports/sales_time.html",
        "Time-wise sales",
        "Twenty-four hourly buckets from the order posting time.",
        date_from,
        date_to,
        rows=rows,
        totals=totals,
    )


@manager_required
def sales_cancelled(request: HttpRequest) -> HttpResponse:
    date_from, date_to = _month_range(request)
    reason = request.GET.get("reason") or None
    rows, totals = sales_reports.cancelled_invoices(date_from, date_to, reason=reason)
    return _page(
        request,
        "backoffice/reports/sales_cancelled.html",
        "Cancelled invoices",
        "Cancelled orders only. Returns never appear. Totals are lost sales.",
        date_from,
        date_to,
        rows=rows,
        totals=totals,
        reason=reason or "",
        show_reason=True,
    )


@manager_required
def sales_average_bill(request: HttpRequest) -> HttpResponse:
    date_from, date_to = _month_range(request)
    grouping = request.GET.get("grouping") or "day"
    if grouping not in {"day", "month"}:
        grouping = "day"
    rows, totals = sales_reports.average_bill(date_from, date_to, grouping=grouping)
    return _page(
        request,
        "backoffice/reports/sales_average_bill.html",
        "Average bill value",
        "Net sales / bill count. Returns net the numerator and count in the denominator.",
        date_from,
        date_to,
        rows=rows,
        totals=totals,
        grouping=grouping,
        show_grouping=True,
    )


@manager_required
def pos_register(request: HttpRequest) -> HttpResponse:
    date_from, date_to = _month_range(request)
    cashier_id = report_filters.int_param(request, "cashier")
    rows, totals = register_reports.pos_register(date_from, date_to, cashier_id=cashier_id)
    return _page(
        request,
        "backoffice/reports/pos_register.html",
        "POS register",
        "Submitted shift closes. Expected amounts already include refund and change netting.",
        date_from,
        date_to,
        rows=rows,
        totals=totals,
        cashier_id=str(cashier_id or ""),
        show_cashier=True,
    )


@manager_required
def gl_report(request: HttpRequest) -> HttpResponse:
    fy = report_filters.fiscal_year_from_request(request)
    date_from, date_to = report_filters.accounting_dates(request, fy)
    account_id = report_filters.int_param(request, "account")
    rows, totals = accounting_reports.gl_report(
        fiscal_year=fy, date_from=date_from, date_to=date_to, account_id=account_id
    )
    return _page(
        request,
        "backoffice/reports/gl_report.html",
        "General ledger",
        "Chronological postings. Running balance is shown when one account is selected.",
        date_from,
        date_to,
        rows=rows,
        totals=totals,
        fiscal_year=fy,
        show_fiscal_year=True,
        show_account=True,
        account_id=str(account_id or ""),
        show_running_balance=bool(account_id),
    )


@manager_required
def trial_balance(request: HttpRequest) -> HttpResponse:
    fy = report_filters.fiscal_year_from_request(request)
    date_to = report_filters.trial_balance_as_of(request, fy)
    groups, totals = ([], {"debit": 0, "credit": 0, "balance": 0})
    if fy:
        groups, totals = accounting_reports.trial_balance(fiscal_year=fy, date_to=date_to)
    return _page(
        request,
        "backoffice/reports/trial_balance.html",
        "Trial balance",
        "Cumulative leaf-account balances to the as-of date, including opening entries.",
        None,
        date_to,
        groups=groups,
        totals=totals,
        fiscal_year=fy,
        show_fiscal_year=True,
        as_of=True,
    )


@manager_required
def simple_pnl(request: HttpRequest) -> HttpResponse:
    fy = report_filters.fiscal_year_from_request(request)
    date_from, date_to = report_filters.accounting_dates(request, fy)
    statement = accounting_reports.simple_pnl(fiscal_year=fy, date_from=date_from, date_to=date_to)
    return _page(
        request,
        "backoffice/reports/simple_pnl.html",
        "Profit & loss",
        "Income minus expense from the general ledger. Food and drinks split by income account.",
        date_from,
        date_to,
        statement=statement,
        fiscal_year=fy,
        show_fiscal_year=True,
    )
