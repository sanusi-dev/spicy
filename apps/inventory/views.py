from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.reports.food_usage import _plate_cost, compute_food_usage, recipe_plate_cost
from apps.users.decorators import backoffice_required
from apps.utils.csv_export import export_filename, money_text, over_row_cap, stream_csv, text
from apps.utils.forms import add_formset_row, remove_formset_row
from apps.utils.rounding import TWO_PLACES

from . import services
from .forms import (
    ItemForm,
    ItemGroupForm,
    ItemUOMConversionFormSet,
    PurchaseReceiptForm,
    PurchaseReceiptItemForm,
    PurchaseReceiptItemFormSet,
    RecipeForm,
    RecipeItemFormSet,
    StockEntryDetailForm,
    StockEntryDetailFormSet,
    StockEntryForm,
    StockReconciliationForm,
    StockReconciliationItemFormSet,
    UOMForm,
    WarehouseForm,
)
from .models import (
    UOM,
    Bin,
    Item,
    ItemGroup,
    PurchaseReceipt,
    PurchaseReceiptItem,
    Recipe,
    StockEntry,
    StockEntryDetail,
    StockLedgerEntry,
    StockReconciliation,
    Warehouse,
)


@backoffice_required
def inventory_dashboard(request: HttpRequest) -> HttpResponse:
    context = {
        "item_count": Item.objects.count(),
        "uom_count": UOM.objects.count(),
    }
    return render(request, "backoffice/inventory/dashboard.html", context)


@backoffice_required
def uom_list(request: HttpRequest) -> HttpResponse:
    uoms = UOM.objects.all().order_by("name")
    return render(request, "backoffice/inventory/uom_list.html", {"uoms": uoms})


@backoffice_required
def uom_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = UOMForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("inventory:uom_list")
    else:
        form = UOMForm()
    return render(
        request,
        "backoffice/inventory/uom_form.html",
        {"form": form, "is_create": True},
    )


@backoffice_required
def uom_update(request: HttpRequest, pk: int) -> HttpResponse:
    uom = get_object_or_404(UOM, pk=pk)
    if request.method == "POST":
        form = UOMForm(request.POST, instance=uom)
        if form.is_valid():
            form.save()
            return redirect("inventory:uom_list")
    else:
        form = UOMForm(instance=uom)
    return render(
        request,
        "backoffice/inventory/uom_form.html",
        {"form": form, "is_create": False, "uom": uom},
    )


@backoffice_required
def item_group_list(request: HttpRequest) -> HttpResponse:
    groups = ItemGroup.objects.all().order_by("name")
    return render(request, "backoffice/inventory/item_group_list.html", {"groups": groups})


@backoffice_required
def item_group_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ItemGroupForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("inventory:item_group_list")
    else:
        form = ItemGroupForm()
    return render(
        request,
        "backoffice/inventory/item_group_form.html",
        {"form": form, "is_create": True},
    )


@backoffice_required
def item_group_detail(request: HttpRequest, pk: int) -> HttpResponse:
    group = get_object_or_404(ItemGroup, pk=pk)
    items = group.items.all().order_by("item_name")
    return render(
        request,
        "backoffice/inventory/item_group_detail.html",
        {"group": group, "items": items},
    )


@backoffice_required
def item_group_update(request: HttpRequest, pk: int) -> HttpResponse:
    group = get_object_or_404(ItemGroup, pk=pk)
    if request.method == "POST":
        form = ItemGroupForm(request.POST, instance=group)
        if form.is_valid():
            form.save()
            return redirect("inventory:item_group_detail", pk=group.pk)
    else:
        form = ItemGroupForm(instance=group)
    return render(
        request,
        "backoffice/inventory/item_group_form.html",
        {"form": form, "is_create": False, "group": group},
    )


@backoffice_required
def warehouse_list(request: HttpRequest) -> HttpResponse:
    warehouses = Warehouse.objects.all()
    return render(request, "backoffice/inventory/warehouse_list.html", {"warehouses": warehouses})


@backoffice_required
def warehouse_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = WarehouseForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("inventory:warehouse_list")
    else:
        form = WarehouseForm()
    return render(
        request,
        "backoffice/inventory/warehouse_form.html",
        {"form": form, "is_create": True},
    )


@backoffice_required
def warehouse_detail(request: HttpRequest, pk: int) -> HttpResponse:
    warehouse = get_object_or_404(Warehouse, pk=pk)
    bins = warehouse.bins.select_related("item").order_by("item__item_name")
    return render(
        request,
        "backoffice/inventory/warehouse_detail.html",
        {"warehouse": warehouse, "bins": bins},
    )


