"""Inventory document services — WAC posting and reversal workflows."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.utils.rounding import money

if TYPE_CHECKING:
    from apps.accounting.models import GLEntry, LedgerAccount

from .models import (
    Bin,
    Item,
    PurchaseReceipt,
    PurchaseReceiptItem,
    StockEntry,
    StockEntryDetail,
    StockLedgerEntry,
    StockReconciliation,
    Warehouse,
    persist_inventory_lifecycle,
)


def _resolve_account(account, label) -> LedgerAccount:
    if account is None:
        raise ValidationError(f"{label} is not configured.")
    if account.disabled:
        raise ValidationError(f"{label} ({account.name}) is disabled.")
    if not account.is_leaf:
        raise ValidationError(f"{label} ({account.name}) must be a leaf account.")
    return account


def _reverse_sles_at_current_wac(*, sles, locked_bins, voucher_type, voucher_no) -> dict[int, Decimal]:
    """Reverse each SLE at current WAC and stamp CANCELLATION_WAC drift. Returns pre-cancel WAC by SLE pk."""

    pre_wac_map = {}
    for sle in sles:
        bin_obj = locked_bins[(sle.item_id, sle.warehouse_id)]
        pre_wac = bin_obj.valuation_rate or Decimal("0")
        pre_wac_map[sle.pk] = pre_wac
        variance = sle.quantity * (pre_wac - sle.unit_rate)
        variance_type = "CANCELLATION_WAC" if variance != 0 else ""
        StockLedgerEntry._create_entry_locked(
            item=sle.item,
            warehouse=sle.warehouse,
            quantity=-sle.quantity,
            voucher_type=voucher_type,
            voucher_no=voucher_no,
            unit_rate=None,
            voucher_detail_no=sle.voucher_detail_no,
            posting_date=timezone.localdate(),
            variance_amount=variance,
            variance_type=variance_type,
            reversal_of_sle_id=sle.pk,
            bin_obj=bin_obj,
        )
    return pre_wac_map


def _cancellation_wac_gl_rows(*, sles, pre_wac_map, counter_account, variance_account) -> list[dict[str, Any]]:
    """SIH at today's value, counter-account at the original booked value, drift to variance."""
    rows = []
    variance_acct = None
    for sle in sles:
        pre_wac = pre_wac_map[sle.pk]
        curr_value = money(abs(sle.quantity) * pre_wac)
        orig_value = money(abs(sle.stock_value_change))
        sih_acct = _resolve_account(sle.warehouse.account, "The warehouse account")
        diff = curr_value - orig_value
        if sle.quantity > 0:
            if curr_value:
                rows.append({"account": sih_acct, "credit": curr_value})
            if orig_value:
                rows.append({"account": counter_account, "debit": orig_value})
            if diff > 0:
                if variance_acct is None:
                    variance_acct = _resolve_account(variance_account, "The inventory price variance account")
                rows.append({"account": variance_acct, "debit": diff})
            elif diff < 0:
                if variance_acct is None:
                    variance_acct = _resolve_account(variance_account, "The inventory price variance account")
                rows.append({"account": variance_acct, "credit": -diff})
        else:
            if curr_value:
                rows.append({"account": sih_acct, "debit": curr_value})
            if orig_value:
                rows.append({"account": counter_account, "credit": orig_value})
            if diff > 0:
                if variance_acct is None:
                    variance_acct = _resolve_account(variance_account, "The inventory price variance account")
                rows.append({"account": variance_acct, "credit": diff})
            elif diff < 0:
                if variance_acct is None:
                    variance_acct = _resolve_account(variance_account, "The inventory price variance account")
                rows.append({"account": variance_acct, "debit": -diff})
    return rows


def _reconciliation_cancel_counter_account(locked) -> LedgerAccount:
    """The non-SIH account that the original reconciliation posted against."""
    from apps.settings.models import ProductionUnit, Restaurant

    restaurant = Restaurant.load()
    if locked.reason == "OPENING_STOCK":
        return _resolve_account(
            restaurant.temporary_opening_account if restaurant else None,
            "The temporary opening account",
        )
    if locked.reason == "ADJUSTMENT":
        return _resolve_account(
            restaurant.stock_adjustment_account if restaurant else None,
            "The stock adjustment account",
        )
    if locked.reason == "CONSUMPTION":
        kitchen_unit = (
            ProductionUnit.objects.select_related("expense_account").filter(department=ProductionUnit.FOOD).first()
        )
        unit_expense = kitchen_unit.expense_account if kitchen_unit is not None else None
        return _resolve_account(
            unit_expense or (restaurant.default_expense_account if restaurant else None),
            "The kitchen expense account",
        )
    return _resolve_account(restaurant.wastage_account if restaurant else None, "The wastage account")


