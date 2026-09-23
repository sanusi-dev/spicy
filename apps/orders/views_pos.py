"""POS-facing views for the orders app — the cashier's full-screen workflow."""

from decimal import Decimal, InvalidOperation
from typing import Protocol, cast

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from django_htmx.middleware import HtmxDetails

from apps.inventory.models import Item
from apps.menu.models import ItemVariant, MenuItem
from apps.orders.models import (
    CANCELLED,
    DINE_IN,
    DISCARDED,
    DRAFT,
    KOT_PRINT_PENDING,
    KOT_PRINTED,
    ORDER_TYPE_CHOICES,
    SUBMITTED,
    TICKET_BAR,
    TICKET_KITCHEN,
    Order,
)
from apps.payments.models import ModeOfPayment
from apps.settings.models import Restaurant
from apps.staff.forms import ClosingPaymentForm, OpeningFloatForm
from apps.staff.models import ClosingPayment, POSClosingEntry, POSOpeningEntry, ShiftCashOut
from apps.staff.services import (
    cancel_cash_out,
    ensure_closing_draft,
    expected_closing_amounts,
    open_shift,
    record_cash_out,
    submit_closing_entry,
)
from apps.users.decorators import staff_required

from . import printing, services
from .forms import POSOrderCancelForm

SESSION_ORDER_KEY = "pos_order_id"
SESSION_CARD_KEY = "pos_active_cards"
CATALOG_FILTER_TARGETS = {"catalog-workspace", "#catalog-workspace"}
ORDER_DETAILS_DRAWER_TARGETS = {"order-details-drawer", "#order-details-drawer"}


class _HtmxRequest(HttpRequest):
    htmx: HtmxDetails


class _AddOnWithMenuItem(Protocol):
    add_on_item: Item
    menu_item: MenuItem | None


def _is_htmx(request: HttpRequest) -> bool:
    return bool(cast(_HtmxRequest, request).htmx)


def _render_pos_surface(request, template_name, context):
    """Render a full POS page or its HTMX surface partial."""
    if _is_htmx(request):
        template_name = f"{template_name}#surface"
    return render(request, template_name, context)


def _home_or_redirect(request):
    """Return the POS home surface for HTMX or preserve the normal redirect."""
    if _is_htmx(request):
        response = pos_home(request)
        response["HX-Push-Url"] = reverse("pos:pos_home")
        return response
    return redirect("pos:pos_home")


def _get_open_shift(lock=False):
    """Return the open POSOpeningEntry, or None."""
    queryset = (
        POSOpeningEntry.objects.select_related("cashier")
        .filter(status=POSOpeningEntry.SUBMITTED, closing_entry__isnull=True)
        .order_by("period_start_date")
    )
    if lock:
        queryset = queryset.select_for_update()
    return queryset.first()


def _get_payment_modes():
    """Return enabled payment modes, ordered with the default first."""
    return ModeOfPayment.objects.filter(enabled=True).select_related("gl_mapping").order_by("-is_default", "name")


def _get_settle_payment_modes():
    """Return enabled payment modes that can be posted at checkout."""
    return _get_payment_modes().filter(gl_mapping__isnull=False, gl_mapping__default_account__isnull=False)


def _get_catalog_filters(request):
    """Read catalog filters from the current request."""
    params = request.GET if request.method == "GET" else request.POST
    query = str(params.get("q", "") or "").strip()
    group = str(params.get("group", "") or "").strip()
    specials = str(params.get("specials", "") or "").lower() in {"1", "true", "on", "yes"}
    return query, group, specials


def _is_catalog_filter_request(request):
    """Return whether an HTMX request targets the replaceable catalog workspace."""
    if not _is_htmx(request):
        return False
    target = request.headers.get("HX-Target")
    if target in CATALOG_FILTER_TARGETS:
        return True
    return target is None and any(parameter in request.GET for parameter in ("q", "group", "specials", "clear_filters"))


def _is_order_details_drawer_request(request):
    """Return whether an HTMX request targets the history detail drawer."""
    return _is_htmx(request) and request.headers.get("HX-Target") in ORDER_DETAILS_DRAWER_TARGETS


def _full_history_allowed(user) -> bool:
    """Return whether the user may open any historical order (managers or the setting)."""
    if user.is_manager or user.is_admin or user.is_superuser:
        return True
    restaurant = Restaurant.load()
    return bool(restaurant and restaurant.pos_allow_full_history)


def _get_kitchen_status(order):
    """Summarize the order's kitchen and bar ticket state."""
    tickets = list(order.kots.all())
    if not tickets:
        return "Not sent"
    if all(ticket.status == CANCELLED or ticket.print_status == "CANCELLED" for ticket in tickets):
        return "Cancelled"
    if any(ticket.status == SUBMITTED and ticket.print_status == KOT_PRINT_PENDING for ticket in tickets):
        return "Pending print"
    return "Sent"


def _group_menu_items(menu_items):
    """Group menu items by item group name, preserving order."""
    groups = {}
    for mi in menu_items:
        gname = mi.item.item_group.name
        groups.setdefault(gname, []).append(mi)
    return groups


def _group_items_by_guest(order, items=None):
    """Group order items by customer_index with per-guest subtotals."""
    buckets = {}
    for item in items if items is not None else order.items.all():
        idx = item.customer_index
        if idx not in buckets:
            buckets[idx] = {"index": idx, "items": [], "subtotal": Decimal("0")}
        buckets[idx]["items"].append(item)
        buckets[idx]["subtotal"] += item.amount

    return [
        buckets.get(idx, {"index": idx, "items": [], "subtotal": Decimal("0")})
        for idx in range(1, order.guest_count + 1)
    ]


def _get_active_card(request, order):
    """Return the active customer card index from session, defaulting to 1."""
    cards = request.session.get(SESSION_CARD_KEY, {})
    card = cards.get(str(order.pk), 1) if isinstance(cards, dict) else 1
    try:
        card = int(card)
    except TypeError, ValueError:
        card = 1
    if card < 1 or card > order.guest_count:
        return 1
    return card