@backoffice_required
def warehouse_update(request: HttpRequest, pk: int) -> HttpResponse:
    warehouse = get_object_or_404(Warehouse, pk=pk)
    if request.method == "POST":
        form = WarehouseForm(request.POST, instance=warehouse)
        if form.is_valid():
            form.save()
            return redirect("inventory:warehouse_detail", pk=warehouse.pk)
    else:
        form = WarehouseForm(instance=warehouse)
    return render(
        request,
        "backoffice/inventory/warehouse_form.html",
        {"form": form, "is_create": False, "warehouse": warehouse},
    )


@backoffice_required
def item_list(request: HttpRequest) -> HttpResponse:
    item_group_id = request.GET.get("item_group")
    department = request.GET.get("department")
    sellable = request.GET.get("sellable")
    purchasable = request.GET.get("purchasable")
    kind = request.GET.get("kind")
    status = request.GET.get("status", "active")
    search_q = request.GET.get("q", "").strip()

    items = Item.objects.select_related(
        "item_group",
        "stock_uom",
        "variant_of",
    )

    if item_group_id:
        items = items.filter(item_group_id=item_group_id)

    if department:
        items = items.filter(department=department)

    if sellable == "1":
        items = items.filter(is_sales_item=True)
    elif sellable == "0":
        items = items.filter(is_sales_item=False)

    if purchasable == "1":
        items = items.filter(is_purchase_item=True)
    elif purchasable == "0":
        items = items.filter(is_purchase_item=False)

    if kind == "template":
        items = items.filter(has_variants=True)
    elif kind == "variant":
        items = items.filter(variant_of__isnull=False)
    elif kind == "plain":
        items = items.filter(
            has_variants=False,
            variant_of__isnull=True,
        )

    if status == "active":
        items = items.filter(disabled=False)
    elif status == "disabled":
        items = items.filter(disabled=True)

    if search_q:
        items = items.filter(Q(item_name__icontains=search_q) | Q(item_code__icontains=search_q))

    context = {
        "items": items,
        "item_groups": ItemGroup.objects.all().order_by("name"),
        "selected_item_group": item_group_id,
        "selected_department": department,
        "selected_sellable": sellable,
        "selected_purchasable": purchasable,
        "selected_kind": kind,
        "selected_status": status,
        "search_q": search_q,
    }

    if request.htmx and request.htmx.target == "item-table-body":
        return render(
            request,
            "backoffice/inventory/item_list.html#item_rows",
            {"items": items},
        )

    return render(
        request,
        "backoffice/inventory/item_list.html",
        context,
    )


def _item_form_context(form, uom_formset, *, is_create, item=None):
    return {
        "form": form,
        "uom_formset": uom_formset,
        "is_create": is_create,
        "item": item,
    }


@backoffice_required
def item_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ItemForm(request.POST, request.FILES)
        uom_fs = ItemUOMConversionFormSet(request.POST, instance=form.instance, prefix="uoms")
        form_ok = form.is_valid()
        uom_fs.instance = form.instance
        uom_ok = uom_fs.is_valid()
        if form_ok and uom_ok:
            with transaction.atomic():
                item = form.save()
                uom_fs.instance = item
                uom_fs.save()
            return redirect("inventory:item_detail", pk=item.pk)
    else:
        form = ItemForm()
        uom_fs = ItemUOMConversionFormSet(instance=Item(), prefix="uoms")
    return render(
        request,
        "backoffice/inventory/item_form.html",
        _item_form_context(form, uom_fs, is_create=True),
    )


@backoffice_required
def item_detail(request: HttpRequest, pk: int) -> HttpResponse:
    from django.db.models import Exists, OuterRef

    from apps.menu.models import MenuItem

    item = get_object_or_404(
        Item.objects.select_related("item_group", "stock_uom", "variant_of"),
        pk=pk,
    )
    conversions = item.uom_conversions.select_related("uom").order_by("uom__name")
    bins = item.bins.select_related("warehouse").all()
    on_menu = Exists(MenuItem.objects.filter(item_id=OuterRef("pk"), disabled=False))
    variants = (
        item.variants.annotate(on_menu=on_menu).select_related("item_group", "stock_uom").order_by("item_name")
        if item.has_variants
        else Item.objects.none()
    )
    menu_lines = item.menu_items.select_related("menu").order_by("menu__name")
    active_recipe = (
        Recipe.objects.filter(item=item, is_active=True).prefetch_related("items__ingredient__stock_uom").first()
    )
    plate_cost = recipe_plate_cost(active_recipe) if active_recipe else None
    return render(
        request,
        "backoffice/inventory/item_detail.html",
        {
            "item": item,
            "bins": bins,
            "conversions": conversions,
            "variants": variants,
            "menu_lines": menu_lines,
            "active_recipe": active_recipe,
            "plate_cost": plate_cost,
        },
    )


