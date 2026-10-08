"""Backoffice shift views — opening and closing entries, cash-outs."""

import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.orders.models import Order
from apps.users.decorators import backoffice_required

from . import services
from .forms import (
    ClosingPaymentForm,
    OpeningFloatForm,
)
from .models import OpeningPayment, POSClosingEntry, POSOpeningEntry

logger = logging.getLogger(__name__)


@backoffice_required
def staff_dashboard(request: HttpRequest) -> HttpResponse:
    """Current shift state and recent closes."""
    open_entry = (
        POSOpeningEntry.objects.filter(
            status=POSOpeningEntry.SUBMITTED,
            closing_entry__isnull=True,
        )
        .select_related("cashier")
        .order_by("-period_start_date")
        .first()
    )
    recent_closes = POSClosingEntry.objects.select_related("cashier", "opening_entry").order_by("-period_end_date")[:5]
    return render(
        request,
        "backoffice/staff/dashboard.html",
        {"open_entry": open_entry, "recent_closes": recent_closes},
    )


@backoffice_required
def opening_entry_list(request: HttpRequest) -> HttpResponse:
    entries = POSOpeningEntry.objects.select_related("cashier", "closing_entry").order_by("-period_start_date")
    return render(request, "backoffice/staff/opening_entry_list.html", {"entries": entries})


@backoffice_required
def opening_entry_create(request: HttpRequest) -> HttpResponse:
    # request.POST is falsy when empty — test the method explicitly.
    user = request.user
    form = OpeningFloatForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        entry = _save_opening_entry(form, user, instance=None)
        if entry is not None:
            messages.success(request, f"Opening entry #{entry.pk} created.")
            return redirect("staff:opening_entry_detail", pk=entry.pk)
    return render(
        request,
        "backoffice/staff/opening_entry_form.html",
        {"form": form, "is_create": True},
    )


@backoffice_required
def opening_entry_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Opening-entry detail page; POST saves inline-edited draft amounts."""
    entry = get_object_or_404(
        POSOpeningEntry.objects.select_related("cashier", "closing_entry"),
        pk=pk,
    )
    opening_payments = list(entry.opening_payments.select_related("mode_of_payment"))
    closing_payments = None
    if entry.closing_entry_id:
        closing_entry = entry.closing_entry
        if closing_entry is not None:
            closing_payments = closing_entry.closing_payments.select_related("mode_of_payment")

    user = request.user
    form: OpeningFloatForm | None
    if request.method == "POST":
        if entry.status != POSOpeningEntry.DRAFT:
            messages.error(request, "Only draft opening entries can be edited.")
            return redirect("staff:opening_entry_detail", pk=entry.pk)
        form = OpeningFloatForm(request.POST)
        if form.is_valid():
            updated = _save_opening_entry(form, user, instance=entry)
            if updated is not None:
                messages.success(request, f"Opening entry #{entry.pk} updated.")
                return redirect("staff:opening_entry_detail", pk=entry.pk)
        return render(
            request,
            "backoffice/staff/opening_entry_detail.html",
            {
                "entry": entry,
                "opening_payments": opening_payments,
                "closing_payments": closing_payments,
                "form": form,
            },
        )

    if entry.status == POSOpeningEntry.DRAFT:
        initial = _entry_to_initial(opening_payments)
        form = OpeningFloatForm(initial=initial)
    else:
        form = None
    return render(
        request,
        "backoffice/staff/opening_entry_detail.html",
        {
            "entry": entry,
            "opening_payments": opening_payments,
            "closing_payments": closing_payments,
            "form": form,
        },
    )


def _entry_to_initial(opening_payments: list[OpeningPayment]) -> dict:
    """Build form-initial data from an existing draft's OpeningPayment rows."""
    initial = {}
    for op in opening_payments:
        initial[OpeningFloatForm._field_name_for(op.mode_of_payment)] = str(op.opening_amount)
    return initial