def _render_cart(request, order, **extra_context):
    """Render the cart fragment with optional action feedback."""
    context = _build_order_context(request, order)
    context["catalog_oob"] = extra_context.pop("catalog_oob", False)
    context.update(extra_context)
    return render(request, "pos/index.html#cart", context)


def _parent_card_match(parent_item, variants, catalog_group, catalog_specials, catalog_query):
    """Return whether a variant parent card passes the catalog filters."""
    if (
        catalog_group
        and parent_item.item_group.name != catalog_group
        and all(mi.item.item_group.name != catalog_group for mi in variants)
    ):
        return False
    if catalog_specials and not any(mi.special_dish for mi in variants):
        return False
    if catalog_query:
        query = catalog_query.casefold()
        if query in parent_item.item_name.casefold() or query in parent_item.item_code.casefold():
            return True
        return any(
            query in mi.item_name.casefold()
            or query in mi.item.item_name.casefold()
            or query in mi.item.item_code.casefold()
            for mi in variants
        )
    return True


def _build_catalog_cards(all_menu_items, menu_items, catalog_group, catalog_specials, catalog_query):
    """Group variant lines under one parent card; ungrouped lines stay flat."""
    parents = {}
    for mi in all_menu_items:
        for link in mi.item.pos_variant_of.all():
            entry = parents.setdefault(link.parent_item_id, {"parent": link.parent_item, "variants": []})
            if mi not in entry["variants"]:
                entry["variants"].append(mi)
    for entry in parents.values():
        entry["variants"].sort(key=lambda mi: (mi.rate, mi.item_name))
    grouped_item_ids = {mi.item_id for entry in parents.values() for mi in entry["variants"]}
    parent_of = {}
    for parent_id, entry in parents.items():
        for mi in entry["variants"]:
            parent_of.setdefault(mi.item_id, []).append(parent_id)

    flat_ids = {mi.item_id for mi in menu_items}
    cards = []
    emitted_parents = set()
    for mi in all_menu_items:
        if mi.item_id in grouped_item_ids:
            for parent_id in parent_of.get(mi.item_id, []):
                if parent_id in emitted_parents:
                    continue
                entry = parents[parent_id]
                if _parent_card_match(
                    entry["parent"], entry["variants"], catalog_group, catalog_specials, catalog_query
                ):
                    cards.append(_parent_card(entry))
                    emitted_parents.add(parent_id)
        elif mi.item_id in flat_ids:
            cards.append({"kind": "single", "menu_item": mi})
    return cards


def _parent_card(entry):
    """Build a parent card dict from grouped variant lines."""
    variants = entry["variants"]
    rates = [mi.rate for mi in variants]
    messages = sorted({mi.stock_message for mi in variants if mi.stock_message})
    return {
        "kind": "parent",
        "parent_item": entry["parent"],
        "variants": variants,
        "min_rate": min(rates),
        "max_rate": max(rates),
        "stock_unavailable": bool(variants) and all(mi.stock_unavailable for mi in variants),
        "stock_message": "; ".join(messages),
    }


def _build_order_context(request, order):
    """Build the context dict for the order screen."""
    user = request.user
    catalog_query, catalog_group, catalog_specials = _get_catalog_filters(request)
    settings = Restaurant.load()
    active_menu = (
        settings.active_menu if settings and settings.active_menu_id and settings.active_menu.enabled else None
    )
    setup_error = None
    if settings is None:
        setup_error = "Restaurant settings are not configured."
    elif not settings.active_menu_id or not settings.active_menu or not settings.active_menu.enabled:
        setup_error = "No active menu is configured. Set an enabled menu in restaurant settings."
    all_menu_items = (
        list(
            active_menu.items.select_related("item", "item__item_group")
            .prefetch_related(
                "item__add_ons__add_on_item__menu_items",
                "item__add_on_for",
                "item__pos_variant_of__parent_item__item_group",
            )
            .filter(disabled=False)
        )
        if active_menu
        else []
    )
    group_names = {mi.item.item_group.name for mi in all_menu_items}
    group_lookup = {name.casefold(): name for name in group_names}
    catalog_group = group_lookup.get(catalog_group.casefold(), "") if catalog_group else ""
    normalized_query = catalog_query.casefold()
    menu_items = [
        menu_item
        for menu_item in all_menu_items
        if (not catalog_group or menu_item.item.item_group.name == catalog_group)
        and (not catalog_specials or menu_item.special_dish)
        and (
            not normalized_query
            or normalized_query in menu_item.item_name.casefold()
            or normalized_query in menu_item.item.item_name.casefold()
            or normalized_query in menu_item.item.item_code.casefold()
        )
    ]
    # Availability is unreserved stock, not physical stock.
    services.drink_stock_available(all_menu_items, settings)
    catalog_cards = _build_catalog_cards(all_menu_items, menu_items, catalog_group, catalog_specials, catalog_query)
    tickets = list(order.kots.select_related("production_unit").filter(status=SUBMITTED))
    tickets_by_type = {}
    for ticket in tickets:
        tickets_by_type.setdefault(ticket.ticket_type, ticket)
    kitchen_ticket = tickets_by_type.get(TICKET_KITCHEN)
    bar_ticket = tickets_by_type.get(TICKET_BAR)
    items = list(order.items.all())
    has_items = bool(items)
    return {
        "order": order,
        "shift": order.opening_entry,
        "menu_items": menu_items,
        "catalog_cards": catalog_cards,
        "catalog_item_count": len(all_menu_items),
        "item_groups": _group_menu_items(all_menu_items),
        "special_item_count": sum(1 for item in all_menu_items if item.special_dish),
        "catalog_query": catalog_query,
        "catalog_group": catalog_group,
        "catalog_specials": catalog_specials,
        "catalog_has_filters": bool(catalog_query or catalog_group or catalog_specials),
        "active_card": _get_active_card(request, order),
        "order_items": items,
        "guest_groups": _group_items_by_guest(order, items),
        "payment_modes": list(_get_settle_payment_modes()),
        "order_type_choices": ORDER_TYPE_CHOICES,
        "cancel_form": POSOrderCancelForm(),
        "order_sent": bool(tickets),
        "kitchen_ticket_pending": bool(kitchen_ticket and kitchen_ticket.print_status == KOT_PRINT_PENDING),
        "bar_ticket_pending": bool(bar_ticket and bar_ticket.print_status == KOT_PRINT_PENDING),
        "kitchen_ticket_printed": bool(kitchen_ticket and kitchen_ticket.print_status == KOT_PRINTED),
        "bar_ticket_printed": bool(bar_ticket and bar_ticket.print_status == KOT_PRINTED),
        "can_reprint": user.is_manager or user.is_admin or user.is_superuser,
        "has_items": has_items,
        "setup_error": setup_error,
        "pos_nav": "order",
    }