def _post_gl_rows(posting_date, voucher_type, voucher_no, rows, remarks) -> list[GLEntry]:
    """Merge rows per account and post them."""

    from apps.accounting.models import GLEntry

    if not rows:
        return []
    merged: dict[int, dict[str, Any]] = {}
    for r in rows:
        key = r["account"].pk
        if key in merged:
            merged[key]["debit"] = merged[key].get("debit", Decimal("0")) + r.get("debit", Decimal("0"))
            merged[key]["credit"] = merged[key].get("credit", Decimal("0")) + r.get("credit", Decimal("0"))
        else:
            merged[key] = dict(r)
    out = list(merged.values())
    against = ", ".join(r["account"].name for r in out if r.get("credit"))
    for r in out:
        r.setdefault("against", against)
    return GLEntry.post(
        posting_date=posting_date,
        rows=out,
        voucher_type=voucher_type,
        voucher_no=voucher_no,
        remarks=remarks,
    )


@transaction.atomic
def submit_stock_entry(entry) -> None:
    """Post the stock entry: create SLEs for every detail line and mark submitted."""
    from apps.settings.models import ProductionUnit, Restaurant

    locked = StockEntry.objects.select_for_update().get(pk=entry.pk)
    if locked.status != "DRAFT":
        entry.status = locked.status
        return
    restaurant = Restaurant.load()
    if not restaurant or not restaurant.store_warehouse_id or restaurant.store_warehouse.disabled:
        raise ValidationError("Configure an enabled central Store warehouse before submitting.")
    if locked.purpose not in {"MATERIAL_RECEIPT", "MATERIAL_TRANSFER"}:
        raise ValidationError("Unsupported stock entry purpose.")
    if locked.purpose == "MATERIAL_RECEIPT":
        if not locked.mode_of_payment_id:
            raise ValidationError("Select the payment mode that funded this receipt.")
    else:
        if locked.mode_of_payment_id:
            raise ValidationError("Transfers do not have a funding account.")

    targets = {}
    if locked.purpose == "MATERIAL_TRANSFER":
        if not restaurant.default_warehouse_id or restaurant.default_warehouse.disabled:
            raise ValidationError("Configure an enabled Bar / POS sales warehouse before transferring stock.")
        units = {
            unit.department: unit
            for unit in ProductionUnit.objects.select_related("warehouse").filter(
                department__in=[ProductionUnit.FOOD, ProductionUnit.DRINKS]
            )
        }
        food_unit = units.get(ProductionUnit.FOOD)
        drinks_unit = units.get(ProductionUnit.DRINKS)
        if not food_unit or food_unit.warehouse.disabled:
            raise ValidationError("Configure an enabled Kitchen production unit warehouse before transferring stock.")
        if (
            not drinks_unit
            or drinks_unit.warehouse.disabled
            or drinks_unit.warehouse_id != restaurant.default_warehouse_id
        ):
            raise ValidationError("Configure the Drinks production unit to use the enabled Bar / POS sales warehouse.")
        if (
            restaurant.store_warehouse_id
            in {
                restaurant.default_warehouse_id,
                food_unit.warehouse_id,
            }
            or restaurant.default_warehouse_id == food_unit.warehouse_id
        ):
            raise ValidationError("Store, Kitchen, and Bar warehouses must be distinct.")
        targets = {"FOOD": food_unit.warehouse, "DRINKS": restaurant.default_warehouse}

    details = list(locked.items.select_related("item", "item__item_group", "source_warehouse", "target_warehouse"))
    if not details:
        raise ValidationError("Add at least one item before submitting.")
    for detail in details:
        detail.validate_for_submission(restaurant=restaurant, targets=targets)

    bin_keys = {
        (detail.item_id, warehouse_id)
        for detail in details
        for warehouse_id in (
            [restaurant.store_warehouse_id]
            if locked.purpose == "MATERIAL_RECEIPT"
            else [restaurant.store_warehouse_id, targets[detail.item.department].pk]
        )
    }
    for item_id, warehouse_id in sorted(bin_keys):
        Bin.get_or_create_bin_id(item_id, warehouse_id)
    locked_bins = {
        (bin_obj.item_id, bin_obj.warehouse_id): bin_obj
        for bin_obj in Bin.objects.select_for_update()
        .filter(
            item_id__in=[item_id for item_id, _ in bin_keys],
            warehouse_id__in=[warehouse_id for _, warehouse_id in bin_keys],
        )
        .order_by("item_id", "warehouse_id")
    }

    voucher_no = str(locked.pk)
    updated_items = set()
    gl_rows = []
    funding_acct = None
    if locked.purpose == "MATERIAL_RECEIPT":
        from apps.accounting.services import _resolve_payment_account
        from apps.payments.models import ModeOfPayment

        mode_pk = locked.mode_of_payment_id
        mode = ModeOfPayment.objects.filter(pk=mode_pk).first() if mode_pk is not None else None
        if mode is None:
            raise ValidationError("The purchase receipt has no payment mode for its funding legs.")
        funding_acct = _resolve_payment_account(mode)
    for detail in details:
        store_bin = locked_bins[(detail.item_id, restaurant.store_warehouse_id)]
        if locked.purpose == "MATERIAL_RECEIPT":
            detail.source_warehouse = None
            detail.target_warehouse = restaurant.store_warehouse
            stock_qty = detail.stock_qty()
            unit_rate = detail.stock_unit_rate()
            StockLedgerEntry._create_entry_locked(
                item=detail.item,
                warehouse=restaurant.store_warehouse,
                quantity=stock_qty,
                voucher_type="Stock Entry",
                voucher_no=voucher_no,
                unit_rate=unit_rate,
                voucher_detail_no=str(detail.pk),
                posting_date=locked.posting_date,
                inbound_value=detail.amount,
                bin_obj=store_bin,
            )
            detail.item.last_purchase_rate = unit_rate
            updated_items.add(detail.item)
            # GL: Dr SIH / Cr funding account (market purchase, no GRNI)
            if detail.amount:
                sih_account = _resolve_account(restaurant.store_warehouse.account, "The Store warehouse account")
                amount = money(detail.amount)
                gl_rows.append({"account": sih_account, "debit": amount})
                gl_rows.append({"account": funding_acct, "credit": amount})
        else:
            target = targets[detail.item.department]
            detail.source_warehouse = restaurant.store_warehouse
            detail.target_warehouse = target
            outgoing = StockLedgerEntry._create_entry_locked(
                item=detail.item,
                warehouse=restaurant.store_warehouse,
                quantity=-detail.qty,
                voucher_type="Stock Entry",
                voucher_no=voucher_no,
                unit_rate=None,
                voucher_detail_no=str(detail.pk),
                posting_date=locked.posting_date,
                bin_obj=store_bin,
            )
            StockLedgerEntry._create_entry_locked(
                item=detail.item,
                warehouse=target,
                quantity=detail.qty,
                voucher_type="Stock Entry",
                voucher_no=voucher_no,
                unit_rate=outgoing.unit_rate,
                voucher_detail_no=str(detail.pk),
                posting_date=locked.posting_date,
                bin_obj=locked_bins[(detail.item_id, target.pk)],
            )
            # GL: value moves at the transfer rate; shared warehouse accounts net to zero, so no legs.
            value = money(abs(outgoing.stock_value_change))
            if value:
                store_account = _resolve_account(restaurant.store_warehouse.account, "The Store warehouse account")
                target_account = _resolve_account(target.account, f"The {target.name} warehouse account")
                if store_account.pk != target_account.pk:
                    gl_rows.append({"account": target_account, "debit": value})
                    gl_rows.append({"account": store_account, "credit": value})
        detail.save(update_fields=["source_warehouse", "target_warehouse", "updated_at"])
    if updated_items:
        Item.objects.bulk_update(updated_items, ["last_purchase_rate", "updated_at"])
    if gl_rows:
        _post_gl_rows(
            locked.posting_date,
            "Stock Entry",
            voucher_no,
            gl_rows,
            f"Stock Entry {voucher_no} {locked.purpose}",
        )
    locked.status = "SUBMITTED"
    persist_inventory_lifecycle(locked, submit=True, update_fields=["status", "updated_at"])
    entry.status = locked.status


