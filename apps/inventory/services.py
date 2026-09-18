"""Inventory document services — WAC posting, reversal workflows, and recipe usage."""

from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.utils.rounding import TWO_PLACES, money

from .models import (
    Bin,
    Item,
    PurchaseReceipt,
    PurchaseReceiptItem,
    Recipe,
    StockEntry,
    StockEntryDetail,
    StockLedgerEntry,
    StockReconciliation,
)


@dataclass
class DishShare:
    dish_name: str
    qty: Decimal


@dataclass
class IngredientUsage:
    ingredient_id: int
    ingredient_name: str
    uom: str
    theoretical_qty: Decimal = Decimal("0")
    consumption_qty: Decimal = Decimal("0")
    waste_qty: Decimal = Decimal("0")
    actual_qty: Decimal = Decimal("0")
    variance_qty: Decimal = Decimal("0")
    rate: Decimal = Decimal("0")
    theoretical_amount: Decimal = Decimal("0")
    consumption_amount: Decimal = Decimal("0")
    waste_amount: Decimal = Decimal("0")
    actual_amount: Decimal = Decimal("0")
    variance_amount: Decimal = Decimal("0")
    rate_estimated: bool = False
    dishes: list = field(default_factory=list)


@dataclass
class UnmappedDish:
    item_name: str
    qty: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")


@dataclass
class FoodUsage:
    usages: list = field(default_factory=list)
    unmapped: list = field(default_factory=list)
    theoretical_cost: Decimal = Decimal("0")
    actual_cost: Decimal = Decimal("0")
    variance_cost: Decimal = Decimal("0")


def _kitchen_warehouse():
    from apps.settings.models import ProductionUnit

    kitchen = ProductionUnit.objects.select_related("warehouse").filter(department=ProductionUnit.FOOD).first()
    return kitchen.warehouse if kitchen else None


def _ingredient_rate(ingredient, kitchen, actual_qty, actual_amount):
    """Weighted actual-SLE rate, else Kitchen bin WAC, else last purchase rate, else 0."""
    if actual_qty:
        return money(actual_amount / actual_qty)
    if kitchen is not None:
        bin_obj = Bin.objects.filter(item=ingredient, warehouse=kitchen).first()
        if bin_obj is not None and bin_obj.valuation_rate:
            return bin_obj.valuation_rate
    if ingredient.last_purchase_rate:
        return ingredient.last_purchase_rate
    return Decimal("0")


def recipe_plate_cost(recipe):
    """Display-only cost of one portion: sum(qty × Kitchen WAC or last rate) / output."""
    items = recipe.items.select_related("ingredient") if recipe.pk else []
    return _plate_cost(recipe.output_qty, items)


def _plate_cost(output_qty, rows):
    kitchen = _kitchen_warehouse()
    total = Decimal("0")
    for line in rows:
        ingredient = line.ingredient
        rate = None
        if kitchen is not None:
            bin_obj = Bin.objects.filter(item=ingredient, warehouse=kitchen).first()
            if bin_obj is not None and bin_obj.valuation_rate:
                rate = bin_obj.valuation_rate
        if rate is None:
            rate = ingredient.last_purchase_rate or Decimal("0")
        total += line.qty * rate
    if not output_qty:
        return Decimal("0")
    return money(total / output_qty)