@backoffice_required
def item_update(request: HttpRequest, pk: int) -> HttpResponse:
    item = get_object_or_404(
        Item.objects.select_related("item_group", "stock_uom", "variant_of"),
        pk=pk,
    )
    if request.method == "POST":
        form = ItemForm(request.POST, request.FILES, instance=item)
        uom_fs = ItemUOMConversionFormSet(request.POST, instance=item, prefix="uoms")
        form_ok = form.is_valid()
        uom_fs.instance = form.instance
        uom_ok = uom_fs.is_valid()
        if form_ok and uom_ok:
            with transaction.atomic():
                form.save()
                uom_fs.save()
            return redirect("inventory:item_detail", pk=item.pk)
    else:
        form = ItemForm(instance=item)
        uom_fs = ItemUOMConversionFormSet(instance=item, prefix="uoms")
    return render(
        request,
        "backoffice/inventory/item_form.html",
        _item_form_context(form, uom_fs, is_create=False, item=item),
    )


@backoffice_required
@require_POST
def item_uom_add(request: HttpRequest) -> HttpResponse:
    formset = add_formset_row(ItemUOMConversionFormSet, "uoms", request.POST)
    return render(request, "backoffice/inventory/item_form.html#uom_conversions_partial", {"uom_formset": formset})


@backoffice_required
@require_POST
def item_uom_remove(request: HttpRequest, index: int) -> HttpResponse:
    formset = remove_formset_row(ItemUOMConversionFormSet, "uoms", request.POST, index)
    return render(request, "backoffice/inventory/item_form.html#uom_conversions_partial", {"uom_formset": formset})


@backoffice_required
def stock_entry_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status")
    purpose = request.GET.get("purpose")
    entries = StockEntry.objects.select_related("mode_of_payment").all()
    if status:
        entries = entries.filter(status=status)
    if purpose:
        entries = entries.filter(purpose=purpose)
    return render(
        request,
        "backoffice/inventory/stock_entry_list.html",
        {
            "entries": entries,
            "selected_status": status,
            "selected_purpose": purpose,
        },
    )


@backoffice_required
def stock_entry_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = StockEntryForm(request.POST)
        detail_fs = StockEntryDetailFormSet(
            request.POST, instance=StockEntry(purpose=request.POST.get("purpose", "")), prefix="items"
        )
        if form.is_valid() and detail_fs.is_valid():
            with transaction.atomic():
                entry = form.save()
                detail_fs.instance = entry
                detail_fs.save()
            return redirect("inventory:stock_entry_detail", pk=entry.pk)
    else:
        form = StockEntryForm()
        detail_fs = StockEntryDetailFormSet(instance=StockEntry(purpose="MATERIAL_RECEIPT"), prefix="items")
    return render(
        request,
        "backoffice/inventory/stock_entry_form.html",
        {"form": form, "is_create": True, "detail_formset": detail_fs},
    )


@backoffice_required
@require_POST
def stock_entry_item_add(request: HttpRequest) -> HttpResponse:
    formset = add_formset_row(StockEntryDetailFormSet, "items", request.POST)
    return render(
        request, "backoffice/inventory/stock_entry_form.html#detail_items_partial", {"detail_formset": formset}
    )


@backoffice_required
@require_POST
def stock_entry_item_remove(request: HttpRequest, index: int) -> HttpResponse:
    formset = remove_formset_row(StockEntryDetailFormSet, "items", request.POST, index)
    return render(
        request, "backoffice/inventory/stock_entry_form.html#detail_items_partial", {"detail_formset": formset}
    )


def _entry_line_params(request: HttpRequest):
    """Pull the bound prefix and entry-UOM params from an HTMX line request."""
    data = request.GET or request.POST
    prefix = None
    for key in data:
        suffix = key.rsplit("-", 1)[-1]
        if suffix in {"item", "uom", "qty"}:
            prefix = key[: -len(suffix) - 1]
            break
    item_id = data.get(f"{prefix}-item") if prefix else data.get("item")
    uom_id = data.get(f"{prefix}-uom") if prefix else data.get("uom")
    qty = data.get(f"{prefix}-qty") if prefix else data.get("qty")
    return prefix, item_id, uom_id, qty