def _transfer_cancel_route(
    detail, original_sles
) -> tuple[Warehouse | None, Warehouse | None, StockLedgerEntry | None, StockLedgerEntry | None]:
    """Return the snapshotted warehouses and original SLE pair for one transfer line."""
    detail_no = str(detail.pk)
    orig_store = next(
        (sle for sle in original_sles if sle.quantity < 0 and sle.voucher_detail_no == detail_no),
        None,
    )
    orig_dest = next(
        (sle for sle in original_sles if sle.quantity > 0 and sle.voucher_detail_no == detail_no),
        None,
    )
    source = detail.source_warehouse or (orig_store.warehouse if orig_store else None)
    target = detail.target_warehouse or (orig_dest.warehouse if orig_dest else None)
    if source is None or target is None:
        raise ValidationError(
            f"This transfer cannot be cancelled — the original warehouses for {detail.item.item_name} are missing."
        )
    if orig_store is None or orig_dest is None:
        raise ValidationError(
            f"This transfer cannot be cancelled — the original stock movements for {detail.item.item_name} are missing."
        )
    return source, target, orig_store, orig_dest


@transaction.atomic
def cancel_stock_entry(entry) -> None:
    """Reverse every SLE created by this entry and mark cancelled."""
    from apps.accounting.models import GLEntry

    locked = StockEntry.objects.select_for_update().get(pk=entry.pk)
    if locked.status != "SUBMITTED":
        entry.status = locked.status
        return
    voucher_no = str(locked.pk)
    original_sles = list(
        StockLedgerEntry.objects.select_related("item", "warehouse", "warehouse__account", "item__item_group").filter(
            voucher_type="Stock Entry", voucher_no=voucher_no
        )
    )
    if locked.purpose == "MATERIAL_TRANSFER":
        details = list(locked.items.select_related("item", "source_warehouse", "target_warehouse").all())
        from apps.settings.models import Restaurant

        restaurant = Restaurant.load()
        routes = [(detail, *_transfer_cancel_route(detail, original_sles)) for detail in details]
        bin_keys = {
            (detail.item_id, warehouse.pk)
            for detail, source, target, _orig_store, _orig_dest in routes
            for warehouse in (source, target)
            if warehouse is not None
        }
        locked_bins = {
            (b.item_id, b.warehouse_id): b
            for b in Bin.objects.select_for_update()
            .filter(
                item_id__in=[k[0] for k in bin_keys],
                warehouse_id__in=[k[1] for k in bin_keys],
            )
            .order_by("item_id", "warehouse_id")
        }
        reversal_rows = []
        for detail, raw_source, raw_target, orig_store, orig_dest in routes:
            if raw_source is None or raw_target is None:
                raise ValidationError(
                    "This transfer cannot be cancelled — the snapshotted warehouses are missing."
                    " Re-run cancel after re-checking the document."
                )
            if orig_store is None or orig_dest is None:
                raise ValidationError("This transfer cannot be cancelled — the original stock movements are missing.")
            source, target = raw_source, raw_target
            dest_bin = locked_bins.get((detail.item_id, target.pk))
            store_bin = locked_bins.get((detail.item_id, source.pk))
            if not dest_bin or not store_bin:
                missing = []
                if not store_bin:
                    missing.append(source.name)
                if not dest_bin:
                    missing.append(target.name)
                raise ValidationError(
                    f"This transfer cannot be cancelled — stock records for {detail.item.item_name} "
                    f"in {', '.join(missing)} are missing."
                )
            if dest_bin.actual_qty < detail.qty:
                used = detail.qty - dest_bin.actual_qty
                raise ValidationError(
                    f"This transfer cannot be cancelled — {used} of {detail.qty} {detail.item.item_name} "
                    f"have already been used from {target.name}. Transfer the remainder back or file an adjustment."
                )
            dest_wac = dest_bin.valuation_rate or Decimal("0")
            curr_value = money(detail.qty * dest_wac)
            orig_value = money(abs(orig_store.stock_value_change))
            unit_rate = orig_store.unit_rate
            variance = curr_value - orig_value
            variance_type = "CANCELLATION_WAC" if variance else ""
            StockLedgerEntry._create_entry_locked(
                item=detail.item,
                warehouse=target,
                quantity=-detail.qty,
                voucher_type="Stock Entry Cancellation",
                voucher_no=voucher_no,
                unit_rate=None,
                voucher_detail_no=str(detail.pk),
                posting_date=timezone.localdate(),
                reversal_of_sle_id=orig_dest.pk,
                bin_obj=dest_bin,
            )
            StockLedgerEntry._create_entry_locked(
                item=detail.item,
                warehouse=source,
                quantity=detail.qty,
                voucher_type="Stock Entry Cancellation",
                voucher_no=voucher_no,
                unit_rate=unit_rate,
                voucher_detail_no=str(detail.pk),
                posting_date=timezone.localdate(),
                variance_amount=variance,
                variance_type=variance_type,
                reversal_of_sle_id=orig_store.pk,
                bin_obj=store_bin,
            )
            if curr_value and source.account_id != target.account_id:
                reversal_rows.append(
                    {
                        "account": _resolve_account(source.account, "The Store warehouse account"),
                        "debit": orig_value,
                    }
                )
                reversal_rows.append(
                    {
                        "account": _resolve_account(target.account, f"The {target.name} warehouse account"),
                        "credit": curr_value,
                    }
                )
                if variance:
                    variance_acct = _resolve_account(
                        restaurant.inventory_price_variance_account if restaurant else None,
                        "The inventory price variance account",
                    )
                    if variance > 0:
                        reversal_rows.append({"account": variance_acct, "debit": variance})
                    else:
                        reversal_rows.append({"account": variance_acct, "credit": -variance})
        gl_originals = list(
            GLEntry.objects.filter(voucher_type="Stock Entry", voucher_no=voucher_no, is_cancelled=False)
        )
        if gl_originals:
            for gl in gl_originals:
                gl.is_cancelled = True
                gl.save(update_fields=["is_cancelled", "updated_at"])
            if reversal_rows:
                _post_gl_rows(timezone.localdate(), "Stock Entry", voucher_no, reversal_rows, "Reversal")
    else:
        from apps.settings.models import Restaurant

        restaurant = Restaurant.load()
        sles = original_sles
        if sles:
            bin_keys = {(s.item_id, s.warehouse_id) for s in sles}
            locked_bins = {
                (b.item_id, b.warehouse_id): b
                for b in Bin.objects.select_for_update()
                .filter(
                    item_id__in=[k[0] for k in bin_keys],
                    warehouse_id__in=[k[1] for k in bin_keys],
                )
                .order_by("item_id", "warehouse_id")
            }
            pre_wac_map = _reverse_sles_at_current_wac(
                sles=sles,
                locked_bins=locked_bins,
                voucher_type="Stock Entry Cancellation",
                voucher_no=voucher_no,
            )
            gl_originals = list(
                GLEntry.objects.filter(voucher_type="Stock Entry", voucher_no=voucher_no, is_cancelled=False)
            )
            if gl_originals:
                for gl in gl_originals:
                    gl.is_cancelled = True
                    gl.save(update_fields=["is_cancelled", "updated_at"])
                from apps.accounting.services import _resolve_payment_account
                from apps.payments.models import ModeOfPayment

                mode_pk = locked.mode_of_payment_id
                mode = ModeOfPayment.objects.filter(pk=mode_pk).first() if mode_pk is not None else None
                if mode is None:
                    raise ValidationError("The stock entry has no payment mode for its funding legs.")
                funding_acct = _resolve_payment_account(mode)
                new_rows = _cancellation_wac_gl_rows(
                    sles=sles,
                    pre_wac_map=pre_wac_map,
                    counter_account=funding_acct,
                    variance_account=restaurant.inventory_price_variance_account if restaurant else None,
                )
                if new_rows:
                    _post_gl_rows(timezone.localdate(), "Stock Entry", voucher_no, new_rows, "Reversal")
            _revert_last_purchase_rates_for_stock_entry(locked, sles)
        locked.status = "CANCELLED"
        persist_inventory_lifecycle(locked, cancel=True, update_fields=["status", "updated_at"])
        entry.status = locked.status
        return
    locked.status = "CANCELLED"
    persist_inventory_lifecycle(locked, cancel=True, update_fields=["status", "updated_at"])
    entry.status = locked.status