def compute_food_usage(business_date):
    """Single source for AvT + Daily P&L: theoretical (recipe × sales) vs actual (kitchen SLEs)."""
    from apps.orders.models import OrderItem
    from apps.reports.models import PnLConfiguration
    from apps.reports.sources import TWO, ZERO, business_day_window, orders_in_window

    config = PnLConfiguration.load()
    start, end = business_day_window(business_date, config.business_day_start_hour)
    orders = orders_in_window(start, end)
    lines = (
        OrderItem.objects.filter(
            Q(department="FOOD") | Q(department__isnull=True, item__department="FOOD"),
            order_id__in=[o.pk for o in orders],
        )
        .select_related("item")
        .order_by("pk")
    )
    item_ids = {line.item_id for line in lines}
    recipes = {
        recipe.item_id: recipe
        for recipe in Recipe.objects.filter(is_active=True, item_id__in=item_ids).prefetch_related(
            "items__ingredient__stock_uom"
        )
    }
    theoretical = {}
    unmapped = {}

    def _usage(ingredient):
        usage = theoretical.get(ingredient.pk)
        if usage is None:
            usage = theoretical[ingredient.pk] = IngredientUsage(
                ingredient_id=ingredient.pk,
                ingredient_name=ingredient.item_name,
                uom=ingredient.stock_uom.name if ingredient.stock_uom_id else "",
            )
        return usage

    for line in lines:
        recipe = recipes.get(line.item_id)
        if recipe is None or not recipe.output_qty:
            dish = unmapped.setdefault(line.item_id, UnmappedDish(item_name=line.item_name or line.item.item_name))
            dish.qty += line.qty
            dish.amount += line.amount
            continue
        factor = line.qty / recipe.output_qty
        dish_name = line.item_name or line.item.item_name
        for recipe_item in recipe.items.all():
            qty_add = factor * recipe_item.qty
            usage = _usage(recipe_item.ingredient)
            usage.theoretical_qty += qty_add
            usage.dishes.append(DishShare(dish_name=dish_name, qty=qty_add))

    kitchen = _kitchen_warehouse()
    actual = {}
    if kitchen is not None:
        recs = list(
            StockReconciliation.objects.filter(
                status="SUBMITTED",
                reason__in=["CONSUMPTION", "WASTE_DAMAGE"],
                posting_date=business_date,
                warehouse=kitchen,
            )
        )
        reason_by_no = {str(rec.pk): rec.reason for rec in recs}
        if reason_by_no:
            sles = (
                StockLedgerEntry.objects.filter(
                    voucher_type="Stock Reconciliation",
                    voucher_no__in=list(reason_by_no),
                    quantity__lt=0,
                )
                .select_related("item", "item__stock_uom")
                .order_by("pk")
            )
            for sle in sles:
                entry = actual.setdefault(
                    sle.item_id,
                    {
                        "ingredient": sle.item,
                        "consumption_qty": ZERO,
                        "waste_qty": ZERO,
                        "consumption_amount": ZERO,
                        "waste_amount": ZERO,
                        "amount": ZERO,
                    },
                )
                qty = abs(sle.quantity)
                amount = (qty * sle.unit_rate).quantize(TWO)
                if reason_by_no[sle.voucher_no] == "WASTE_DAMAGE":
                    entry["waste_qty"] += qty
                    entry["waste_amount"] += amount
                else:
                    entry["consumption_qty"] += qty
                    entry["consumption_amount"] += amount
                entry["amount"] += amount

    usages = []
    ingredients = {}
    for usage in theoretical.values():
        ingredients[usage.ingredient_id] = usage
    for item_id, entry in actual.items():
        if item_id not in ingredients:
            ingredient = entry["ingredient"]
            ingredients[item_id] = IngredientUsage(
                ingredient_id=item_id,
                ingredient_name=ingredient.item_name,
                uom=ingredient.stock_uom.name if ingredient.stock_uom_id else "",
            )
    for usage in ingredients.values():
        entry = actual.get(usage.ingredient_id, {})
        usage.consumption_qty = entry.get("consumption_qty", ZERO)
        usage.waste_qty = entry.get("waste_qty", ZERO)
        usage.actual_qty = (usage.consumption_qty + usage.waste_qty).quantize(TWO_PLACES)
        usage.theoretical_qty = usage.theoretical_qty.quantize(TWO_PLACES)
        usage.consumption_amount = money(entry.get("consumption_amount", ZERO))
        usage.waste_amount = money(entry.get("waste_amount", ZERO))
        usage.actual_amount = money(entry.get("amount", ZERO))
        ingredient = entry.get("ingredient")
        if ingredient is None:
            ingredient = Item.objects.select_related("stock_uom").get(pk=usage.ingredient_id)
        usage.rate = _ingredient_rate(ingredient, kitchen, usage.actual_qty, usage.actual_amount)
        usage.rate_estimated = usage.rate == 0
        usage.theoretical_amount = money(usage.theoretical_qty * usage.rate)
        usage.variance_qty = (usage.theoretical_qty - usage.actual_qty).quantize(TWO_PLACES)
        usage.variance_amount = money(usage.theoretical_amount - usage.actual_amount)
        usages.append(usage)
    usages.sort(key=lambda u: u.ingredient_name)
    unmapped_list = sorted(unmapped.values(), key=lambda d: d.item_name)
    for dish in unmapped_list:
        dish.qty = dish.qty.quantize(TWO_PLACES)
        dish.amount = money(dish.amount)
    return FoodUsage(
        usages=usages,
        unmapped=unmapped_list,
        theoretical_cost=money(sum((u.theoretical_amount for u in usages), ZERO)),
        actual_cost=money(sum((u.actual_amount for u in usages), ZERO)),
        variance_cost=money(sum((u.variance_amount for u in usages), ZERO)),
    )


