"""Daily P&L backoffice views. Manager/Admin only."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.users.decorators import manager_required
from apps.utils.csv_export import export_filename, money_text, over_row_cap, stream_csv, text
from apps.utils.forms import add_formset_row, remove_formset_row

from .forms import (
    DailyPnLAdHocFormSet,
    DailyPnLForm,
    DailyPnLMaterialQtyFormSet,
    PnLConfigurationForm,
    PnLMaterialFormSet,
    PnLRecurringExpenseFormSet,
)
from .models import DailyPnL, PnLConfiguration, PnLMaterial, PnLRecurringExpense
from .services import compute_daily_pnl


def _seed_material_rows(pnl):
    """Seed qty-0 rows for every active material."""
    existing = set(pnl.material_qtys.values_list("material_id", flat=True))
    for material in PnLMaterial.objects.filter(disabled=False):
        if material.pk not in existing:
            pnl.material_qtys.create(material=material, qty=0)


@manager_required
def pnl_settings(request: HttpRequest) -> HttpResponse:
    config = PnLConfiguration.load()
    form = PnLConfigurationForm(request.POST or None, instance=config)
    materials = PnLMaterialFormSet(request.POST or None, queryset=PnLMaterial.objects.all(), prefix="materials")
    expenses = PnLRecurringExpenseFormSet(
        request.POST or None, queryset=PnLRecurringExpense.objects.all(), prefix="expenses"
    )
    if request.method == "POST" and form.is_valid() and materials.is_valid() and expenses.is_valid():
        with transaction.atomic():
            form.save()
            materials.save()
            expenses.save()
        messages.success(request, "P&L settings saved.")
        return redirect("reports:pnl_settings")
    return render(
        request,
        "backoffice/reports/pnl_settings.html",
        {"form": form, "materials": materials, "expenses": expenses},
    )


@manager_required
def daily_pnl_list(request: HttpRequest) -> HttpResponse:
    qs = DailyPnL.objects.all()
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    if date_from:
        qs = qs.filter(business_date__gte=date_from)
    if date_to:
        qs = qs.filter(business_date__lte=date_to)
    if request.GET.get("export") == "csv":
        return _daily_pnl_list_csv(qs, status, date_from, date_to)
    return render(
        request,
        "backoffice/reports/daily_pnl_list.html",
        {"entries": qs, "status": status or "", "date_from": date_from or "", "date_to": date_to or ""},
    )


def _daily_pnl_list_csv(qs, status, date_from, date_to):
    """Download the filtered Daily P&L list as CSV — same rows and order as the page."""
    too_many = over_row_cap(qs)
    if too_many is not None:
        return too_many

    def rows():
        for entry in qs.iterator():
            yield [
                entry.business_date.isoformat(),
                text(entry.status),
                money_text(entry.gross_sales_food),
                money_text(entry.gross_sales_drinks),
                money_text(entry.net_sales),
                money_text(entry.gross_profit),
                money_text(entry.net_profit),
            ]

    filename = export_filename(
        "daily-pnl",
        {"status": status, "from": date_from, "to": date_to},
    )
    return stream_csv(
        filename,
        ["Business date", "Status", "Food sales", "Drinks sales", "Net sales", "Gross profit", "Net profit"],
        rows(),
    )


@manager_required
def daily_pnl_create(request: HttpRequest) -> HttpResponse:
    form = DailyPnLForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        pnl = form.save()
        _seed_material_rows(pnl)
        messages.success(request, f"Daily P&L draft for {pnl.business_date} created.")
        return redirect("reports:daily_pnl_update", pk=pnl.pk)
    return render(request, "backoffice/reports/daily_pnl_form.html", {"form": form, "is_create": True})


def _form_context(pnl, form, materials, adhoc, *, preview=None):
    return {
        "form": form,
        "materials": materials,
        "adhoc": adhoc,
        "pnl": pnl,
        "is_create": False,
        "preview": preview,
        "food_usage_counted": preview.food_usage_counted if preview else True,
    }


@manager_required
def daily_pnl_update(request: HttpRequest, pk: int) -> HttpResponse:
    pnl = get_object_or_404(DailyPnL, pk=pk)
    if pnl.status != DailyPnL.DRAFT:
        messages.error(request, "Only draft Daily P&L documents can be edited.")
        return redirect("reports:daily_pnl_detail", pk=pnl.pk)
    form = DailyPnLForm(request.POST or None, instance=pnl)
    materials = DailyPnLMaterialQtyFormSet(request.POST or None, instance=pnl, prefix="materials")
    adhoc = DailyPnLAdHocFormSet(request.POST or None, instance=pnl, prefix="adhoc")
    if request.method == "POST" and form.is_valid() and materials.is_valid() and adhoc.is_valid():
        with transaction.atomic():
            form.save()
            materials.save()
            adhoc.save()
        messages.success(request, "Draft saved.")
        return redirect("reports:daily_pnl_update", pk=pnl.pk)
    preview = None
    if request.method != "POST":
        try:
            preview = compute_daily_pnl(pnl)
        except ValidationError:
            preview = None
    return render(
        request,
        "backoffice/reports/daily_pnl_form.html",
        _form_context(pnl, form, materials, adhoc, preview=preview),
    )


@manager_required
def daily_pnl_detail(request: HttpRequest, pk: int) -> HttpResponse:
    pnl = get_object_or_404(DailyPnL.objects.select_related("amended_from", "submitted_by"), pk=pk)
    return render(
        request,
        "backoffice/reports/daily_pnl_detail.html",
        {
            "pnl": pnl,
            "lines": pnl.lines.all(),
            "cogs_rows": pnl.cogs_rows.all(),
            "consumption_rows": pnl.consumption_rows.all(),
            "theoretical_rows": pnl.theoretical_rows.all(),
            "unmapped_rows": pnl.unmapped_rows.all(),
            "food_usage_counted": pnl.food_usage_counted,
        },
    )


@manager_required
@require_POST
def daily_pnl_preview(request: HttpRequest, pk: int) -> HttpResponse:
    pnl = get_object_or_404(DailyPnL, pk=pk)
    if pnl.status != DailyPnL.DRAFT:
        return HttpResponse("Only drafts can be previewed.", status=400)
    form = DailyPnLForm(request.POST, instance=pnl)
    materials = DailyPnLMaterialQtyFormSet(request.POST, instance=pnl, prefix="materials")
    adhoc = DailyPnLAdHocFormSet(request.POST, instance=pnl, prefix="adhoc")
    if form.is_valid() and materials.is_valid() and adhoc.is_valid():
        with transaction.atomic():
            form.save()
            materials.save()
            adhoc.save()
        pnl.refresh_from_db()
        try:
            preview = compute_daily_pnl(pnl)
        except ValidationError as e:
            return render(
                request,
                "backoffice/reports/_statement.html",
                {"preview_error": e.messages[0] if e.messages else str(e)},
            )
        return render(
            request,
            "backoffice/reports/_statement.html",
            {"preview": preview, "pnl": pnl, "food_usage_counted": preview.food_usage_counted},
        )
    return render(
        request,
        "backoffice/reports/daily_pnl_form.html",
        _form_context(pnl, form, materials, adhoc),
    )


@manager_required
@require_POST
def daily_pnl_submit(request: HttpRequest, pk: int) -> HttpResponse:
    pnl = get_object_or_404(DailyPnL, pk=pk)
    try:
        pnl.submit(actor=request.user)
    except ValidationError as e:
        messages.error(request, e.messages[0] if e.messages else str(e))
        return redirect(
            "reports:daily_pnl_detail" if pnl.status != DailyPnL.DRAFT else "reports:daily_pnl_update", pk=pnl.pk
        )
    messages.success(request, f"Daily P&L for {pnl.business_date} submitted.")
    return redirect("reports:daily_pnl_detail", pk=pnl.pk)


@manager_required
@require_POST
def daily_pnl_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    pnl = get_object_or_404(DailyPnL, pk=pk)
    try:
        pnl.cancel()
    except ValidationError as e:
        messages.error(request, e.messages[0] if e.messages else str(e))
        return redirect("reports:daily_pnl_detail", pk=pnl.pk)
    messages.success(request, f"Daily P&L for {pnl.business_date} cancelled.")
    return redirect("reports:daily_pnl_list")


@manager_required
@require_POST
def daily_pnl_amend(request: HttpRequest, pk: int) -> HttpResponse:
    pnl = get_object_or_404(DailyPnL, pk=pk)
    try:
        copy = pnl.amend()
    except ValidationError as e:
        messages.error(request, e.messages[0] if e.messages else str(e))
        return redirect("reports:daily_pnl_detail", pk=pnl.pk)
    messages.success(request, f"Amendment created — edit the draft for {copy.business_date}.")
    return redirect("reports:daily_pnl_update", pk=copy.pk)


@manager_required
@require_POST
def daily_pnl_material_add(request: HttpRequest, pk: int) -> HttpResponse:
    pnl = get_object_or_404(DailyPnL, pk=pk)
    formset = add_formset_row(DailyPnLMaterialQtyFormSet, "materials", request.POST)
    return render(
        request,
        "backoffice/reports/_material_formset.html",
        {"materials": formset, "pnl": pnl},
    )


@manager_required
@require_POST
def daily_pnl_material_remove(request: HttpRequest, pk: int, index: int) -> HttpResponse:
    pnl = get_object_or_404(DailyPnL, pk=pk)
    formset = remove_formset_row(DailyPnLMaterialQtyFormSet, "materials", request.POST, index)
    return render(
        request,
        "backoffice/reports/_material_formset.html",
        {"materials": formset, "pnl": pnl},
    )


@manager_required
@require_POST
def daily_pnl_adhoc_add(request: HttpRequest, pk: int) -> HttpResponse:
    pnl = get_object_or_404(DailyPnL, pk=pk)
    formset = add_formset_row(DailyPnLAdHocFormSet, "adhoc", request.POST)
    return render(
        request,
        "backoffice/reports/_adhoc_formset.html",
        {"adhoc": formset, "pnl": pnl},
    )


@manager_required
@require_POST
def daily_pnl_adhoc_remove(request: HttpRequest, pk: int, index: int) -> HttpResponse:
    pnl = get_object_or_404(DailyPnL, pk=pk)
    formset = remove_formset_row(DailyPnLAdHocFormSet, "adhoc", request.POST, index)
    return render(
        request,
        "backoffice/reports/_adhoc_formset.html",
        {"adhoc": formset, "pnl": pnl},
    )