@transaction.atomic
def submit_stock_reconciliation(reconciliation, actor=None) -> None:
    """Post adjustment SLEs and GL legs for the four active reconciliation reasons."""
    from apps.settings.models import ProductionUnit

    locked = StockReconciliation.objects.select_for_update().select_related("warehouse").get(pk=reconciliation.pk)
    if locked.status != "DRAFT":
        reconciliation.status = locked.status
        return
    if locked.warehouse.disabled:
        raise ValidationError("The reconciliation warehouse must be enabled.")
    valid_reasons = {value for value, _label in (locked._meta.get_field("reason").choices or [])}
    if locked.reason not in valid_reasons:
        raise ValidationError("A reconciliation reason is required.")

    lines = list(locked.items.select_related("item", "item__item_group"))
    if not lines:
        raise ValidationError("Add at least one item before submitting.")
    for line in lines:
        if line.item.disabled or not line.item.is_stock_item or line.item.has_variants:
            raise ValidationError(f"{line.item.item_name} is not an enabled stock item.")
    if locked.reason in {"OPENING_STOCK", "ADJUSTMENT", "CONSUMPTION"}:
        for line in lines:
            if line.qty < 0:
                raise ValidationError(f"Counted quantity for {line.item.item_name} cannot be negative.")
    else:
        for line in lines:
            if line.qty <= 0:
                raise ValidationError(f"Quantity wasted for {line.item.item_name} must be greater than zero.")
    kitchen_unit = None
    if locked.reason == "CONSUMPTION":
        kitchen_unit = (
            ProductionUnit.objects.select_related("warehouse", "expense_account")
            .filter(department=ProductionUnit.FOOD)
            .first()
        )
        if not kitchen_unit or kitchen_unit.warehouse_id != locked.warehouse_id:
            raise ValidationError("Consumption reconciliation is only allowed for the configured Kitchen warehouse.")
        if any(line.item.department != "FOOD" for line in lines):
            raise ValidationError("Consumption reconciliation accepts FOOD stock items only.")
        for line in lines:
            if line.item.is_sales_item or not line.item.is_stock_item or not line.item.is_purchase_item:
                raise ValidationError(
                    f"{line.item.item_name} must be a stock-tracked, purchasable food ingredient for consumption."
                )

    from apps.settings.models import Restaurant

    restaurant = Restaurant.load()
    sih_acct = _resolve_account(locked.warehouse.account, "The warehouse account")
    counter_accts: dict[str, LedgerAccount] = {}
    if locked.reason == "OPENING_STOCK":
        if StockLedgerEntry.objects.filter(warehouse=locked.warehouse).exists():
            raise ValidationError("Opening Stock is only allowed for a fresh warehouse with no stock history.")
        counter_accts[locked.reason] = _resolve_account(
            restaurant.temporary_opening_account if restaurant else None, "The temporary opening account"
        )
        if counter_accts[locked.reason].report_type == "PROFIT_AND_LOSS":
            raise ValidationError("The temporary opening account must be a balance-sheet account, never a P&L account.")
    elif locked.reason == "ADJUSTMENT":
        counter_accts[locked.reason] = _resolve_account(
            restaurant.stock_adjustment_account if restaurant else None, "The stock adjustment account"
        )
    elif locked.reason == "CONSUMPTION":
        unit_expense = kitchen_unit.expense_account if kitchen_unit is not None else None
        counter_accts[locked.reason] = _resolve_account(
            unit_expense or (restaurant.default_expense_account if restaurant else None),
            "The kitchen expense account",
        )
    else:
        counter_accts[locked.reason] = _resolve_account(
            restaurant.wastage_account if restaurant else None, "The wastage account"
        )

    for line in lines:
        Bin.get_or_create_bin_id(line.item_id, locked.warehouse_id)
    locked_bins = {
        bin_obj.item_id: bin_obj
        for bin_obj in Bin.objects.select_for_update()
        .filter(item_id__in=[line.item_id for line in lines], warehouse_id=locked.warehouse_id)
        .order_by("item_id")
    }
    voucher_no = str(locked.pk)
    gl_rows = []
    for line in lines:
        bin_obj = locked_bins[line.item_id]
        current_qty = bin_obj.actual_qty
        line.current_qty = current_qty
        line.save(update_fields=["current_qty", "updated_at"])
        if locked.reason == "WASTE_DAMAGE":
            available = current_qty - (bin_obj.reserved_qty or Decimal("0"))
            if line.qty > available:
                raise ValidationError(
                    f"Quantity wasted for {line.item.item_name} cannot exceed on-hand stock ({available})."
                )
            sle = StockLedgerEntry._create_entry_locked(
                item=line.item,
                warehouse=locked.warehouse,
                quantity=-line.qty,
                voucher_type="Stock Reconciliation",
                voucher_no=voucher_no,
                unit_rate=None,
                voucher_detail_no=str(line.pk),
                posting_date=locked.posting_date,
                bin_obj=bin_obj,
            )
            amount = money(abs(sle.stock_value_change))
            if amount:
                counter = counter_accts[locked.reason]
                gl_rows.append({"account": counter, "debit": amount, "against": sih_acct.name})
                gl_rows.append({"account": sih_acct, "credit": amount, "against": counter.name})
            continue
        if line.qty < (bin_obj.reserved_qty or Decimal("0")):
            raise ValidationError(
                f"Counted quantity for {line.item.item_name} cannot be below reserved quantity "
                f"({bin_obj.reserved_qty})."
            )
        if locked.reason == "CONSUMPTION" and line.qty > current_qty:
            raise ValidationError(
                f"Counted quantity for {line.item.item_name} cannot exceed the bin "
                f"({current_qty}). Run an Adjustment first."
            )
        difference = line.qty - current_qty
        if difference == 0:
            continue
        rate = None
        if locked.reason == "OPENING_STOCK":
            if difference > 0:
                if line.valuation_rate is None:
                    raise ValidationError(f"Opening stock for {line.item.item_name} requires a valuation rate.")
                rate = line.valuation_rate
        elif bin_obj.actual_qty == 0 and difference > 0:
            if line.valuation_rate is None:
                raise ValidationError(f"A valuation rate is required to seed empty stock for {line.item.item_name}.")
            rate = line.valuation_rate
        sle = StockLedgerEntry._create_entry_locked(
            item=line.item,
            warehouse=locked.warehouse,
            quantity=difference,
            voucher_type="Stock Reconciliation",
            voucher_no=voucher_no,
            unit_rate=rate,
            voucher_detail_no=str(line.pk),
            posting_date=locked.posting_date,
            bin_obj=bin_obj,
        )
        amount = money(abs(sle.stock_value_change))
        if not amount:
            continue
        counter = counter_accts[locked.reason]
        if locked.reason == "CONSUMPTION":
            # Consumption is outbound — the expense absorbs the cost at WAC; SIH is credited.
            gl_rows.append({"account": counter, "debit": amount, "against": sih_acct.name})
            gl_rows.append({"account": sih_acct, "credit": amount, "against": counter.name})
        elif difference > 0:
            gl_rows.append({"account": sih_acct, "debit": amount, "against": counter.name})
            gl_rows.append({"account": counter, "credit": amount, "against": sih_acct.name})
        else:
            gl_rows.append({"account": counter, "debit": amount, "against": sih_acct.name})
            gl_rows.append({"account": sih_acct, "credit": amount, "against": counter.name})
    if gl_rows:
        from apps.accounting.models import GLEntry

        GLEntry.post(
            posting_date=locked.posting_date,
            rows=gl_rows,
            voucher_type="Stock Reconciliation",
            voucher_no=voucher_no,
            remarks=f"Stock Reconciliation {voucher_no} {locked.reason}",
        )
    locked.submitted_by = actor
    locked.submitted_at = timezone.now()
    locked.status = "SUBMITTED"
    persist_inventory_lifecycle(
        locked, submit=True, update_fields=["status", "submitted_by", "submitted_at", "updated_at"]
    )
    reconciliation.status = locked.status


