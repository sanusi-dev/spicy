"""GET filter parsing and shared report page context."""

from datetime import date

from django.utils import timezone

from apps.accounting.models import FiscalYear, LedgerAccount
from apps.inventory.models import ItemGroup
from apps.orders.models import CANCEL_REASON_CHOICES

from . import register_reports
from .models import DEPARTMENT_CHOICES


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def date_range(request, *, default_from=None, default_to=None):
    date_from = parse_date(request.GET.get("from")) if "from" in request.GET else default_from
    date_to = parse_date(request.GET.get("to")) if "to" in request.GET else default_to
    return date_from, date_to


def month_defaults():
    today = timezone.localdate()
    return today.replace(day=1), today


def current_fiscal_year():
    today = timezone.localdate()
    return FiscalYear.objects.filter(disabled=False, year_start_date__lte=today, year_end_date__gte=today).first()


def fiscal_year_from_request(request, *, fallback=True):
    """Return the requested fiscal year, falling back to the current one."""
    raw = request.GET.get("fiscal_year")
    if raw:
        try:
            return FiscalYear.objects.filter(pk=int(raw)).first()
        except TypeError, ValueError:
            return None
    return current_fiscal_year() if fallback else None


def fiscal_years():
    return FiscalYear.objects.order_by("-year_start_date")


def int_param(request, name):
    raw = request.GET.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except TypeError, ValueError:
        return None


def _clamp_to_year(date_from, date_to, fy):
    lo = date_from if date_from else fy.year_start_date
    hi = date_to if date_to else fy.year_end_date
    lo = max(lo, fy.year_start_date)
    hi = min(hi, fy.year_end_date)
    return lo, hi


def accounting_dates(request, fy):
    """from/to inside the fiscal year. Stale dates vs a newly selected year reset to its bounds."""
    default_from = fy.year_start_date if fy else None
    default_to = timezone.localdate()
    if fy and default_to > fy.year_end_date:
        default_to = fy.year_end_date
    date_from, date_to = date_range(request, default_from=default_from, default_to=default_to)
    if fy is None:
        return date_from, date_to
    lo, hi = _clamp_to_year(date_from, date_to, fy)
    if lo > hi:
        return fy.year_start_date, fy.year_end_date
    return lo, hi


def monthwise_period(request):
    """Fiscal year or from/to. Custom dates win; a year switch with leftover dates uses the year."""
    fy = None
    if request.GET.get("fiscal_year"):
        fy = FiscalYear.objects.filter(pk=int_param(request, "fiscal_year")).first()
    elif "from" not in request.GET:
        fy = current_fiscal_year()
    date_from, date_to = date_range(request)
    if fy is None:
        return None, date_from, date_to
    fy_from, fy_to = fy.year_start_date, fy.year_end_date
    if date_from is None and date_to is None:
        return fy, fy_from, fy_to
    if date_from == fy_from and date_to == fy_to:
        return fy, fy_from, fy_to
    lo, hi = _clamp_to_year(date_from, date_to, fy)
    if lo > hi:
        return fy, fy_from, fy_to
    return None, date_from, date_to


def trial_balance_as_of(request, fy):
    date_to = parse_date(request.GET.get("to")) if "to" in request.GET else timezone.localdate()
    if fy and date_to and (date_to > fy.year_end_date or date_to < fy.year_start_date):
        date_to = fy.year_end_date
    return date_to


def report_context(*, title, description, date_from, date_to, extra=None):
    extra = extra or {}
    context = {
        "title": title,
        "description": description,
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
        "fiscal_year": extra.get("fiscal_year"),
    }
    if extra.get("show_fiscal_year"):
        context["fiscal_years"] = fiscal_years()
    if extra.get("show_department"):
        context["departments"] = DEPARTMENT_CHOICES
    if extra.get("show_item_group"):
        context["item_groups"] = ItemGroup.objects.order_by("name")
    if extra.get("show_reason"):
        context["cancel_reasons"] = CANCEL_REASON_CHOICES
    if extra.get("show_cashier"):
        context["cashiers"] = register_reports.register_cashiers()
    if extra.get("show_account"):
        context["accounts"] = LedgerAccount.objects.filter(is_group=False).order_by("name")
    context.update(extra)
    return context