def _resolve_account(account, label):
    if account is None:
        raise ValidationError(f"{label} is not configured.")
    if account.disabled:
        raise ValidationError(f"{label} ({account.name}) is disabled.")
    if not account.is_leaf:
        raise ValidationError(f"{label} ({account.name}) must be a leaf account.")
    return account


def _post_gl_rows(posting_date, voucher_type, voucher_no, rows, remarks):
    """Merge rows per account and post via GLEntry."""

    from apps.accounting.models import GLEntry

    if not rows:
        return []
    merged = {}
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
def submit_stock_entry(entry):
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

        mode = ModeOfPayment.objects.get(pk=locked.mode_of_payment_id)
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
                prevent_negative=False,
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
                prevent_negative=True,
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
                prevent_negative=False,
                posting_date=locked.posting_date,
                bin_obj=locked_bins[(detail.item_id, target.pk)],
            )
        detail.save(update_fields=["source_warehouse", "target_warehouse", "updated_at"])
    if updated_items:
        Item.objects.bulk_update(updated_items, ["last_purchase_rate", "updated_at"])
    if gl_rows and locked.purpose == "MATERIAL_RECEIPT":
        _post_gl_rows(
            locked.posting_date,
            "Stock Entry",
            voucher_no,
            gl_rows,
            f"Stock Entry {voucher_no} MATERIAL_RECEIPT",
        )
    locked.status = "SUBMITTED"
    locked.save(update_fields=["status", "updated_at"])
    entry.status = locked.status