@backoffice_required
@require_GET
def stock_entry_item_meta(request: HttpRequest) -> HttpResponse:
    prefix, item_id, _uom_id, qty_raw = _entry_line_params(request)
    item = Item.objects.filter(pk=item_id).select_related("stock_uom").first() if item_id else None
    purpose = request.GET.get("purpose") or "MATERIAL_RECEIPT"
    instance = StockEntryDetail(item=item, uom=item.stock_uom if item else None)
    instance.stock_entry = StockEntry(purpose=purpose)
    form = StockEntryDetailForm(instance=instance, prefix=prefix)
    qty = None
    if qty_raw:
        try:
            qty = Decimal(str(qty_raw))
        except InvalidOperation:
            qty = None
    preview = _stock_qty_preview_text(item, item.stock_uom if item else None, qty)
    return render(
        request,
        "backoffice/inventory/stock_entry_form.html#uom_widget_partial",
        {"uom_field": form["uom"], "prefix": prefix or "", "preview": preview},
    )


@backoffice_required
@require_GET
def stock_entry_stock_qty_preview(request: HttpRequest) -> HttpResponse:
    prefix, item_id, uom_id, qty_raw = _entry_line_params(request)
    item = Item.objects.filter(pk=item_id).select_related("stock_uom").first() if item_id else None
    uom = UOM.objects.filter(pk=uom_id).first() if uom_id else None
    qty = None
    if qty_raw:
        try:
            qty = Decimal(str(qty_raw))
        except InvalidOperation:
            qty = None
    preview = _stock_qty_preview_text(item, uom, qty)
    return render(
        request,
        "backoffice/inventory/stock_entry_form.html#stock_qty_preview_partial",
        {"prefix": prefix or "", "preview": preview},
    )


@backoffice_required
def stock_entry_detail(request: HttpRequest, pk: int) -> HttpResponse:
    entry = get_object_or_404(StockEntry.objects.select_related("mode_of_payment"), pk=pk)
    items = entry.items.select_related("item", "source_warehouse", "target_warehouse", "uom").all()
    voucher_no = str(entry.pk)
    ledger_entries = StockEntry.stock_ledger_entries_for_voucher(voucher_no)
    return render(
        request,
        "backoffice/inventory/stock_entry_detail.html",
        {"entry": entry, "items": items, "ledger_entries": ledger_entries},
    )


@backoffice_required
@require_POST
def stock_entry_submit(request: HttpRequest, pk: int) -> HttpResponse:
    entry = get_object_or_404(StockEntry, pk=pk)
    if entry.status == "DRAFT":
        try:
            services.submit_stock_entry(entry)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Stock entry #{entry.pk} submitted.")
    return redirect("inventory:stock_entry_detail", pk=pk)


@backoffice_required
@require_POST
def stock_entry_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    entry = get_object_or_404(StockEntry, pk=pk)
    if entry.status == "SUBMITTED":
        try:
            services.cancel_stock_entry(entry)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Stock entry #{entry.pk} cancelled.")
    return redirect("inventory:stock_entry_detail", pk=pk)