@staff_required
def pos_home(request: HttpRequest) -> HttpResponse:
    """Main POS entry: shift gate or draft orders list."""
    settings = Restaurant.load()
    if not settings:
        return _render_pos_surface(
            request,
            "pos/index.html",
            {"error": "Restaurant settings are not configured.", "pos_nav": "open"},
        )

    shift = _get_open_shift()

    if not shift:
        payment_modes = _get_payment_modes()
        return _render_pos_surface(
            request,
            "pos/index.html",
            {"no_shift": True, "payment_modes": list(payment_modes), "pos_nav": "open"},
        )

    order_filter = request.GET.get("filter", "all").strip()
    order_search = request.GET.get("q", "").strip()
    draft_orders = services.open_draft_orders(shift, request.user, order_filter, order_search)
    visible_draft_count = Order.objects.open_drafts_for(shift, request.user).count()
    total_draft_count = Order.objects.open_drafts(shift).count()
    max_open_drafts = settings.max_open_drafts
    context = {
        "shift": shift,
        "draft_orders": draft_orders,
        "draft_count": visible_draft_count,
        "max_open_drafts": max_open_drafts,
        "drafts_remaining": max(max_open_drafts - total_draft_count, 0),
        "draft_cap_reached": total_draft_count >= max_open_drafts,
        "order_filter": order_filter,
        "order_search": order_search,
        "show_order_tabs": True,
        "pos_nav": "open",
    }
    context.update(_cash_out_context(request, shift))
    return _render_pos_surface(
        request,
        "pos/draft_orders.html",
        context,
    )


@staff_required
@require_POST
def pos_order_new(request: HttpRequest) -> HttpResponse:
    """Create a new draft order and open the order screen."""
    user = request.user
    if Restaurant.load() is None:
        return _home_or_redirect(request)
    order_type = request.POST.get("order_type", DINE_IN)
    valid_types = {c[0] for c in ORDER_TYPE_CHOICES}
    if order_type not in valid_types:
        order_type = DINE_IN

    try:
        guest_count = int(request.POST.get("guest_count", "1"))
    except ValueError, TypeError:
        guest_count = 1
    guest_count = max(1, min(50, guest_count))
    shift = _get_open_shift()
    if shift is None:
        messages.error(request, "Open a shift before taking orders.")
        return _home_or_redirect(request)
    try:
        order = services.create_draft_order(shift, user, order_type=order_type, guest_count=guest_count)
    except ValidationError as e:
        messages.error(request, e.messages[0] if e.messages else "Cannot create the order.")
        return _home_or_redirect(request)
    request.session[SESSION_ORDER_KEY] = order.pk
    cards = request.session.get(SESSION_CARD_KEY, {})
    if not isinstance(cards, dict):
        cards = {}
    cards[str(order.pk)] = 1
    request.session[SESSION_CARD_KEY] = cards
    if _is_htmx(request):
        response = _render_pos_surface(request, "pos/index.html", _build_order_context(request, order))
        response["HX-Push-Url"] = reverse("pos:pos_order_screen", kwargs={"pk": order.pk})
        return response
    return redirect("pos:pos_order_screen", pk=order.pk)


@staff_required
@require_POST
def pos_open_shift(request: HttpRequest) -> HttpResponse:
    """Create a POSOpeningEntry with opening payments from the POS screen."""
    user = request.user
    if Restaurant.load() is None:
        return _home_or_redirect(request)
    form = OpeningFloatForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter valid non-negative opening balances.")
        return _home_or_redirect(request)
    opening_amounts = form.opening_amounts()
    if not opening_amounts:
        messages.error(request, "Configure at least one enabled payment method before opening a shift.")
        return _home_or_redirect(request)

    try:
        open_shift(user, opening_amounts, remarks=request.POST.get("remarks", ""))
    except ValidationError as e:
        messages.error(request, e.messages[0] if e.messages else "Cannot open the shift.")
        return _home_or_redirect(request)
    messages.success(request, "Shift opened successfully.")
    if _is_htmx(request):
        return _home_or_redirect(request)
    return redirect("pos:pos_home")


def _closing_form_prefix(mode_of_payment_id):
    # Keyed by payment mode — GET preview rows are unsaved (pk None).
    return f"cp_mop_{mode_of_payment_id}"