@transaction.atomic
def cancel_stock_entry(entry):
    """Reverse every SLE created by this entry and mark cancelled."""
    from apps.accounting.models import GLEntry

    locked = StockEntry.objects.select_for_update().get(pk=entry.pk)
    if locked.status != "SUBMITTED":
        entry.status = locked.status
        return
    voucher_no = str(locked.pk)
    original_sles = list(
        StockLedgerEntry.objects.select_related("item", "warehouse", "item__item_group").filter(
            voucher_type="Stock Entry", voucher_no=voucher_no
        )
    )
    if locked.purpose == "MATERIAL_TRANSFER":
        details = list(locked.items.select_related("item").all())
        from apps.settings.models import ProductionUnit, Restaurant

        restaurant = Restaurant.load()
        targets = {}
        if restaurant:
            units = {u.department: u for u in ProductionUnit.objects.all()}
            food_unit = units.get(ProductionUnit.FOOD)
            if food_unit:
                targets["FOOD"] = food_unit.warehouse
            if restaurant.default_warehouse_id:
                targets["DRINKS"] = restaurant.default_warehouse
        bin_keys = set()
        for d in details:
            bin_keys.add((d.item_id, restaurant.store_warehouse_id))
            tgt = targets.get(d.item.department)
            if tgt:
                bin_keys.add((d.item_id, tgt.pk if hasattr(tgt, "pk") else tgt))
        locked_bins = {
            (b.item_id, b.warehouse_id): b
            for b in Bin.objects.select_for_update()
            .filter(
                item_id__in=[k[0] for k in bin_keys],
                warehouse_id__in=[k[1] for k in bin_keys],
            )
            .order_by("item_id", "warehouse_id")
        }
        for detail in details:
            tgt = targets.get(detail.item.department)
            if not tgt:
                continue
            tgt_id = tgt.pk if hasattr(tgt, "pk") else tgt
            dest_bin = locked_bins.get((detail.item_id, tgt_id))
            store_bin = locked_bins.get((detail.item_id, restaurant.store_warehouse_id))
            if not dest_bin or not store_bin:
                continue
            dest_wac = dest_bin.valuation_rate or Decimal("0")
            orig_dest = next(
                (
                    s
                    for s in original_sles
                    if s.warehouse_id == tgt_id and s.quantity > 0 and s.voucher_detail_no == str(detail.pk)
                ),
                None,
            )
            orig_store = next(
                (
                    s
                    for s in original_sles
                    if s.warehouse_id == restaurant.store_warehouse_id
                    and s.quantity < 0
                    and s.voucher_detail_no == str(detail.pk)
                ),
                None,
            )
            StockLedgerEntry._create_entry_locked(
                item=detail.item,
                warehouse=tgt,
                quantity=-detail.qty,
                voucher_type="Stock Entry Cancellation",
                voucher_no=voucher_no,
                unit_rate=None,
                voucher_detail_no=str(detail.pk),
                prevent_negative=True,
                posting_date=locked.posting_date,
                reversal_of_sle_id=orig_dest.pk if orig_dest else None,
                bin_obj=dest_bin,
            )
            StockLedgerEntry._create_entry_locked(
                item=detail.item,
                warehouse=restaurant.store_warehouse,
                quantity=detail.qty,
                voucher_type="Stock Entry Cancellation",
                voucher_no=voucher_no,
                unit_rate=dest_wac,
                voucher_detail_no=str(detail.pk),
                prevent_negative=False,
                posting_date=locked.posting_date,
                reversal_of_sle_id=orig_store.pk if orig_store else None,
                bin_obj=store_bin,
            )
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
                    voucher_type="Stock Entry Cancellation",
                    voucher_no=voucher_no,
                    unit_rate=None,
                    voucher_detail_no=sle.voucher_detail_no,
                    prevent_negative=sle.quantity > 0,
                    posting_date=locked.posting_date,
                    variance_amount=variance,
                    variance_type=variance_type,
                    reversal_of_sle_id=sle.pk,
                    bin_obj=bin_obj,
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

                mode = ModeOfPayment.objects.get(pk=locked.mode_of_payment_id)
                funding_acct = _resolve_payment_account(mode)
                # Original as-bought money per receipt line — qty × per-stock-unit rate can round.
                detail_amounts = {}
                if locked.purpose == "MATERIAL_RECEIPT":
                    detail_amounts = {d.pk: d.amount for d in StockEntryDetail.objects.filter(stock_entry_id=locked.pk)}
                # SIH moves by the bin's current value; the funding leg returns the original
                # money. Any difference (WAC drift or rate rounding) goes to the variance account.
                line_amounts = []
                for sle in sles:
                    pre_wac = pre_wac_map[sle.pk]
                    curr_amount = (sle.quantity * pre_wac).quantize(Decimal("0.01"))
                    orig_amount = (sle.quantity * sle.unit_rate).quantize(Decimal("0.01"))
                    detail_amount = detail_amounts.get(int(sle.voucher_detail_no or 0))
                    if detail_amount:
                        orig_amount = detail_amount
                    line_amounts.append((sle, curr_amount, orig_amount))
                variance_acct = None
                if any(curr != orig for _sle, curr, orig in line_amounts):
                    variance_acct = _resolve_account(
                        restaurant.inventory_price_variance_account if restaurant else None,
                        "The inventory price variance account",
                    )
                new_rows = []
                for sle, curr_amount, orig_amount in line_amounts:
                    sih_acct = _resolve_account(sle.warehouse.account, "The warehouse account")
                    new_rows.append({"account": sih_acct, "credit": curr_amount})
                    new_rows.append({"account": funding_acct, "debit": orig_amount})
                    diff = curr_amount - orig_amount
                    if diff != 0:
                        if variance_acct is None:
                            raise ValidationError("The inventory price variance account is not configured.")
                        if diff > 0:
                            new_rows.append({"account": variance_acct, "debit": diff})
                        else:
                            new_rows.append({"account": variance_acct, "credit": -diff})
                if new_rows:
                    _post_gl_rows(locked.posting_date, "Stock Entry", voucher_no, new_rows, "Reversal")
            _revert_last_purchase_rates_for_stock_entry(locked, sles)
        locked.status = "CANCELLED"
        locked.save(update_fields=["status", "updated_at"])
        entry.status = locked.status
        return
    locked.status = "CANCELLED"
    locked.save(update_fields=["status", "updated_at"])
    entry.status = locked.status