@transaction.atomic
def cancel_stock_reconciliation(reconciliation, actor=None) -> None:
    """Reverse every SLE created by this reconciliation and mark cancelled."""
    locked = StockReconciliation.objects.select_for_update().get(pk=reconciliation.pk)
    if locked.status != "SUBMITTED":
        reconciliation.status = locked.status
        return
    voucher_no = str(locked.pk)
    sles = list(
        StockLedgerEntry.objects.select_related("item", "warehouse", "warehouse__account").filter(
            voucher_type="Stock Reconciliation", voucher_no=voucher_no
        )
    )
    if sles:
        from apps.accounting.models import GLEntry
        from apps.settings.models import Restaurant

        restaurant = Restaurant.load()
        bin_keys = {(s.item_id, s.warehouse_id) for s in sles}
        locked_bins = {
            (b.item_id, b.warehouse_id): b
            for b in Bin.objects.select_for_update()
            .filter(
                item_id__in=[k[0] for k in bin_keys],
                warehouse_id__in=[k[1] for k in bin_keys],
            )
            .order_by("item_id", "warehouse_id")
        }
        pre_wac_map = _reverse_sles_at_current_wac(
            sles=sles,
            locked_bins=locked_bins,
            voucher_type="Stock Reconciliation Cancellation",
            voucher_no=voucher_no,
        )
        gl_originals = list(
            GLEntry.objects.filter(voucher_type="Stock Reconciliation", voucher_no=voucher_no, is_cancelled=False)
        )
        if gl_originals:
            for gl in gl_originals:
                gl.is_cancelled = True
                gl.save(update_fields=["is_cancelled", "updated_at"])
            new_rows = _cancellation_wac_gl_rows(
                sles=sles,
                pre_wac_map=pre_wac_map,
                counter_account=_reconciliation_cancel_counter_account(locked),
                variance_account=restaurant.inventory_price_variance_account if restaurant else None,
            )
            if new_rows:
                _post_gl_rows(timezone.localdate(), "Stock Reconciliation", voucher_no, new_rows, "Reversal")
    locked.cancelled_by = actor
    locked.cancelled_at = timezone.now()
    locked.status = "CANCELLED"
    persist_inventory_lifecycle(
        locked, cancel=True, update_fields=["status", "cancelled_by", "cancelled_at", "updated_at"]
    )
    reconciliation.status = locked.status