@staff_required
@require_http_methods(["GET", "POST"])
def pos_close_shift(request: HttpRequest) -> HttpResponse:
    """Show or submit the active shift's closing reconciliation."""
    user = request.user
    open_shift = _get_open_shift()
    if open_shift is None:
        messages.warning(request, "There is no open shift to close.")
        return _home_or_redirect(request)
    if not open_shift.can_be_closed_by(user):
        messages.error(request, "Only the cashier who opened this shift, or a manager, can close it.")
        return _home_or_redirect(request)

    draft_count = Order.objects.open_drafts(open_shift).count()
    if draft_count and request.method == "GET":
        return _render_pos_surface(
            request,
            "pos/close_shift.html",
            {
                "draft_count": draft_count,
                "shift": open_shift,
                "show_order_tabs": _is_htmx(request),
                "pos_nav": "close",
            },
        )

    period_start = open_shift.period_start_date
    period_end = timezone.now()
    expected_rows = expected_closing_amounts(open_shift, period_start, period_end)

    if request.method == "POST":
        with transaction.atomic():
            open_shift = POSOpeningEntry.objects.select_for_update().get(pk=open_shift.pk)
            draft_count = Order.objects.open_drafts(open_shift).count()
            if draft_count:
                messages.error(
                    request,
                    f"Close or settle {draft_count} open order{'s' if draft_count != 1 else ''} "
                    "before closing the shift.",
                )
                return _home_or_redirect(request)
            closing = ensure_closing_draft(open_shift, user)
            closing_payments = list(closing.closing_payments.select_related("mode_of_payment"))
            expected_by_mode = {row["mode"].pk: row for row in expected_rows}
            form_data = []
            for payment in closing_payments:
                expected = expected_by_mode.get(payment.mode_of_payment_id)
                if expected is not None:
                    payment.opening_amount = expected["opening_amount"]
                    payment.expected_amount = expected["expected_amount"]
                form_data.append(
                    (
                        payment,
                        ClosingPaymentForm(
                            request.POST,
                            instance=payment,
                            prefix=_closing_form_prefix(payment.mode_of_payment_id),
                        ),
                    )
                )
            if all(form.is_valid() for _payment, form in form_data):
                try:
                    for payment, form in form_data:
                        form.save()
                        payment.save(update_fields=["opening_amount", "expected_amount", "updated_at"])
                    closing.remarks = str(request.POST.get("remarks", "")).strip()
                    closing.period_end_date = timezone.now()
                    closing.save(update_fields=["remarks", "period_end_date", "updated_at"])
                    closing.full_clean()
                    submit_closing_entry(closing, actor=request.user)
                except ValidationError as exc:
                    messages.error(request, exc.messages[0] if exc.messages else "Cannot close the shift.")
                else:
                    messages.success(request, "Shift closed successfully.")
                    if _is_htmx(request):
                        return _home_or_redirect(request)
                    return redirect("pos:pos_home")
            display_closing = closing
            display_payments = [payment for payment, _form in form_data]
        cash_out_ctx = _cash_out_context(request, open_shift)
        return _render_pos_surface(
            request,
            "pos/close_shift.html",
            {
                "closing": display_closing,
                "form_data": form_data,
                "total_expected": sum((payment.expected_amount for payment in display_payments), Decimal("0")),
                "draft_count": 0,
                "shift": open_shift,
                "show_order_tabs": _is_htmx(request),
                "pos_nav": "close",
                **cash_out_ctx,
            },
        )

    existing_draft = POSClosingEntry.objects.filter(
        opening_entry=open_shift,
        status=POSClosingEntry.DRAFT,
    ).first()
    if existing_draft is not None:
        existing_by_mode = {
            cp.mode_of_payment_id: cp for cp in existing_draft.closing_payments.select_related("mode_of_payment").all()
        }
    else:
        existing_by_mode = {}

    form_data = []
    display_payments = []
    for row in expected_rows:
        payment = existing_by_mode.get(row["mode"].pk)
        if payment is None:
            payment = ClosingPayment(
                mode_of_payment=row["mode"],
                opening_amount=row["opening_amount"],
                expected_amount=row["expected_amount"],
                closing_amount=Decimal("0"),
            )
        else:
            payment.opening_amount = row["opening_amount"]
            payment.expected_amount = row["expected_amount"]
        display_payments.append(payment)
        form_data.append(
            (
                payment,
                ClosingPaymentForm(
                    instance=payment if payment.pk else None,
                    initial={"closing_amount": payment.closing_amount if payment.pk else Decimal("0")},
                    prefix=_closing_form_prefix(row["mode"].pk),
                ),
            )
        )

    class _PreviewClosing:
        period_start_date = period_start
        opening_entry = open_shift
        remarks = existing_draft.remarks if existing_draft is not None else ""

    return _render_pos_surface(
        request,
        "pos/close_shift.html",
        {
            "closing": existing_draft if existing_draft is not None else _PreviewClosing(),
            "form_data": form_data,
            "total_expected": sum((payment.expected_amount for payment in display_payments), Decimal("0")),
            "draft_count": 0,
            "shift": open_shift,
            "show_order_tabs": _is_htmx(request),
            "pos_nav": "close",
            **_cash_out_context(request, open_shift),
        },
    )


def _cash_out_context(request, open_shift):
    """Build the cash-out section context for the open shift."""
    expected_by_mode = {
        row["mode"].pk: row["expected_amount"]
        for row in expected_closing_amounts(open_shift, open_shift.period_start_date, timezone.now())
    }
    opening_modes = list(
        open_shift.opening_payments.select_related("mode_of_payment")
        .filter(mode_of_payment__type=ModeOfPayment.TYPE_CASH, mode_of_payment__enabled=True)
        .order_by("mode_of_payment__name")
    )
    cash_outs = list(open_shift.cash_outs.select_related("mode_of_payment", "recorded_by").order_by("-created_at"))
    user = request.user
    return {
        "shift": open_shift,
        "cash_modes": [
            {"mode": op.mode_of_payment, "expected": expected_by_mode.get(op.mode_of_payment_id, Decimal("0"))}
            for op in opening_modes
        ],
        "cash_outs": cash_outs,
        "can_cancel_cash_out": user.is_manager or user.is_admin or user.is_superuser,
        "cash_out_reasons": ShiftCashOut.REASON_CHOICES,
    }


@staff_required
def pos_cash_out_dialog(request: HttpRequest) -> HttpResponse:
    """Render the record-cash-out dialog fragment for the open shift."""
    open_shift = _get_open_shift()
    if open_shift is None:
        messages.warning(request, "There is no open shift.")
        return _home_or_redirect(request)
    return render(request, "pos/partials/shift/cash_out_dialog.html", _cash_out_context(request, open_shift))