def _save_opening_entry(form: OpeningFloatForm, cashier, instance: POSOpeningEntry | None) -> POSOpeningEntry | None:
    """Persist a POSOpeningEntry and its OpeningPayment rows from a bound form."""
    from apps.settings.models import Restaurant

    if Restaurant.load() is None:
        form.add_error(None, "Restaurant settings are not configured.")
        return None
    opening_amounts = form.opening_amounts()
    if not opening_amounts:
        form.add_error(
            None,
            "No active payment methods found. Ask a manager to add at least "
            "one payment method in Settings before opening a shift.",
        )
        return None

    with transaction.atomic():
        entry = instance or POSOpeningEntry(cashier=cashier)
        entry.cashier = cashier
        entry.save()
        # Replacing child rows also clears stale rows for modes disabled since open.
        if instance is not None:
            entry.opening_payments.all().delete()
        rows = [
            OpeningPayment(
                opening_entry=entry,
                mode_of_payment=mode,
                opening_amount=amount,
            )
            for mode, amount in opening_amounts.items()
        ]
        OpeningPayment.objects.bulk_create(rows)
        return entry


@backoffice_required
@require_POST
def opening_entry_submit(request: HttpRequest, pk: int) -> HttpResponse:
    entry = get_object_or_404(POSOpeningEntry, pk=pk)
    if entry.status != POSOpeningEntry.DRAFT:
        messages.error(request, "This opening entry is no longer in draft.")
        return redirect("staff:opening_entry_detail", pk=entry.pk)
    # Early feedback only; submit() repeats the one-open-shift check under row lock.
    try:
        entry.full_clean()
    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("staff:opening_entry_detail", pk=entry.pk)
    except Exception:
        logger.exception("Opening entry submission failed", extra={"opening_entry_id": entry.pk})
        messages.error(request, "Cannot submit the opening entry.")
        return redirect("staff:opening_entry_detail", pk=entry.pk)
    entry.submit()
    messages.success(request, f"Shift opened. Entry #{entry.pk} is now Open.")
    return redirect("staff:opening_entry_detail", pk=entry.pk)


@backoffice_required
@require_POST
def opening_entry_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    entry = get_object_or_404(POSOpeningEntry, pk=pk)
    if entry.closing_entry_id is not None:
        messages.error(
            request,
            "Cannot cancel a shift that has already been closed. Cancel the closing entry instead.",
        )
        return redirect("staff:opening_entry_detail", pk=entry.pk)
    try:
        entry.cancel(by_user=request.user)
    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("staff:opening_entry_detail", pk=entry.pk)
    except Exception:
        logger.exception("Opening entry cancellation failed", extra={"opening_entry_id": entry.pk})
        messages.error(request, "Cannot cancel the opening entry.")
        return redirect("staff:opening_entry_detail", pk=entry.pk)
    messages.success(request, f"Opening entry #{entry.pk} cancelled.")
    return redirect("staff:opening_entry_list")


@backoffice_required
def closing_entry_list(request: HttpRequest) -> HttpResponse:
    entries = POSClosingEntry.objects.select_related("cashier", "opening_entry").order_by("-period_end_date")
    return render(request, "backoffice/staff/closing_entry_list.html", {"entries": entries})


@backoffice_required
def closing_entry_create(request: HttpRequest) -> HttpResponse:
    """Auto-create (or reuse) the DRAFT closing entry for the single Open shift."""
    user = request.user
    open_entry = (
        POSOpeningEntry.objects.filter(status=POSOpeningEntry.SUBMITTED, closing_entry__isnull=True)
        .select_related("cashier")
        .order_by("period_start_date")
        .first()
    )
    if open_entry is None:
        messages.warning(request, "There is no open shift to close. Open a shift first.")
        return redirect("staff:dashboard")

    draft_count = Order.objects.open_drafts(open_entry).count()
    if draft_count:
        messages.error(
            request,
            f"Close or settle {draft_count} open order{'s' if draft_count != 1 else ''} before closing the shift.",
        )
        return redirect("pos:pos_home")

    with transaction.atomic():
        # Lock the shift row so two concurrent "Close Shift" clicks can't both pass the duplicate-draft check.
        open_entry = POSOpeningEntry.objects.select_for_update().select_related("cashier").get(pk=open_entry.pk)
        draft_count = Order.objects.open_drafts(open_entry).count()
        if draft_count:
            messages.error(
                request,
                f"Close or settle {draft_count} open order{'s' if draft_count != 1 else ''} before closing the shift.",
            )
            return redirect("pos:pos_home")
        existing_draft = POSClosingEntry.objects.filter(opening_entry=open_entry, status=POSClosingEntry.DRAFT).first()
        if existing_draft is not None:
            return redirect("staff:closing_entry_detail", pk=existing_draft.pk)

        closing = services.ensure_closing_draft(open_entry, user)
    messages.success(
        request,
        f"Closing entry #{closing.pk} started for the open shift. "
        "Count the drawer and enter the closing amounts below.",
    )
    return redirect("staff:closing_entry_detail", pk=closing.pk)