def check_receipt_cancel_blocked(receipt) -> bool:
    """Return True if a submitted supplier invoice references the receipt."""
    from apps.accounting.models import SupplierInvoice

    return SupplierInvoice.objects.filter(status=SupplierInvoice.SUBMITTED, purchase_receipt=receipt).exists()


@transaction.atomic
def submit_purchase_receipt(receipt) -> None:
    """Post the receipt: create SLEs for each line into the configured store warehouse."""
    from apps.settings.models import Restaurant

    locked = PurchaseReceipt.objects.select_for_update().get(pk=receipt.pk)
    if locked.status != "DRAFT":
        receipt.status = locked.status
        return
    restaurant = Restaurant.load()
    if not restaurant or not restaurant.store_warehouse_id or restaurant.store_warehouse.disabled:
        raise ValidationError("Configure an enabled central Store warehouse before submitting.")
    if locked.warehouse_id and locked.warehouse_id != restaurant.store_warehouse_id:
        raise ValidationError("Purchase Receipt warehouse must be the configured central Store.")
    if not restaurant.stock_received_but_not_billed_account_id:
        raise ValidationError("Configure the stock received but not billed (GRNI) account before submitting.")
    if not restaurant.store_warehouse.account_id:
        raise ValidationError("Configure the Store warehouse account before submitting.")

    lines = list(locked.items.select_related("item", "uom"))
    if not lines:
        raise ValidationError("Add at least one item before submitting.")
    for line in lines:
        line.validate_for_submission()
        Bin.get_or_create_bin_id(line.item_id, restaurant.store_warehouse_id)
    locked_bins = {
        bin_obj.item_id: bin_obj
        for bin_obj in Bin.objects.select_for_update()
        .filter(item_id__in=[line.item_id for line in lines], warehouse_id=restaurant.store_warehouse_id)
        .order_by("item_id")
    }
    total = Decimal("0")
    updated_items = set()
    for line in lines:
        stock_qty = line.stock_qty()
        unit_rate = line.stock_unit_rate()
        StockLedgerEntry._create_entry_locked(
            item=line.item,
            warehouse=restaurant.store_warehouse,
            quantity=stock_qty,
            voucher_type="Purchase Receipt",
            voucher_no=str(locked.pk),
            unit_rate=unit_rate,
            voucher_detail_no=str(line.pk),
            posting_date=locked.posting_date,
            inbound_value=line.amount,
            bin_obj=locked_bins[line.item_id],
        )
        line.item.last_purchase_rate = unit_rate
        updated_items.add(line.item)
        total += line.amount
    Item.objects.bulk_update(updated_items, ["last_purchase_rate", "updated_at"])
    from apps.accounting.models import GLEntry

    grni_acct = _resolve_account(
        restaurant.stock_received_but_not_billed_account, "The stock received but not billed account"
    )
    sih_acct = _resolve_account(restaurant.store_warehouse.account, "The Store warehouse account")
    stock_total = money(sum((line.amount for line in lines), Decimal("0")))
    if stock_total:
        GLEntry.post(
            posting_date=locked.posting_date,
            rows=[
                {"account": sih_acct, "debit": stock_total, "against": grni_acct.name},
                {"account": grni_acct, "credit": stock_total, "against": sih_acct.name},
            ],
            voucher_type="Purchase Receipt",
            voucher_no=str(locked.pk),
            remarks=f"Purchase Receipt {locked.pk}",
        )
    locked.warehouse = restaurant.store_warehouse
    locked.total = total
    locked.status = "SUBMITTED"
    persist_inventory_lifecycle(locked, submit=True, update_fields=["warehouse", "status", "total", "updated_at"])
    receipt.warehouse = locked.warehouse
    receipt.total = locked.total
    receipt.status = locked.status