@transaction.atomic
def submit_stock_reconciliation(reconciliation, actor=None):
    """Post adjustment SLEs and GL legs for the four active reconciliation reasons."""
    from apps.settings.models import ProductionUnit

    locked = StockReconciliation.objects.select_for_update().select_related("warehouse").get(pk=reconciliation.pk)
    if locked.status != "DRAFT":
        reconciliation.status = locked.status
        return
    if locked.warehouse.disabled:
        raise ValidationError("The reconciliation warehouse must be enabled.")
    valid_reasons = {value for value, _label in locked._meta.get_field("reason").choices}
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
    adjustment_acct = None
    opening_acct = None
    expense_acct = None
    wastage_acct = None
    if locked.reason == "OPENING_STOCK":
        if StockLedgerEntry.objects.filter(warehouse=locked.warehouse).exists():
            raise ValidationError("Opening Stock is only allowed for a fresh warehouse with no stock history.")
        opening_acct = _resolve_account(
            restaurant.temporary_opening_account if restaurant else None, "The temporary opening account"
        )
        if opening_acct.report_type == "PROFIT_AND_LOSS":
            raise ValidationError("The temporary opening account must be a balance-sheet account, never a P&L account.")
    elif locked.reason == "ADJUSTMENT":
        adjustment_acct = _resolve_account(
            restaurant.stock_adjustment_account if restaurant else None, "The stock adjustment account"
        )
    elif locked.reason == "CONSUMPTION":
        unit_expense = kitchen_unit.expense_account if kitchen_unit is not None else None
        expense_acct = _resolve_account(
            unit_expense or (restaurant.default_expense_account if restaurant else None),
            "The kitchen expense account",
        )
    else:
        wastage_acct = _resolve_account(restaurant.wastage_account if restaurant else None, "The wastage account")

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
                prevent_negative=True,
                posting_date=locked.posting_date,
                bin_obj=bin_obj,
            )
            amount = (abs(sle.quantity) * sle.unit_rate).quantize(Decimal("0.01"))
            if amount:
                gl_rows.append({"account": wastage_acct, "debit": amount, "against": sih_acct.name})
                gl_rows.append({"account": sih_acct, "credit": amount, "against": wastage_acct.name})
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
            prevent_negative=False,
            posting_date=locked.posting_date,
            bin_obj=bin_obj,
        )
        amount = (abs(sle.quantity) * sle.unit_rate).quantize(Decimal("0.01"))
        if not amount:
            continue
        if locked.reason == "OPENING_STOCK":
            if difference > 0:
                gl_rows.append({"account": sih_acct, "debit": amount, "against": opening_acct.name})
                gl_rows.append({"account": opening_acct, "credit": amount, "against": sih_acct.name})
            else:
                gl_rows.append({"account": opening_acct, "debit": amount, "against": sih_acct.name})
                gl_rows.append({"account": sih_acct, "credit": amount, "against": opening_acct.name})
        elif locked.reason == "ADJUSTMENT":
            if difference > 0:
                gl_rows.append({"account": sih_acct, "debit": amount, "against": adjustment_acct.name})
                gl_rows.append({"account": adjustment_acct, "credit": amount, "against": sih_acct.name})
            else:
                gl_rows.append({"account": adjustment_acct, "debit": amount, "against": sih_acct.name})
                gl_rows.append({"account": sih_acct, "credit": amount, "against": adjustment_acct.name})
        else:
            gl_rows.append({"account": expense_acct, "debit": amount, "against": sih_acct.name})
            gl_rows.append({"account": sih_acct, "credit": amount, "against": expense_acct.name})
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
    locked.save(update_fields=["status", "submitted_by", "submitted_at", "updated_at"])
    reconciliation.status = locked.status