@staff_required
@require_POST
def pos_cash_out_record(request: HttpRequest) -> HttpResponse:
    """Record a cash-out voucher from the POS dialog."""
    open_shift = _get_open_shift()
    if open_shift is None:
        messages.warning(request, "There is no open shift.")
        return _home_or_redirect(request)
    try:
        mode = ModeOfPayment.objects.get(pk=request.POST.get("mode_of_payment"), enabled=True)
        amount = Decimal(str(request.POST.get("amount", "0")))
        reason = str(request.POST.get("reason", "") or "").strip() or ShiftCashOut.OTHER
        record_cash_out(
            open_shift,
            mode=mode,
            amount=amount,
            reason=reason,
            note=str(request.POST.get("note", "") or ""),
            actor=request.user,
        )
    except ModeOfPayment.DoesNotExist, InvalidOperation, ValueError, TypeError:
        messages.error(request, "Enter a valid cash mode and amount.")
        if _is_htmx(request):
            return render(
                request,
                "pos/partials/shift/cash_out_dialog.html",
                {**_cash_out_context(request, open_shift), "error": "Enter a valid cash mode and amount."},
            )
        return redirect("pos:pos_home")
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if exc.messages else "Cannot record the cash-out.")
        if _is_htmx(request):
            return render(
                request,
                "pos/partials/shift/cash_out_dialog.html",
                {
                    **_cash_out_context(request, open_shift),
                    "error": exc.messages[0] if exc.messages else "Cannot record the cash-out.",
                },
            )
        return redirect("pos:pos_home")
    messages.success(request, "Cash-out recorded.")
    if _is_htmx(request):
        response = render(request, "pos/partials/shift/cash_out_section.html", _cash_out_context(request, open_shift))
        response["HX-Trigger"] = "close-cash-out"
        return response
    return redirect("pos:pos_home")


@staff_required
@require_POST
def pos_cash_out_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    """Cancel a cash-out voucher. Managers only."""
    user = request.user
    if not (user.is_manager or user.is_admin or user.is_superuser):
        return HttpResponse(status=403)
    open_shift = _get_open_shift()
    if open_shift is None:
        messages.warning(request, "There is no open shift.")
        return _home_or_redirect(request)
    row = get_object_or_404(ShiftCashOut, pk=pk, opening_entry=open_shift)
    try:
        cancel_cash_out(row, actor=user)
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if exc.messages else "Cannot cancel the cash-out.")
    else:
        messages.success(request, "Cash-out cancelled.")
    if _is_htmx(request):
        return render(request, "pos/partials/shift/cash_out_section.html", _cash_out_context(request, open_shift))
    redirect_to = str(request.POST.get("next", "") or "").strip() or reverse("pos:pos_home")
    return redirect(redirect_to)


@staff_required
def pos_order_screen(request: HttpRequest, pk: int) -> HttpResponse:
    """Render the full POS order screen (menu grid + cart)."""
    shift = _get_open_shift()
    if shift is None:
        return _home_or_redirect(request)
    order = get_object_or_404(
        Order.objects.open_drafts_for(shift, request.user).prefetch_related("items__item"),
        pk=pk,
    )
    request.session[SESSION_ORDER_KEY] = order.pk
    context = _build_order_context(request, order)
    if _is_catalog_filter_request(request):
        return render(request, "pos/index.html#catalog_workspace", context)
    return _render_pos_surface(request, "pos/index.html", context)


@staff_required
def pos_order_add_on_dialog(request: HttpRequest, pk: int, item_id: int) -> HttpResponse:
    """Render the optional add-on dialog for a menu item."""
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")
    order = get_object_or_404(Order.objects.open_drafts_for(shift, request.user), pk=pk)
    settings = Restaurant.load()
    active_menu = settings.active_menu if settings and settings.active_menu and settings.active_menu.enabled else None
    if active_menu is None:
        return HttpResponse("Active menu is not configured.", status=404)
    menu_item = get_object_or_404(
        MenuItem.objects.select_related("item").prefetch_related("item__add_ons__add_on_item__menu_items"),
        item_id=item_id,
        menu=active_menu,
        disabled=False,
    )
    add_ons: list[_AddOnWithMenuItem] = []
    for add_on in menu_item.item.add_ons.all():
        if add_on.add_on_item.disabled or not add_on.add_on_item.is_sales_item:
            continue
        resolved_add_on = cast(_AddOnWithMenuItem, add_on)
        resolved_add_on.menu_item = next(
            (
                candidate
                for candidate in add_on.add_on_item.menu_items.all()
                if candidate.menu_id == active_menu.pk and not candidate.disabled
            ),
            None,
        )
        if resolved_add_on.menu_item is not None:
            add_ons.append(resolved_add_on)
    return render(
        request,
        "pos/partials/catalog/add_on_dialog.html",
        {"order": order, "menu_item": menu_item, "add_ons": add_ons},
    )


def _variant_add_ons(item, active_menu):
    """Return the item's add-ons that are sellable on the active menu."""
    add_ons = []
    for add_on in item.add_ons.select_related("add_on_item").all():
        if add_on.add_on_item.disabled or not add_on.add_on_item.is_sales_item:
            continue
        resolved = cast(_AddOnWithMenuItem, add_on)
        resolved.menu_item = next(
            (
                candidate
                for candidate in add_on.add_on_item.menu_items.all()
                if candidate.menu_id == active_menu.pk and not candidate.disabled
            ),
            None,
        )
        if resolved.menu_item is not None:
            add_ons.append(resolved)
    return add_ons


@staff_required
def pos_order_variant_dialog(request: HttpRequest, pk: int, parent_item_id: int) -> HttpResponse:
    """Render the single-choice variant dialog for a grouped dish."""
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")
    order = get_object_or_404(Order.objects.open_drafts_for(shift, request.user), pk=pk)
    if order.kots.exists():
        return HttpResponse("This order was sent to the kitchen or bar.", status=404)
    settings = Restaurant.load()
    active_menu = settings.active_menu if settings and settings.active_menu and settings.active_menu.enabled else None
    if active_menu is None:
        return HttpResponse("Active menu is not configured.", status=404)
    parent_item = get_object_or_404(Item, pk=parent_item_id)
    options = []
    links = (
        ItemVariant.objects.filter(parent_item=parent_item)
        .select_related("variant_item")
        .prefetch_related("variant_item__menu_items", "variant_item__add_ons__add_on_item__menu_items")
    )
    for link in links:
        variant = link.variant_item
        if variant.disabled or not variant.is_sales_item:
            continue
        menu_item = next(
            (
                candidate
                for candidate in variant.menu_items.all()
                if candidate.menu_id == active_menu.pk and not candidate.disabled
            ),
            None,
        )
        if menu_item is None:
            continue
        options.append({"item": variant, "menu_item": menu_item})
    if not options:
        return HttpResponse("No sizes are available for this dish.", status=404)
    options.sort(key=lambda option: (option["menu_item"].rate, option["item"].item_name))
    services.drink_stock_available([option["menu_item"] for option in options], settings)
    first_available = next((option for option in options if not option["menu_item"].stock_unavailable), None)
    if first_available is not None:
        first_available["preselected"] = True
    return render(
        request,
        "pos/partials/catalog/variant_dialog.html",
        {"order": order, "parent_item": parent_item, "options": options},
    )