@backoffice_required
def closing_entry_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Show a closing entry's expected-vs-counted reconciliation."""
    closing = get_object_or_404(
        POSClosingEntry.objects.select_related("cashier", "opening_entry").prefetch_related(
            "opening_entry__cash_outs__mode_of_payment", "opening_entry__cash_outs__recorded_by"
        ),
        pk=pk,
    )
    closing_payments = list(closing.closing_payments.select_related("mode_of_payment"))
    draft_count = Order.objects.open_drafts(closing.opening_entry).count()
    cancelled_count = Order.objects.cancelled_in_shift(closing.opening_entry).count()

    if request.method == "POST":
        if closing.status != POSClosingEntry.DRAFT:
            messages.error(request, "Only draft closing entries can be edited.")
            return redirect("staff:closing_entry_detail", pk=closing.pk)
        form_data, all_valid = _validate_closing_payment_forms(request, closing_payments)
        if all_valid:
            if draft_count:
                messages.error(
                    request,
                    f"Close or settle {draft_count} open order{'s' if draft_count != 1 else ''} "
                    "before closing the shift.",
                )
                return redirect("pos:pos_home")
            with transaction.atomic():
                for _cp, form in form_data:
                    form.save()
                closing.variance_note = request.POST.get("variance_note", "").strip()
                closing.save(update_fields=["variance_note", "updated_at"])
            messages.success(request, f"Closing entry #{closing.pk} updated.")
            return redirect("staff:closing_entry_detail", pk=closing.pk)
        return render(
            request,
            "backoffice/staff/closing_entry_detail.html",
            {
                "closing": closing,
                "form_data": form_data,
                "closing_payments": closing_payments,
                "draft_count": draft_count,
            },
        )

    if closing.status == POSClosingEntry.DRAFT:
        form_data = [(cp, ClosingPaymentForm(instance=cp, prefix=f"cp_{cp.pk}")) for cp in closing_payments]
    else:
        form_data = None
    return render(
        request,
        "backoffice/staff/closing_entry_detail.html",
        {
            "closing": closing,
            "form_data": form_data,
            "closing_payments": closing_payments,
            "draft_count": draft_count,
            "cancelled_count": cancelled_count,
        },
    )


def _validate_closing_payment_forms(request, closing_payments):
    """Bind one `ClosingPaymentForm` per row and return (form_data, all_valid)."""
    form_data = []
    all_valid = True
    for cp in closing_payments:
        form = ClosingPaymentForm(request.POST, instance=cp, prefix=f"cp_{cp.pk}")
        if not form.is_valid():
            all_valid = False
        form_data.append((cp, form))
    return form_data, all_valid


@backoffice_required
@require_POST
def closing_entry_submit(request: HttpRequest, pk: int) -> HttpResponse:
    closing = get_object_or_404(POSClosingEntry, pk=pk)
    if closing.status != POSClosingEntry.DRAFT:
        messages.error(request, "This closing entry is no longer in draft.")
        return redirect("staff:closing_entry_detail", pk=closing.pk)
    try:
        closing.full_clean()
        services.submit_closing_entry(closing, actor=request.user)
    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("staff:closing_entry_detail", pk=closing.pk)
    except Exception:
        logger.exception("Closing entry submission failed", extra={"closing_entry_id": closing.pk})
        messages.error(request, "Cannot submit the closing entry.")
        return redirect("staff:closing_entry_detail", pk=closing.pk)
    messages.success(
        request,
        f"Shift closed. Closing entry #{closing.pk} submitted; "
        f"opening entry #{closing.opening_entry_id} marked Closed.",
    )
    return redirect("staff:closing_entry_detail", pk=closing.pk)


@backoffice_required
@require_POST
def closing_entry_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    closing = get_object_or_404(POSClosingEntry, pk=pk)
    try:
        closing.cancel(by_user=request.user)
    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("staff:closing_entry_detail", pk=closing.pk)
    except Exception:
        logger.exception("Closing entry cancellation failed", extra={"closing_entry_id": closing.pk})
        messages.error(request, "Cannot cancel the closing entry.")
        return redirect("staff:closing_entry_detail", pk=closing.pk)
    messages.success(request, f"Closing entry #{closing.pk} cancelled.")
    return redirect("staff:closing_entry_list")