@transaction.atomic
def cancel_purchase_receipt(receipt) -> None:
    """Reverse every SLE created by this receipt and mark cancelled."""
    from apps.accounting.models import GLEntry
    from apps.settings.models import Restaurant

    locked = PurchaseReceipt.objects.select_for_update().get(pk=receipt.pk)
    if locked.status != "SUBMITTED":
        receipt.status = locked.status
        return
    if check_receipt_cancel_blocked(locked):
        raise ValidationError("Cancel the supplier invoice(s) and payment(s) for this receipt first.")
    voucher_no = str(locked.pk)
    sles = list(
        StockLedgerEntry.objects.select_related("item", "warehouse", "warehouse__account").filter(
            voucher_type="Purchase Receipt", voucher_no=voucher_no
        )
    )
    if not sles:
        _revert_last_purchase_rates(locked)
        locked.status = "CANCELLED"
        persist_inventory_lifecycle(locked, cancel=True, update_fields=["status", "updated_at"])
        receipt.status = locked.status
        return
    restaurant = Restaurant.load()
    bin_keys = {(s.item_id, s.warehouse_id) for s in sles}
    locked_bins = {
        (b.item_id, b.warehouse_id): b
        for b in Bin.objects.select_for_update()
        .filter(
            item_id__in=[k[0] for k in bin_keys],
            warehouse_id__in=[k[1] for k in bin_keys],
        )
        .order_by("item_id", "warehouse_id")
    }
    pre_wac_map = _reverse_sles_at_current_wac(
        sles=sles,
        locked_bins=locked_bins,
        voucher_type="Purchase Receipt Cancellation",
        voucher_no=voucher_no,
    )
    gl_originals = list(
        GLEntry.objects.filter(voucher_type="Purchase Receipt", voucher_no=voucher_no, is_cancelled=False)
    )
    if gl_originals:
        for gl in gl_originals:
            gl.is_cancelled = True
            gl.save(update_fields=["is_cancelled", "updated_at"])
        grni_acct = _resolve_account(
            restaurant.stock_received_but_not_billed_account,
            "The stock received but not billed account",
        )
        new_rows = _cancellation_wac_gl_rows(
            sles=sles,
            pre_wac_map=pre_wac_map,
            counter_account=grni_acct,
            variance_account=restaurant.inventory_price_variance_account if restaurant else None,
        )
        if new_rows:
            _post_gl_rows(timezone.localdate(), "Purchase Receipt", voucher_no, new_rows, "Reversal")
    _revert_last_purchase_rates(locked)
    locked.status = "CANCELLED"
    persist_inventory_lifecycle(locked, cancel=True, update_fields=["status", "updated_at"])
    receipt.status = locked.status