@staff_required
@require_POST
def pos_order_update_meta(request: HttpRequest, pk: int) -> HttpResponse:
    """Update order type or guest count on a draft order; return the cart partial."""
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")
    order = get_object_or_404(
        Order.objects.select_for_update().open_drafts_for(shift, request.user),
        pk=pk,
    )
    try:
        guest_count = services.update_order_meta(
            order,
            order_type=request.POST.get("order_type"),
            guest_delta=request.POST.get("guest_delta"),
            guest_count=request.POST.get("guest_count"),
            actor=request.user,
        )
    except ValidationError as e:
        return _render_cart(request, order, error=e.messages[0] if e.messages else "Cannot update the order.")
    active = _get_active_card(request, order)
    if active > guest_count:
        cards = request.session.get(SESSION_CARD_KEY, {})
        if isinstance(cards, dict):
            cards[str(order.pk)] = 1
            request.session[SESSION_CARD_KEY] = cards
    return _render_cart(request, order)


@staff_required
@require_POST
def pos_order_add_item(request: HttpRequest, pk: int) -> HttpResponse:
    """Add an item to the active customer card. Returns the cart partial."""
    error = None
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")
    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update().open_drafts_for(shift, request.user),
            pk=pk,
        )
        if order.kots.exists():
            error = "This order was sent to the kitchen or bar. Cancel it before making changes."
        else:
            item_id = 0
            variant_item_id = 0
            try:
                item_id = int(request.POST.get("item_id", 0))
                variant_item_id = int(request.POST.get("variant_item_id", 0) or 0)
                qty = Decimal(str(request.POST.get("qty", "1")))
            except ValueError, TypeError, InvalidOperation:
                error = "Invalid item quantity."
                qty = Decimal("0")

            if not error and qty <= 0:
                error = "Quantity must be greater than zero."

            item = Item.objects.filter(pk=item_id, disabled=False).first() if not error else None
            if not error and item is None:
                error = "That menu item is no longer available."
            if not error and item.has_variants and not variant_item_id:
                error = "Choose a size."

            variant = None
            if not error and variant_item_id:
                link = (
                    ItemVariant.objects.select_related("variant_item")
                    .filter(parent_item_id=item.pk, variant_item_id=variant_item_id)
                    .first()
                )
                if link is None:
                    error = "Choose a valid size."
                else:
                    variant = link.variant_item
                    if variant.disabled or not variant.is_sales_item:
                        error = "That size is no longer available."
                        variant = None

            comments = str(request.POST.get("comments", "") or "").strip()
            if len(comments) > 200:
                error = "Special instructions must be 200 characters or fewer."

            selected_add_on_ids = []
            for value in request.POST.getlist("add_on_ids"):
                try:
                    selected_add_on_ids.append(int(value))
                except TypeError, ValueError:
                    error = "Choose valid add-ons."
                    break

            if not error and variant is not None:
                settings = Restaurant.load()
                active_menu = (
                    settings.active_menu if settings and settings.active_menu and settings.active_menu.enabled else None
                )
                variant_menu_item = (
                    MenuItem.objects.filter(item=variant, menu=active_menu, disabled=False).first()
                    if active_menu is not None
                    else None
                )
                variant_add_ons = _variant_add_ons(variant, active_menu) if variant_menu_item is not None else []
                if variant_menu_item is None:
                    error = "That size is not on the active menu."
                elif variant_add_ons and not selected_add_on_ids and not request.POST.get("variant_confirmed"):
                    response = render(
                        request,
                        "pos/partials/catalog/add_on_dialog.html",
                        {
                            "order": order,
                            "menu_item": variant_menu_item,
                            "add_ons": variant_add_ons,
                            "preset_qty": qty,
                            "preset_comments": comments,
                            "variant_confirmed": True,
                        },
                    )
                    response["HX-Retarget"] = "#add-on-dialog-container"
                    response["HX-Reswap"] = "innerHTML"
                    response["HX-Trigger"] = "close-variant-dialog"
                    return response

            if not error and item is not None:
                try:
                    active_card = _get_active_card(request, order)
                    line_item = variant if variant is not None else item
                    services.apply_add_on_line(
                        order, line_item, selected_add_on_ids, qty, active_card, comments=comments
                    )
                    order.audit(
                        "ITEM_ADDED",
                        actor=request.user,
                        metadata={
                            "item_id": line_item.pk,
                            "quantity": str(qty),
                            "customer_index": active_card,
                            **({"parent_item_id": item.pk} if variant is not None else {}),
                        },
                    )
                except ValidationError as e:
                    error = e.messages[0] if e.messages else "Unable to add that item."

    response = _render_cart(request, order, error=error, catalog_oob=not error)
    if not error and request.headers.get("HX-Request"):
        response["HX-Trigger"] = "close-add-on-dialog"
    return response


@staff_required
@require_POST
def pos_order_update_item(request: HttpRequest, pk: int, item_pk: int) -> HttpResponse:
    """Update item quantity or remove it. Returns the cart partial."""
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")
    order = get_object_or_404(
        Order.objects.select_for_update().open_drafts_for(shift, request.user),
        pk=pk,
    )
    try:
        services.update_order_item(
            order,
            item_pk,
            action=request.POST.get("action", "update"),
            qty=request.POST.get("qty"),
            actor=request.user,
        )
    except ValidationError as e:
        return _render_cart(request, order, error=e.messages[0] if e.messages else "Invalid item update.")
    return _render_cart(request, order, catalog_oob=True)


@staff_required
@require_POST
def pos_customer_card_activate(request: HttpRequest, pk: int, idx: int) -> HttpResponse:
    """Set the active customer card in session and return the cart."""
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")
    order = get_object_or_404(Order.objects.open_drafts_for(shift, request.user), pk=pk)
    if 1 <= idx <= order.guest_count:
        cards = request.session.get(SESSION_CARD_KEY, {})
        if not isinstance(cards, dict):
            cards = {}
        cards[str(order.pk)] = idx
        request.session[SESSION_CARD_KEY] = cards
    return _render_cart(request, order)


