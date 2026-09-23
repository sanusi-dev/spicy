"""Backoffice views for the orders app — order and KOT management."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import models as django_models
from django.db.models import Count, Prefetch, Q, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.users.decorators import backoffice_required, manager_required
from apps.utils.csv_export import export_filename, money_text, over_row_cap, stream_csv, text

from . import services
from .forms import POSOrderCancelForm
from .models import (
    CANCELLED,
    DRAFT,
    KOT,
    KOT_PRINT_PENDING,
    KOT_PRINT_STATUS_CHOICES,
    KOT_TYPE_CHOICES,
    ORDER_TYPE_CHOICES,
    STATUS_CHOICES,
    SUBMITTED,
    TICKET_TYPE_CHOICES,
    Order,
    OrderItem,
)


@backoffice_required
def orders_dashboard(request: HttpRequest) -> HttpResponse:
    """Render the orders and kitchen/bar ticket backoffice overview."""
    today = timezone.localdate()
    todays_orders = Order.objects.filter(posting_date=today)
    today_summary = todays_orders.aggregate(
        orders=Count("pk"),
        paid=Count("pk", filter=Q(status=SUBMITTED, is_paid=True)),
        cancelled=Count("pk", filter=Q(status=CANCELLED)),
        revenue=Sum("grand_total", filter=Q(status=SUBMITTED, is_paid=True)),
    )
    recent_orders = (
        Order.objects.select_related("cashier")
        .annotate(
            item_count=Count("items", distinct=True),
            ticket_count=Count("kots", distinct=True),
        )
        .order_by("-updated_at")[:8]
    )
    pending_tickets = (
        KOT.objects.select_related("order", "production_unit", "created_by")
        .filter(status=SUBMITTED, print_status=KOT_PRINT_PENDING)
        .annotate(item_count=Count("items", distinct=True))
        .order_by("created_at")[:8]
    )
    context = {
        "today": today,
        "orders_today_count": today_summary["orders"],
        "paid_today_count": today_summary["paid"],
        "draft_count": Order.objects.filter(status=DRAFT).count(),
        "cancelled_today_count": today_summary["cancelled"],
        "today_revenue": today_summary["revenue"] or 0,
        "tickets_today_count": KOT.objects.filter(created_at__date=today).count(),
        "pending_ticket_count": KOT.objects.filter(status=SUBMITTED, print_status=KOT_PRINT_PENDING).count(),
        "recent_orders": recent_orders,
        "pending_tickets": pending_tickets,
    }
    return render(request, "backoffice/orders/dashboard.html", context)


@backoffice_required
def order_list(request: HttpRequest) -> HttpResponse:
    search = request.GET.get("search", "").strip()
    status_filter = request.GET.get("status", "").strip()
    order_type_filter = request.GET.get("order_type", "").strip()
    date_from = parse_date(request.GET.get("from") or "")
    date_to = parse_date(request.GET.get("to") or "")

    orders = Order.objects.select_related("cashier").annotate(
        item_count=Count("items", distinct=True),
        ticket_count=Count("kots", distinct=True),
        pending_ticket_count=Count(
            "kots",
            filter=Q(kots__status=SUBMITTED, kots__print_status=KOT_PRINT_PENDING),
            distinct=True,
        ),
    )
    if search:
        search_query = django_models.Q(invoice_number__icontains=search) | django_models.Q(
            customer_name__icontains=search
        )
        order_number = search.removeprefix("#")
        if order_number.isdigit():
            search_query |= django_models.Q(order_number=int(order_number))
        orders = orders.filter(search_query)
    if status_filter:
        orders = orders.filter(status=status_filter)
    if order_type_filter:
        orders = orders.filter(order_type=order_type_filter)
    if date_from:
        orders = orders.filter(posting_date__gte=date_from)
    if date_to:
        orders = orders.filter(posting_date__lte=date_to)
    orders = orders.order_by("-updated_at")
    if request.GET.get("export") == "csv":
        return _order_list_csv(orders, search, status_filter, order_type_filter, date_from, date_to)
    page_obj = Paginator(orders, 50).get_page(request.GET.get("page") or 1)

    return render(
        request,
        "backoffice/orders/order_list.html",
        {
            "orders": page_obj,
            "page_obj": page_obj,
            "search": search,
            "status_filter": status_filter,
            "order_type_filter": order_type_filter,
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
            "status_choices": STATUS_CHOICES,
            "order_type_choices": ORDER_TYPE_CHOICES,
        },
    )


def _order_list_csv(orders, search, status_filter, order_type_filter, date_from, date_to):
    """Download the filtered order register as CSV — same rows and order as the page."""
    too_many = over_row_cap(orders)
    if too_many is not None:
        return too_many

    def rows():
        for order in orders.iterator():
            cashier = order.cashier.get_display_name() if order.cashier_id else ""
            yield [
                text(order.invoice_number),
                text(order.order_number),
                order.posting_date.isoformat(),
                order.posting_time.strftime("%H:%M"),
                cashier,
                order.get_order_type_display(),
                text(order.customer_name),
                order.get_status_display(),
                money_text(order.net_total),
                money_text(order.grand_total),
                money_text(order.paid_amount),
                money_text(order.change_amount),
            ]

    filename = export_filename(
        "orders",
        {
            "search": search,
            "status": status_filter,
            "order-type": order_type_filter,
            "from": date_from.isoformat() if date_from else "",
            "to": date_to.isoformat() if date_to else "",
        },
    )
    return stream_csv(
        filename,
        [
            "Invoice",
            "Order no",
            "Date",
            "Time",
            "Cashier",
            "Type",
            "Customer",
            "Status",
            "Net",
            "Grand",
            "Paid",
            "Change",
        ],
        rows(),
    )


@backoffice_required
def order_detail(request: HttpRequest, pk: int) -> HttpResponse:
    ticket_queryset = KOT.objects.select_related("production_unit", "created_by").prefetch_related("items__item")
    order = get_object_or_404(
        Order.objects.select_related("cashier", "opening_entry").prefetch_related(
            "items__item__item_group",
            "payments__mode_of_payment",
            Prefetch("kots", queryset=ticket_queryset),
        ),
        pk=pk,
    )
    return render(
        request,
        "backoffice/orders/order_detail.html",
        {"order": order, "cancel_form": POSOrderCancelForm()},
    )


@manager_required
@require_POST
def order_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(Order, pk=pk)
    form = POSOrderCancelForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a cancellation reason before cancelling the order.")
        return redirect("orders:order_detail", pk=order.pk)
    try:
        cancellation_kots = services.cancel_sent_order(
            order,
            form.cleaned_data["cancel_reason"],
            cancelled_by=request.user,
            reason_note=form.cleaned_data["cancel_reason_note"],
        )
    except ValidationError as e:
        messages.error(request, str(e.messages[0]) if e.messages else "Cancel failed.")
        return redirect("orders:order_detail", pk=order.pk)
    for ticket_type in services.dispatch_tickets(cancellation_kots):
        messages.warning(request, f"Cancellation ticket for {ticket_type} remains pending.")
    messages.success(request, f"Order {order.invoice_number} cancelled.")
    return redirect("orders:order_detail", pk=order.pk)


@backoffice_required
def kot_list(request: HttpRequest) -> HttpResponse:
    type_filter = request.GET.get("type", "").strip()
    ticket_type_filter = request.GET.get("ticket_type", "").strip()
    status_filter = request.GET.get("status", "").strip()
    print_status_filter = request.GET.get("print_status", "").strip()
    search = request.GET.get("search", "").strip()
    kots = KOT.objects.select_related("order", "production_unit", "created_by").annotate(
        item_count=Count("items", distinct=True)
    )
    if type_filter:
        kots = kots.filter(type=type_filter)
    if ticket_type_filter:
        kots = kots.filter(ticket_type=ticket_type_filter)
    if status_filter:
        kots = kots.filter(status=status_filter)
    if print_status_filter:
        kots = kots.filter(print_status=print_status_filter)
    if search:
        search_query = Q(kot_number__icontains=search) | Q(order__invoice_number__icontains=search)
        order_number = search.removeprefix("#")
        if order_number.isdigit():
            search_query |= Q(order__order_number=int(order_number))
        kots = kots.filter(search_query)
    kots = kots[:50]
    return render(
        request,
        "backoffice/orders/kot_list.html",
        {
            "kots": kots,
            "type_filter": type_filter,
            "ticket_type_filter": ticket_type_filter,
            "status_filter": status_filter,
            "print_status_filter": print_status_filter,
            "search": search,
            "kot_type_choices": KOT_TYPE_CHOICES,
            "ticket_type_choices": TICKET_TYPE_CHOICES,
            "kot_status_choices": [(SUBMITTED, "Submitted"), (CANCELLED, "Cancelled")],
            "print_status_choices": KOT_PRINT_STATUS_CHOICES,
        },
    )


@backoffice_required
def kot_detail(request: HttpRequest, pk: int) -> HttpResponse:
    kot = get_object_or_404(
        KOT.objects.select_related("order", "production_unit", "created_by").prefetch_related("items__item"),
        pk=pk,
    )
    return render(request, "backoffice/orders/kot_detail.html", {"kot": kot})


@manager_required
@require_POST
def order_return(request: HttpRequest, pk: int) -> HttpResponse:
    """Create a return draft from a submitted order."""
    order = get_object_or_404(Order, pk=pk)
    try:
        return_order = services.make_return(order)
    except ValidationError as e:
        messages.error(request, str(e.messages[0]) if e.messages else "Return failed.")
        return redirect("orders:order_detail", pk=order.pk)
    messages.success(
        request,
        f"Return draft #{return_order.pk} created from {order.invoice_number}. "
        "Review and submit the return to process refunds.",
    )
    return redirect("orders:order_detail", pk=return_order.pk)


@manager_required
@require_POST
def order_return_submit(request: HttpRequest, pk: int) -> HttpResponse:
    """Submit a return draft, restoring stock and mirroring refunds."""
    order = get_object_or_404(Order, pk=pk)
    try:
        services.submit_return(order, actor=request.user)
    except ValidationError as e:
        messages.error(request, str(e.messages[0]) if e.messages else "Return submission failed.")
        return redirect("orders:order_detail", pk=order.pk)
    messages.success(
        request,
        f"Return {order.invoice_number} submitted. Stock restored and refunds recorded.",
    )
    return redirect("orders:order_detail", pk=order.pk)


@manager_required
@require_POST
def order_return_line_update(request: HttpRequest, pk: int, line_pk: int) -> HttpResponse:
    """Reduce qty, drop a line, or mark wastage on a return draft."""
    order = get_object_or_404(Order, pk=pk)
    qty_raw = request.POST.get("qty")
    qty = None
    if qty_raw is not None and qty_raw != "":
        try:
            qty = Decimal(str(qty_raw))
        except InvalidOperation, TypeError, ValueError:
            messages.error(request, "Enter a valid quantity.")
            return redirect("orders:order_detail", pk=order.pk)
    not_restockable = None
    if "not_restockable" in request.POST:
        not_restockable = request.POST.getlist("not_restockable")[-1] in {"1", "on", "true", "True"}
    try:
        services.update_return_line(order, line_pk, qty=qty, not_restockable=not_restockable)
    except ValidationError as e:
        messages.error(request, str(e.messages[0]) if e.messages else "Could not update the return line.")
        return redirect("orders:order_detail", pk=order.pk)
    except OrderItem.DoesNotExist:
        messages.error(request, "That return line was not found.")
        return redirect("orders:order_detail", pk=order.pk)
    messages.success(request, "Return draft updated.")
    return redirect("orders:order_detail", pk=order.pk)


@manager_required
@require_POST
def order_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Abandon an unsent draft order, keeping its audit trail as a tombstone."""
    order = get_object_or_404(Order, pk=pk)
    try:
        services.delete_unsent_draft(order, deleted_by=request.user)
    except ValidationError as e:
        messages.error(request, str(e.messages[0]) if e.messages else "Delete failed.")
        return redirect("orders:order_detail", pk=order.pk)
    messages.success(request, f"Order {order.invoice_number} deleted.")
    return redirect("orders:order_list")