def _revert_last_purchase_rates(receipt) -> None:
    """Restore each item's last_purchase_rate from the prior submitted receipt."""
    lines = list(receipt.items.select_related("item").all())
    if not lines:
        return
    items_to_update = []
    for line in lines:
        prior = (
            PurchaseReceiptItem.objects.filter(
                item=line.item,
                purchase_receipt__status="SUBMITTED",
            )
            .exclude(purchase_receipt=receipt)
            .select_related("purchase_receipt")
            .order_by("-purchase_receipt__posting_date", "-purchase_receipt__pk")
            .first()
        )
        line.item.last_purchase_rate = prior.stock_unit_rate() if prior else None
        items_to_update.append(line.item)
    Item.objects.bulk_update(items_to_update, ["last_purchase_rate", "updated_at"])


def _revert_last_purchase_rates_for_stock_entry(entry, sles) -> None:
    """Revert last_purchase_rate for stock entry material receipt cancel."""
    if not sles:
        return
    seen = set()
    items_to_update = []
    for sle in sles:
        if sle.item_id in seen:
            continue
        seen.add(sle.item_id)
        prior = (
            StockEntryDetail.objects.filter(
                item_id=sle.item_id,
                stock_entry__status="SUBMITTED",
                stock_entry__purpose="MATERIAL_RECEIPT",
            )
            .exclude(stock_entry=entry)
            .select_related("stock_entry")
            .order_by("-stock_entry__posting_date", "-stock_entry__pk")
            .first()
        )
        sle.item.last_purchase_rate = prior.stock_unit_rate() if prior else None
        items_to_update.append(sle.item)
    if items_to_update:
        Item.objects.bulk_update(items_to_update, ["last_purchase_rate", "updated_at"])