@staff_required
@require_POST
def pos_order_sync(request: HttpRequest, pk: int) -> HttpResponse:
    """Create the initial kitchen and bar tickets, then print each independently."""
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")
    try:
        with transaction.atomic():
            order = get_object_or_404(
                Order.objects.select_for_update().open_drafts_for(shift, request.user),
                pk=pk,
            )
            kots = services.create_tickets(order, created_by=request.user)
    except ValidationError as e:
        order = get_object_or_404(Order.objects.open_drafts_for(shift, request.user), pk=pk)
        return _render_cart(
            request,
            order,
            error=e.messages[0] if e.messages else "Unable to send the order.",
        )

    # Print each ticket individually so one printer failure doesn't block the other station.
    print_failures = services.dispatch_tickets(kots)

    if not print_failures:
        messages.success(request, f"Sent {len(kots)} ticket{'s' if len(kots) != 1 else ''} to kitchen & bar.")

    return _render_cart(
        request,
        order,
        catalog_oob=True,
        sync_success=True,
        kot_count=len(kots),
        print_failures=print_failures,
    )


@staff_required
@require_POST
def pos_order_clear(request: HttpRequest, pk: int) -> HttpResponse:
    """Empty a draft order before any kitchen or bar ticket has been sent."""
    error = None
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")
    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update().open_drafts_for(shift, request.user),
            pk=pk,
        )
        if order.kots.exists():
            error = "This order was sent to the kitchen or bar. Use Cancel Order instead of Clear."
        else:
            services.clear_order_lines(order)
            order.audit("ITEMS_CLEARED", actor=request.user)
            messages.success(request, "Order cleared.")

    return _render_cart(request, order, error=error, catalog_oob=not error)


@staff_required
@require_http_methods(["GET", "POST"])
def pos_order_settle(request: HttpRequest, pk: int) -> HttpResponse:
    """GET: show payment dialog. POST: process payment and settle."""
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")
    order = get_object_or_404(Order.objects.open_drafts_for(shift, request.user), pk=pk)

    if request.method == "POST":
        payments_data = []
        for key, value in request.POST.items():
            mode_pk = key.removeprefix("payment_") if key.startswith("payment_") else ""
            if mode_pk.isdigit() and str(value).strip() != "":
                payments_data.append(
                    {
                        "mode_of_payment": mode_pk,
                        "amount": value,
                        "reference_no": request.POST.get(f"reference_{mode_pk}", ""),
                    }
                )

        if not payments_data:
            messages.error(request, "Enter at least one payment amount.")
            return redirect("pos:pos_order_screen", pk=order.pk)

        try:
            services.settle_order(order, payments_data, cashier=request.user, opening_entry=shift)
        except ValidationError as e:
            messages.error(request, str(e.messages[0]) if e.messages else "Settle failed.")
            return redirect("pos:pos_order_screen", pk=order.pk)

        request.session.pop(SESSION_ORDER_KEY, None)
        cards = request.session.get(SESSION_CARD_KEY, {})
        if isinstance(cards, dict):
            cards.pop(str(order.pk), None)
            request.session[SESSION_CARD_KEY] = cards
        messages.success(request, f"Order {order.invoice_number} settled.")
        if not printing.print_receipt(order).success:
            messages.warning(request, "The receipt failed to print — reprint it from order history.")
        return redirect("pos:pos_home")

    return render(
        request,
        "pos/index.html#payment_dialog",
        {
            "order": order,
            "payment_modes": list(_get_settle_payment_modes()),
            "require_payment_reference": Restaurant.requires_payment_reference(),
            "show_payment": True,
        },
    )


@staff_required
@require_POST
def pos_order_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    """Cancel a sent draft order with a structured reason."""
    form = POSOrderCancelForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a cancellation reason before cancelling the order.")
        return redirect("pos:pos_order_screen", pk=pk)
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")
    cancellation_kots = []
    try:
        with transaction.atomic():
            order = get_object_or_404(
                Order.objects.select_for_update().open_drafts_for(shift, request.user),
                pk=pk,
            )
            cancellation_kots = services.cancel_sent_order(
                order,
                reason=form.cleaned_data["cancel_reason"],
                reason_note=form.cleaned_data["cancel_reason_note"],
                cancelled_by=request.user,
            )
    except ValidationError as e:
        messages.error(request, str(e.messages[0]) if e.messages else "Cancel failed.")
        return redirect("pos:pos_order_screen", pk=pk)

    print_failures = services.dispatch_tickets(cancellation_kots)

    request.session.pop(SESSION_ORDER_KEY, None)
    cards = request.session.get(SESSION_CARD_KEY, {})
    if isinstance(cards, dict):
        cards.pop(str(order.pk), None)
        request.session[SESSION_CARD_KEY] = cards
    if print_failures:
        failed = ", ".join(sorted(set(print_failures)))
        messages.warning(
            request,
            f"Order {order.invoice_number} cancelled, but the {failed} cancellation ticket "
            "failed to print. Retry it from order history.",
        )
    else:
        messages.success(request, f"Order {order.invoice_number} cancelled.")
    return redirect("pos:pos_home")