@transaction.atomic
def cancel_stock_reconciliation(reconciliation, actor=None):
    """Reverse every SLE created by this reconciliation and mark cancelled."""
    locked = StockReconciliation.objects.select_for_update().get(pk=reconciliation.pk)
    if locked.status != "SUBMITTED":
        reconciliation.status = locked.status
        return
    voucher_no = str(locked.pk)
    sles = list(
        StockLedgerEntry.objects.select_related("item", "warehouse").filter(
            voucher_type="Stock Reconciliation", voucher_no=voucher_no
        )
    )
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
        for sle in sles:
            bin_obj = locked_bins[(sle.item_id, sle.warehouse_id)]
            StockLedgerEntry._create_entry_locked(
                item=sle.item,
                warehouse=sle.warehouse,
                quantity=-sle.quantity,
                voucher_type="Stock Reconciliation Cancellation",
                voucher_no=voucher_no,
                unit_rate=None,
                voucher_detail_no=sle.voucher_detail_no,
                prevent_negative=sle.quantity > 0,
                posting_date=locked.posting_date,
                reversal_of_sle_id=sle.pk,
                bin_obj=bin_obj,
            )
        from apps.accounting.models import GLEntry

        gl_rows = list(
            GLEntry.objects.filter(voucher_type="Stock Reconciliation", voucher_no=voucher_no, is_cancelled=False)
        )
        if gl_rows:
            for gl in gl_rows:
                gl.is_cancelled = True
                gl.save(update_fields=["is_cancelled", "updated_at"])
            GLEntry.post(
                posting_date=locked.posting_date,
                rows=[
                    {
                        "account": gl.account,
                        "debit": gl.credit,
                        "credit": gl.debit,
                        "against": gl.against,
                    }
                    for gl in gl_rows
                ],
                voucher_type="Stock Reconciliation",
                voucher_no=voucher_no,
                remarks="Reversal",
            )
    locked.cancelled_by = actor
    locked.cancelled_at = timezone.now()
    locked.status = "CANCELLED"
    locked.save(update_fields=["status", "cancelled_by", "cancelled_at", "updated_at"])
    reconciliation.status = locked.status


def check_receipt_cancel_blocked(receipt):
    """Return True if receipt has downstream SUBMITTED invoice or allocated payment."""
    from apps.accounting.models import SupplierInvoice

    return SupplierInvoice.objects.filter(status=SupplierInvoice.SUBMITTED, purchase_receipt=receipt).exists()


@transaction.atomic
def submit_purchase_receipt(receipt):
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
            prevent_negative=False,
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
    locked.save(update_fields=["warehouse", "status", "total", "updated_at"])
    receipt.warehouse = locked.warehouse
    receipt.total = locked.total
    receipt.status = locked.status


@transaction.atomic
def cancel_purchase_receipt(receipt):
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
        StockLedgerEntry.objects.select_related("item", "warehouse").filter(
            voucher_type="Purchase Receipt", voucher_no=voucher_no
        )
    )
    if not sles:
        _revert_last_purchase_rates(locked)
        locked.status = "CANCELLED"
        locked.save(update_fields=["status", "updated_at"])
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
            voucher_type="Purchase Receipt Cancellation",
            voucher_no=voucher_no,
            unit_rate=None,
            voucher_detail_no=sle.voucher_detail_no,
            prevent_negative=True,
            posting_date=locked.posting_date,
            variance_amount=variance,
            variance_type=variance_type,
            reversal_of_sle_id=sle.pk,
            bin_obj=bin_obj,
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
        has_drift = False
        for sle in sles:
            pre_wac = pre_wac_map[sle.pk]
            curr_amount = (sle.quantity * pre_wac).quantize(Decimal("0.01"))
            orig_amount = (sle.quantity * sle.unit_rate).quantize(Decimal("0.01"))
            if curr_amount != orig_amount:
                has_drift = True
                break
        variance_acct = None
        if has_drift:
            variance_acct = _resolve_account(
                restaurant.inventory_price_variance_account,
                "The inventory price variance account",
            )
        new_rows = []
        for sle in sles:
            pre_wac = pre_wac_map[sle.pk]
            curr_amount = (sle.quantity * pre_wac).quantize(Decimal("0.01"))
            orig_amount = (sle.quantity * sle.unit_rate).quantize(Decimal("0.01"))
            sih_acct = _resolve_account(sle.warehouse.account, "The warehouse account")
            new_rows.append({"account": sih_acct, "credit": curr_amount})
            new_rows.append({"account": grni_acct, "debit": orig_amount})
            diff = curr_amount - orig_amount
            if diff != 0:
                if variance_acct is None:
                    raise ValidationError("The inventory price variance account is not configured.")
                if diff > 0:
                    new_rows.append({"account": variance_acct, "debit": diff})
                else:
                    new_rows.append({"account": variance_acct, "credit": -diff})
        if new_rows:
            _post_gl_rows(locked.posting_date, "Purchase Receipt", voucher_no, new_rows, "Reversal")
    _revert_last_purchase_rates(locked)
    locked.status = "CANCELLED"
    locked.save(update_fields=["status", "updated_at"])
    receipt.status = locked.status


def _revert_last_purchase_rates(receipt):
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


def _revert_last_purchase_rates_for_stock_entry(entry, sles):
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