@backoffice_required
def reconciliation_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status")
    reason = request.GET.get("reason")
    warehouse_id = request.GET.get("warehouse")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    reconciliations = StockReconciliation.objects.select_related("warehouse").all()
    if status:
        reconciliations = reconciliations.filter(status=status)
    if reason:
        reconciliations = reconciliations.filter(reason=reason)
    if warehouse_id:
        reconciliations = reconciliations.filter(warehouse_id=warehouse_id)
    if date_from:
        reconciliations = reconciliations.filter(posting_date__gte=date_from)
    if date_to:
        reconciliations = reconciliations.filter(posting_date__lte=date_to)
    return render(
        request,
        "backoffice/inventory/reconciliation_list.html",
        {
            "reconciliations": reconciliations,
            "warehouses": Warehouse.objects.filter(disabled=False),
            "reason_choices": StockReconciliationForm.ACTIVE_REASON_CHOICES,
            "selected_status": status,
            "selected_reason": reason,
            "selected_warehouse": warehouse_id,
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
    )


@backoffice_required
def reconciliation_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = StockReconciliationForm(request.POST)
        item_fs = StockReconciliationItemFormSet(request.POST, instance=StockReconciliation(), prefix="items")
        if form.is_valid() and item_fs.is_valid():
            with transaction.atomic():
                reconciliation = form.save()
                item_fs.instance = reconciliation
                item_fs.save()
            return redirect("inventory:reconciliation_detail", pk=reconciliation.pk)
    else:
        form = StockReconciliationForm()
        item_fs = StockReconciliationItemFormSet(instance=StockReconciliation(), prefix="items")
    return render(
        request,
        "backoffice/inventory/reconciliation_form.html",
        {"form": form, "is_create": True, "item_formset": item_fs},
    )


@backoffice_required
@require_POST
def reconciliation_item_add(request: HttpRequest) -> HttpResponse:
    formset = add_formset_row(StockReconciliationItemFormSet, "items", request.POST)
    return render(request, "backoffice/inventory/reconciliation_form.html#items_partial", {"item_formset": formset})


@backoffice_required
@require_POST
def reconciliation_item_remove(request: HttpRequest, index: int) -> HttpResponse:
    formset = remove_formset_row(StockReconciliationItemFormSet, "items", request.POST, index)
    return render(request, "backoffice/inventory/reconciliation_form.html#items_partial", {"item_formset": formset})


@backoffice_required
def reconciliation_detail(request: HttpRequest, pk: int) -> HttpResponse:
    reconciliation = get_object_or_404(
        StockReconciliation.objects.select_related("warehouse"),
        pk=pk,
    )
    items = reconciliation.items.select_related("item").all()
    voucher_no = str(pk)
    ledger_entries = StockLedgerEntry.objects.filter(
        voucher_type__in=["Stock Reconciliation", "Stock Reconciliation Cancellation"], voucher_no=voucher_no
    ).select_related("item", "warehouse")
    return render(
        request,
        "backoffice/inventory/reconciliation_detail.html",
        {
            "reconciliation": reconciliation,
            "items": items,
            "ledger_entries": ledger_entries,
        },
    )


@backoffice_required
@require_POST
def reconciliation_submit(request: HttpRequest, pk: int) -> HttpResponse:
    reconciliation = get_object_or_404(StockReconciliation, pk=pk)
    if reconciliation.status == "DRAFT":
        try:
            services.submit_stock_reconciliation(reconciliation, actor=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Reconciliation #{reconciliation.pk} submitted.")
    return redirect("inventory:reconciliation_detail", pk=pk)


@backoffice_required
@require_POST
def reconciliation_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    reconciliation = get_object_or_404(StockReconciliation, pk=pk)
    if reconciliation.status == "SUBMITTED":
        try:
            services.cancel_stock_reconciliation(reconciliation, actor=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Reconciliation #{reconciliation.pk} cancelled.")
    return redirect("inventory:reconciliation_detail", pk=pk)


@backoffice_required
def purchase_receipt_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status")
    supplier = request.GET.get("supplier")
    receipts = PurchaseReceipt.objects.select_related("warehouse").all()
    if status:
        receipts = receipts.filter(status=status)
    if supplier:
        receipts = receipts.filter(supplier_name__icontains=supplier)
    return render(
        request,
        "backoffice/inventory/purchase_receipt_list.html",
        {
            "receipts": receipts,
            "selected_status": status,
            "selected_supplier": supplier or "",
        },
    )


@backoffice_required
def purchase_receipt_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = PurchaseReceiptForm(request.POST)
        item_fs = PurchaseReceiptItemFormSet(request.POST, instance=PurchaseReceipt(), prefix="items")
        if form.is_valid() and item_fs.is_valid():
            with transaction.atomic():
                receipt = form.save()
                item_fs.instance = receipt
                item_fs.save()
            return redirect("inventory:purchase_receipt_detail", pk=receipt.pk)
    else:
        form = PurchaseReceiptForm()
        item_fs = PurchaseReceiptItemFormSet(instance=PurchaseReceipt(), prefix="items")
    return render(
        request,
        "backoffice/inventory/purchase_receipt_form.html",
        {"form": form, "is_create": True, "item_formset": item_fs},
    )


@backoffice_required
@require_POST
def purchase_receipt_item_add(request: HttpRequest) -> HttpResponse:
    formset = add_formset_row(PurchaseReceiptItemFormSet, "items", request.POST)
    return render(request, "backoffice/inventory/purchase_receipt_form.html#items_partial", {"item_formset": formset})


@backoffice_required
@require_POST
def purchase_receipt_item_remove(request: HttpRequest, index: int) -> HttpResponse:
    formset = remove_formset_row(PurchaseReceiptItemFormSet, "items", request.POST, index)
    return render(request, "backoffice/inventory/purchase_receipt_form.html#items_partial", {"item_formset": formset})


@backoffice_required
def purchase_receipt_detail(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(
        PurchaseReceipt.objects.select_related("warehouse"),
        pk=pk,
    )
    items = receipt.items.select_related("item", "item__stock_uom", "uom").all()
    voucher_no = str(pk)
    ledger_entries = StockLedgerEntry.objects.filter(
        voucher_type__in=["Purchase Receipt", "Purchase Receipt Cancellation"], voucher_no=voucher_no
    ).select_related("item", "warehouse")
    return render(
        request,
        "backoffice/inventory/purchase_receipt_detail.html",
        {"receipt": receipt, "items": items, "ledger_entries": ledger_entries},
    )


def _receipt_line_params(request: HttpRequest):
    data = request.GET or request.POST
    prefix = None
    for key in data:
        suffix = key.rsplit("-", 1)[-1]
        if suffix in {"item", "uom", "received_qty"}:
            prefix = key[: -len(suffix) - 1]
            break
    item_id = data.get(f"{prefix}-item") if prefix else data.get("item")
    uom_id = data.get(f"{prefix}-uom") if prefix else data.get("uom")
    qty = data.get(f"{prefix}-received_qty") if prefix else data.get("received_qty")
    return prefix, item_id, uom_id, qty


def _stock_qty_preview_text(item, uom, qty):
    if item is None or uom is None or qty is None:
        return ""
    try:
        factor = item.uom_factor(uom)
        stock_qty = (qty * factor).quantize(TWO_PLACES)
    except ValidationError, InvalidOperation:
        return ""
    qty_display = format(qty.normalize(), "f")
    stock_display = format(stock_qty.normalize(), "f")
    if factor == 1:
        return f"{qty_display} {uom.name}"
    return f"{qty_display} {uom.name} = {stock_display} {item.stock_uom.name}"


@backoffice_required
@require_GET
def purchase_receipt_item_meta(request: HttpRequest) -> HttpResponse:
    prefix, item_id, _uom_id, qty_raw = _receipt_line_params(request)
    item = Item.objects.filter(pk=item_id).select_related("stock_uom").first() if item_id else None
    instance = PurchaseReceiptItem(item=item, uom=item.stock_uom if item else None)
    form = PurchaseReceiptItemForm(instance=instance, prefix=prefix)
    qty = None
    if qty_raw:
        try:
            qty = Decimal(str(qty_raw))
        except InvalidOperation:
            qty = None
    preview = _stock_qty_preview_text(item, item.stock_uom if item else None, qty)
    return render(
        request,
        "backoffice/inventory/purchase_receipt_form.html#uom_widget_partial",
        {
            "uom_field": form["uom"],
            "prefix": prefix or "",
            "preview": preview,
        },
    )


@backoffice_required
@require_GET
def purchase_receipt_stock_qty_preview(request: HttpRequest) -> HttpResponse:
    prefix, item_id, uom_id, qty_raw = _receipt_line_params(request)
    item = Item.objects.filter(pk=item_id).select_related("stock_uom").first() if item_id else None
    uom = UOM.objects.filter(pk=uom_id).first() if uom_id else None
    qty = None
    if qty_raw:
        try:
            qty = Decimal(str(qty_raw))
        except InvalidOperation:
            qty = None
    preview = _stock_qty_preview_text(item, uom, qty)
    return render(
        request,
        "backoffice/inventory/purchase_receipt_form.html#stock_qty_preview_partial",
        {"prefix": prefix or "", "preview": preview},
    )


@backoffice_required
@require_POST
def purchase_receipt_submit(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(PurchaseReceipt.objects.select_related("warehouse"), pk=pk)
    if receipt.status == "DRAFT":
        try:
            services.submit_purchase_receipt(receipt)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Purchase receipt #{receipt.pk} submitted.")
    return redirect("inventory:purchase_receipt_detail", pk=pk)


@backoffice_required
@require_POST
def purchase_receipt_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    receipt = get_object_or_404(PurchaseReceipt.objects.select_related("warehouse"), pk=pk)
    if receipt.status == "SUBMITTED":
        try:
            services.cancel_purchase_receipt(receipt)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Purchase receipt #{receipt.pk} cancelled.")
    return redirect("inventory:purchase_receipt_detail", pk=pk)


@backoffice_required
def stock_ledger_list(request: HttpRequest) -> HttpResponse:
    item_id = request.GET.get("item")
    warehouse_id = request.GET.get("warehouse")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    entries = StockLedgerEntry.objects.select_related("item", "warehouse").all()
    if item_id:
        entries = entries.filter(item_id=item_id)
    if warehouse_id:
        entries = entries.filter(warehouse_id=warehouse_id)
    if date_from:
        entries = entries.filter(posting_date__gte=date_from)
    if date_to:
        entries = entries.filter(posting_date__lte=date_to)
    items = Item.objects.all().order_by("item_name")
    warehouses = Warehouse.objects.all().order_by("name")
    if request.GET.get("export") == "csv":
        return _stock_ledger_list_csv(entries, item_id, warehouse_id, date_from, date_to)
    return render(
        request,
        "backoffice/inventory/stock_ledger_list.html",
        {
            "entries": entries,
            "items": items,
            "warehouses": warehouses,
            "selected_item": item_id,
            "selected_warehouse": warehouse_id,
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
    )


def _stock_ledger_list_csv(entries, item_id, warehouse_id, date_from, date_to):
    """Download the filtered stock ledger as CSV — same rows and order as the page."""
    too_many = over_row_cap(entries)
    if too_many is not None:
        return too_many

    def rows():
        for entry in entries.iterator():
            yield [
                entry.posting_date.isoformat(),
                entry.item.item_name,
                entry.warehouse.name,
                text(entry.voucher_type),
                text(entry.voucher_no),
                money_text(entry.quantity),
                money_text(entry.unit_rate),
                money_text(entry.stock_value_change),
                text(entry.get_variance_type_display() if entry.variance_type else ""),
                money_text(entry.variance_amount) if entry.variance_type else "",
            ]

    filename = export_filename(
        "stock-ledger",
        {"item": item_id, "warehouse": warehouse_id, "from": date_from, "to": date_to},
    )
    return stream_csv(
        filename,
        [
            "Posting date",
            "Item",
            "Warehouse",
            "Voucher type",
            "Voucher no",
            "Qty",
            "Unit rate",
            "Value change",
            "Variance type",
            "Variance amount",
        ],
        rows(),
    )


@backoffice_required
def stock_balance_list(request: HttpRequest) -> HttpResponse:
    warehouse_id = request.GET.get("warehouse")
    bins = Bin.objects.select_related("item", "warehouse").all()
    if warehouse_id:
        bins = bins.filter(warehouse_id=warehouse_id)
    warehouses = Warehouse.objects.all().order_by("name")
    return render(
        request,
        "backoffice/inventory/stock_balance_list.html",
        {
            "bins": bins,
            "warehouses": warehouses,
            "selected_warehouse": warehouse_id,
        },
    )


@backoffice_required
def recipe_list(request: HttpRequest) -> HttpResponse:
    recipes = (
        Recipe.objects.select_related("item")
        .prefetch_related("items__ingredient__stock_uom")
        .order_by("item__item_name")
    )
    rows = [(recipe, recipe_plate_cost(recipe)) for recipe in recipes]
    return render(request, "backoffice/inventory/recipe_list.html", {"rows": rows})


@backoffice_required
def recipe_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = RecipeForm(request.POST)
        ingredient_fs = RecipeItemFormSet(request.POST, instance=Recipe(), prefix="items")
        if form.is_valid() and ingredient_fs.is_valid():
            with transaction.atomic():
                recipe = form.save()
                ingredient_fs.instance = recipe
                ingredient_fs.save()
            return redirect("inventory:recipe_detail", pk=recipe.pk)
    else:
        item_id = request.GET.get("item")
        form = RecipeForm(initial={"item": item_id} if item_id else None)
        ingredient_fs = RecipeItemFormSet(instance=Recipe(), prefix="items")
    return render(
        request,
        "backoffice/inventory/recipe_form.html",
        {"form": form, "is_create": True, "ingredient_formset": ingredient_fs, "plate_cost": None},
    )


@backoffice_required
def recipe_open(request: HttpRequest, item_id: int) -> HttpResponse:
    """Jump to an item's active recipe, or to the create form when it has none."""
    recipe = Recipe.objects.filter(item_id=item_id, is_active=True).first()
    if recipe is None:
        return redirect(f"{reverse('inventory:recipe_create')}?item={item_id}")
    return redirect("inventory:recipe_detail", pk=recipe.pk)


@backoffice_required
def recipe_detail(request: HttpRequest, pk: int) -> HttpResponse:
    recipe = get_object_or_404(
        Recipe.objects.select_related("item").prefetch_related("items__ingredient__stock_uom"),
        pk=pk,
    )
    return render(
        request,
        "backoffice/inventory/recipe_detail.html",
        {"recipe": recipe, "plate_cost": recipe_plate_cost(recipe)},
    )


@backoffice_required
def recipe_update(request: HttpRequest, pk: int) -> HttpResponse:
    recipe = get_object_or_404(Recipe, pk=pk)
    if request.method == "POST":
        form = RecipeForm(request.POST, instance=recipe)
        ingredient_fs = RecipeItemFormSet(request.POST, instance=recipe, prefix="items")
        if form.is_valid() and ingredient_fs.is_valid():
            with transaction.atomic():
                form.save()
                ingredient_fs.save()
            return redirect("inventory:recipe_detail", pk=recipe.pk)
    else:
        form = RecipeForm(instance=recipe)
        ingredient_fs = RecipeItemFormSet(instance=recipe, prefix="items")
    return render(
        request,
        "backoffice/inventory/recipe_form.html",
        {
            "form": form,
            "is_create": False,
            "recipe": recipe,
            "ingredient_formset": ingredient_fs,
            "plate_cost": recipe_plate_cost(recipe),
        },
    )


@backoffice_required
@require_POST
def recipe_item_add(request: HttpRequest) -> HttpResponse:
    formset = add_formset_row(RecipeItemFormSet, "items", request.POST)
    return render(
        request, "backoffice/inventory/recipe_form.html#recipe_items_partial", {"ingredient_formset": formset}
    )


@backoffice_required
@require_POST
def recipe_item_remove(request: HttpRequest, index: int) -> HttpResponse:
    formset = remove_formset_row(RecipeItemFormSet, "items", request.POST, index)
    return render(
        request, "backoffice/inventory/recipe_form.html#recipe_items_partial", {"ingredient_formset": formset}
    )


@backoffice_required
@require_GET
def recipe_plate_cost_preview(request: HttpRequest) -> HttpResponse:
    from types import SimpleNamespace

    data = request.GET
    try:
        total_forms = int(data.get("items-TOTAL_FORMS", "0"))
    except TypeError, ValueError:
        total_forms = 0
    try:
        output_qty = Decimal(str(data.get("output_qty") or "0"))
    except InvalidOperation:
        output_qty = Decimal("0")
    rows = []
    for i in range(total_forms):
        if data.get(f"items-{i}-DELETE"):
            continue
        ingredient_id = data.get(f"items-{i}-ingredient")
        qty_raw = data.get(f"items-{i}-qty")
        if not ingredient_id or not qty_raw:
            continue
        try:
            qty = Decimal(str(qty_raw))
        except InvalidOperation:
            continue
        ingredient = Item.objects.select_related("stock_uom").filter(pk=ingredient_id).first()
        if ingredient is None or qty <= 0:
            continue
        rows.append(SimpleNamespace(ingredient=ingredient, qty=qty))
    preview = _plate_cost(output_qty, rows) if rows and output_qty > 0 else None
    return render(
        request,
        "backoffice/inventory/recipe_form.html#plate_cost_preview_partial",
        {"preview": preview},
    )


@backoffice_required
def food_usage(request: HttpRequest) -> HttpResponse:
    from datetime import date as date_class

    raw = request.GET.get("date")
    try:
        business_date = date_class.fromisoformat(raw) if raw else date_class.today()
    except ValueError:
        business_date = date_class.today()
    usage = compute_food_usage(business_date)
    food_sales = Decimal("0")
    food_sales_error = None
    try:
        from apps.reports.models import PnLConfiguration
        from apps.reports.sources import business_day_window, orders_in_window, sales_by_department

        config = PnLConfiguration.load()
        start, end = business_day_window(business_date, config.business_day_start_hour)
        food_sales, _drinks = sales_by_department(orders_in_window(start, end))
    except ValidationError as e:
        food_sales = Decimal("0")
        food_sales_error = e.messages[0] if e.messages else str(e)
    return render(
        request,
        "backoffice/inventory/food_usage.html",
        {
            "business_date": business_date,
            "usage": usage,
            "food_sales": food_sales,
            "food_sales_error": food_sales_error,
        },
    )