@staff_required
@require_POST
def pos_order_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Abandon an unsent draft order, keeping its audit trail as a tombstone."""
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")
    try:
        with transaction.atomic():
            order = get_object_or_404(
                Order.objects.select_for_update().open_drafts_for(shift, request.user),
                pk=pk,
            )
            services.delete_unsent_draft(order, deleted_by=request.user)
    except ValidationError as e:
        messages.error(request, str(e.messages[0]) if e.messages else "Delete failed.")
        return redirect("pos:pos_order_screen", pk=pk)
    request.session.pop(SESSION_ORDER_KEY, None)
    cards = request.session.get(SESSION_CARD_KEY, {})
    if isinstance(cards, dict):
        cards.pop(str(order.pk), None)
        request.session[SESSION_CARD_KEY] = cards
    messages.success(request, f"Order {order.invoice_number} deleted.")
    return redirect("pos:pos_home")


@staff_required
@require_POST
def pos_order_ticket_print(request: HttpRequest, pk: int, ticket_type: str, action: str) -> HttpResponse:
    """Retry or reprint one kitchen/bar ticket, including cancellation tickets."""
    user = request.user
    if ticket_type not in {TICKET_KITCHEN, TICKET_BAR} or action not in {"retry", "reprint"}:
        return HttpResponse(status=404)
    if action == "reprint" and not (user.is_manager or user.is_admin or user.is_superuser):
        return HttpResponse(status=403)
    shift = _get_open_shift()
    if shift is None:
        return redirect("pos:pos_home")

    feedback = {}
    required_status = KOT_PRINT_PENDING if action == "retry" else KOT_PRINTED
    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            pk=pk,
            status__in=[DRAFT, CANCELLED, SUBMITTED],
            is_return=False,
            opening_entry=shift,
        )
        if order.status == DRAFT and not order.can_be_accessed_by(user):
            return HttpResponse(status=404)
        # Cancellation tickets remain SUBMITTED with print_status PENDING after a failed print.
        ticket = (
            order.kots.select_for_update()
            .filter(ticket_type=ticket_type, print_status=required_status, status=SUBMITTED)
            .order_by("-created_at")
            .first()
        )
        if ticket is None:
            if order.status == DRAFT:
                return _render_cart(request, order, error=f"No {ticket_type} ticket is ready for that action.")
            messages.error(request, f"No {ticket_type} ticket is ready for that action.")
            return redirect("pos:pos_order_history_detail", pk=order.pk)

    order = Order.objects.get(pk=pk)
    if services.dispatch_tickets([ticket]):
        feedback = {"ticket_print_error": ticket_type, "ticket_print_action": action}
        if order.status != DRAFT:
            messages.error(request, f"{ticket_type.title()} ticket failed to print. Try again.")
    else:
        messages.success(request, f"{ticket_type.title()} ticket {'reprinted' if action == 'reprint' else 'retried'}.")

    if order.status == DRAFT:
        return _render_cart(request, order, **feedback)
    return redirect("pos:pos_order_history_detail", pk=order.pk)


@staff_required
def pos_order_history(request: HttpRequest) -> HttpResponse:
    """Show cashier-safe historical orders for a selected date."""
    from datetime import date as date_type

    user = request.user
    payment_filter = request.GET.get("payment", "").strip()
    status_filter = request.GET.get("status", "sales").strip()
    order_type_filter = request.GET.get("order_type", "").strip()
    search = request.GET.get("q", "").strip()
    if "date" not in request.GET:
        parsed_date = timezone.localdate()
        date_filter = str(parsed_date)
    else:
        date_filter = request.GET.get("date", "").strip()
        if date_filter:
            try:
                parsed_date = date_type.fromisoformat(date_filter)
            except ValueError:
                parsed_date = timezone.localdate()
                date_filter = str(parsed_date)
        else:
            parsed_date = None

    allow_full_history = _full_history_allowed(user)
    manager_only_filters = {"all", "returns", "cancelled", "discarded"}
    if status_filter in manager_only_filters and not allow_full_history:
        status_filter = "sales"

    orders = services.order_history_rows(
        {
            "payment": payment_filter,
            "status": status_filter,
            "order_type": order_type_filter,
            "search": search,
            "posting_date": parsed_date,
        }
    )
    paginator = Paginator(orders, 50)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)
    open_shift = _get_open_shift()
    return _render_pos_surface(
        request,
        "pos/order_history.html",
        {
            "orders": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "payment_filter": payment_filter,
            "status_filter": status_filter,
            "order_type_filter": order_type_filter,
            "search": search,
            "date_filter": date_filter,
            "allow_full_history": allow_full_history,
            "shift": open_shift,
            "draft_count": (Order.objects.open_drafts_for(open_shift, request.user).count() if open_shift else 0),
            "show_order_tabs": True,
            "pos_nav": "history",
        },
    )


@staff_required
def pos_order_history_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Show a read-only cashier view of a historical order."""
    orders = Order.objects.select_related("cashier", "opening_entry", "stock_warehouse").prefetch_related(
        "items__item", "payments__mode_of_payment", "kots__production_unit"
    )
    if _full_history_allowed(request.user):
        orders = orders.filter(status__in=[SUBMITTED, CANCELLED, DISCARDED])
    else:
        # Same visibility as the cashier's history list: paid sales only.
        orders = orders.filter(status=SUBMITTED, is_paid=True, is_return=False)
    order = get_object_or_404(orders, pk=pk)
    open_shift = _get_open_shift()
    context = {
        "order": order,
        "draft_count": (Order.objects.open_drafts_for(open_shift, request.user).count() if open_shift else 0),
        "show_order_tabs": _is_htmx(request),
        "pos_nav": "history",
        "kitchen_status": _get_kitchen_status(order),
        # GET opens the drawer with a slide-in; POST re-renders (print) stay put.
        "drawer_animate": request.method == "GET",
    }
    if _is_order_details_drawer_request(request):
        return render(request, "pos/order_history_detail.html#drawer", context)
    return _render_pos_surface(request, "pos/order_history_detail.html", context)


@staff_required
@require_POST
def pos_order_history_print(request: HttpRequest, pk: int) -> HttpResponse:
    """Reprint a submitted historical receipt without editing it."""
    orders = Order.objects.filter(status=SUBMITTED)
    if not _full_history_allowed(request.user):
        orders = orders.filter(is_paid=True, is_return=False)
    order = get_object_or_404(orders, pk=pk)
    result = printing.print_receipt(order)
    if result.success:
        messages.success(request, "Receipt reprinted successfully.")
    else:
        messages.error(request, "Receipt could not be printed.")
    if _is_htmx(request):
        response = pos_order_history_detail(request, pk=order.pk)
        if not _is_order_details_drawer_request(request):
            response["HX-Push-Url"] = reverse("pos:pos_order_history_detail", kwargs={"pk": order.pk})
        return response
    return redirect("pos:pos_order_history_detail", pk=order.pk)
