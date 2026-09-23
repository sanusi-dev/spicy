"""GL posting services — order settlement, cancellation, refund, variance, and payables postings."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.orders.models import SUBMITTED
from apps.payments.models import PaymentGLMapping
from apps.settings.models import Restaurant
from apps.utils.rounding import money

from .models import GLEntry, JournalEntry, JournalEntryAccount, LedgerAccount
from .payables_models import SupplierInvoiceItem


def _income_account_for(department):
    """Return the ProductionUnit income account for a department, or None."""
    from apps.settings.models import ProductionUnit

    unit = ProductionUnit.objects.filter(department=department).select_related("income_account").first()
    if unit is not None and unit.income_account_id:
        return unit.income_account
    return None


def _expense_account_for(department):
    """Return the ProductionUnit expense account for a department, or None."""
    from apps.settings.models import ProductionUnit

    unit = ProductionUnit.objects.filter(department=department).select_related("expense_account").first()
    if unit is not None and unit.expense_account_id:
        return unit.expense_account
    return None


def _resolve_required_account(account, *, label):
    if account is None:
        raise ValidationError(f"{label} is not configured.")
    if account.disabled:
        raise ValidationError(f"{label} ({account.name}) is disabled.")
    if not account.is_leaf:
        raise ValidationError(f"{label} ({account.name}) must be a leaf account.")
    return account


def _resolve_payment_account(mode):
    try:
        mapping = PaymentGLMapping.objects.select_related("default_account").get(mode_of_payment=mode)
    except PaymentGLMapping.DoesNotExist:
        raise ValidationError(f"Payment mode {mode.name} has no GL mapping.") from None
    account = mapping.default_account
    if account is None:
        raise ValidationError(f"Payment mode {mode.name} has no GL mapping.")
    if account.disabled:
        raise ValidationError(f"Payment mode {mode.name}'s GL account ({account.name}) is disabled.")
    if not account.is_leaf:
        raise ValidationError(f"Payment mode {mode.name}'s GL account ({account.name}) must be a leaf account.")
    if account.account_type == LedgerAccount.INCOME:
        raise ValidationError(f"Payment mode {mode.name}'s GL account ({account.name}) is a sales account.")
    return account


def _order_lines_with_accounts(order):
    """Return order lines with their departments for account resolution."""
    lines = list(order.items.select_related("item").all())
    result = []
    for line in lines:
        department = line.department or line.item.department
        result.append(
            {
                "line": line,
                "department": department,
            }
        )
    return result


def _income_legs(rows):
    """Build income GL rows keyed by resolved income account, merging per account."""
    settings = Restaurant.load()
    default_income = settings.default_income_account if settings else None
    per_account = {}
    for row in rows:
        account = _income_account_for(row["department"]) or default_income
        account = _resolve_required_account(account, label="The default income account")
        amount = row["line"].amount
        per_account[account.pk] = {
            "account": account,
            "credit": per_account.get(account.pk, {}).get("credit", Decimal("0")) + amount,
        }
    return list(per_account.values())


def _sales_returns_legs(order, settings):
    """Build sales-returns GL rows keyed by resolved returns account, merging per account."""
    default_returns = settings.default_sales_returns_account if settings else None
    per_account = {}
    for row in _order_lines_with_accounts(order):
        amount = abs(row["line"].amount)
        if not amount:
            continue
        account = _sales_returns_account_for(row["department"]) or default_returns
        account = _resolve_required_account(account, label="The sales returns account")
        per_account[account.pk] = {
            "account": account,
            "debit": per_account.get(account.pk, {}).get("debit", Decimal("0")) + amount,
        }
    return list(per_account.values())


def _payment_legs(order, settings):
    """Build payment GL rows (debits), reducing change once on the change account."""
    change_left = order.change_amount or Decimal("0")
    change_account = None
    if change_left:
        change_account = _resolve_required_account(
            settings.account_for_change_amount if settings else None,
            label="The change account",
        )
    rows = []
    for payment in order.payments.select_related("mode_of_payment").all():
        account = _resolve_payment_account(payment.mode_of_payment)
        amount = payment.amount
        if change_left and account.pk == change_account.pk:
            reduction = min(amount, change_left)
            amount -= reduction
            change_left -= reduction
        if amount != 0:
            rows.append({"account": account, "debit": amount})
    if change_left:
        raise ValidationError("Change could not be applied to a matching payment account.")
    return rows


def _rounding_leg(order, settings):
    """Round-off row: credit positive adjustment, debit negative."""
    amount = order.rounding_adjustment
    if not amount:
        return []
    account = _resolve_required_account(settings.round_off_account, label="The round-off account")
    if amount > 0:
        return [{"account": account, "credit": amount}]
    return [{"account": account, "debit": -amount}]


def _cogs_legs(order, rows, settings):
    """COGS from settle-time drink deductions — credited to the warehouse account."""
    from apps.inventory.models import StockLedgerEntry

    default_expense = settings.default_expense_account if settings else None
    unit_expense = _expense_account_for("DRINKS")
    sle_rows = StockLedgerEntry.objects.filter(
        voucher_type="POS Order",
        voucher_no=str(order.pk),
        quantity__lt=0,
    ).select_related("item")
    per_account = {}
    for sle in sle_rows:
        line = next((r for r in rows if r["line"].item_id == sle.item_id), None)
        if line is None:
            continue
        account = unit_expense or default_expense
        if account is None:
            raise ValidationError("The default expense account is not configured.")
        account = _resolve_required_account(account, label="The default expense account")
        value = money(abs(sle.stock_value_change))
        per_account[account.pk] = {
            "account": account,
            "debit": per_account.get(account.pk, {}).get("debit", Decimal("0")) + value,
        }
    if not per_account:
        return []
    total_value = money(sum((row["debit"] for row in per_account.values()), Decimal("0")))
    warehouse_account = _resolve_required_account(
        order.stock_warehouse.account if order.stock_warehouse_id else None,
        label="The warehouse account",
    )
    return [{"account": row["account"], "debit": row["debit"]} for row in per_account.values()] + [
        {"account": warehouse_account, "credit": total_value}
    ]


def _merge_rows(rows):
    """Merge GL rows sharing account/against."""
    merged = {}
    for row in rows:
        key = (row.get("account").pk, row.get("against", ""))
        if key in merged:
            merged[key]["debit"] = merged[key].get("debit", Decimal("0")) + row.get("debit", Decimal("0"))
            merged[key]["credit"] = merged[key].get("credit", Decimal("0")) + row.get("credit", Decimal("0"))
        else:
            merged[key] = {
                "account": row.get("account"),
                "against": row.get("against", ""),
                "debit": row.get("debit", Decimal("0")),
                "credit": row.get("credit", Decimal("0")),
            }
    result = []
    for row in merged.values():
        net = (row["debit"] or Decimal("0")) - (row["credit"] or Decimal("0"))
        if net > 0:
            result.append({**row, "debit": net, "credit": Decimal("0")})
        elif net < 0:
            result.append({**row, "debit": Decimal("0"), "credit": -net})
    return result


def _ensure_disjoint_sides(rows):
    """Reject rows where one account sits on both the debit and credit side."""
    debits = {}
    credits = {}
    for row in rows:
        if row.get("debit"):
            debits[row["account"].pk] = row["account"].name
        if row.get("credit"):
            credits[row["account"].pk] = row["account"].name
    clash = sorted(debits[pk] for pk in debits.keys() & credits.keys())
    if clash:
        raise ValidationError(
            f"Account {'/'.join(clash)} appears on both sides of the posting — "
            "check that no payment mode is mapped to a sales account."
        )


@transaction.atomic
def post_order_gl(order):
    """Post GL entries for a settled order; fails closed when the account chain is unconfigured."""
    if order.status != SUBMITTED:
        raise ValidationError("Only submitted orders can be posted to the GL.")
    if order.is_return:
        return
    if GLEntry.objects.filter(voucher_type="Order", voucher_no=order.invoice_number, is_cancelled=False).exists():
        return  # already posted — idempotent
    settings = Restaurant.load()
    if settings is None:
        raise ValidationError("Restaurant settings are not configured.")
    rows = _order_lines_with_accounts(order)

    legs = []
    legs.extend(_income_legs(rows))
    legs.extend(_payment_legs(order, settings))
    legs.extend(_rounding_leg(order, settings))
    legs.extend(_cogs_legs(order, rows, settings))
    _ensure_disjoint_sides(legs)

    against = ", ".join(row["account"].name for row in legs if row.get("credit"))
    for row in legs:
        row["against"] = against
    merged = _merge_rows(legs)

    if not merged:
        return
    GLEntry.post(
        posting_date=order.posting_date,
        rows=merged,
        voucher_type="Order",
        voucher_no=order.invoice_number,
        remarks=f"Order {order.invoice_number}",
    )


def _is_drink_line(line):
    return (line.department or getattr(line.item, "department", None)) == "DRINKS"


def _sales_returns_account_for(department):
    """Return the ProductionUnit sales-returns account for a department, or None."""
    from apps.settings.models import ProductionUnit

    unit = ProductionUnit.objects.filter(department=department).select_related("sales_returns_account").first()
    if unit is not None and unit.sales_returns_account_id:
        return unit.sales_returns_account
    return None


def _restore_value_for(return_order, line):
    """Booked value of a return line's restore SLE — zero when the line was not restocked."""
    from apps.inventory.models import StockLedgerEntry

    sle = StockLedgerEntry.objects.filter(
        voucher_type="POS Return",
        voucher_no=str(return_order.pk),
        voucher_detail_no=str(line.pk),
        quantity__gt=0,
    ).first()
    return abs(sle.stock_value_change) if sle is not None else Decimal("0")


def _plug_round_off(rows, settings):
    """Put any debit/credit remainder on the round-off account so the batch balances."""
    debit = sum((row.get("debit") or Decimal("0") for row in rows), Decimal("0"))
    credit = sum((row.get("credit") or Decimal("0") for row in rows), Decimal("0"))
    diff = money(debit - credit)
    if not diff:
        return rows
    account = _resolve_required_account(
        settings.round_off_account if settings else None,
        label="The round-off account",
    )
    if diff > 0:
        rows.append({"account": account, "credit": diff})
    else:
        rows.append({"account": account, "debit": -diff})
    return rows


@transaction.atomic
def post_refund_gl(return_order):
    """Post refund GL rebuilt from the returned lines, payments, and wastage."""
    if return_order.status != SUBMITTED or not return_order.is_return:
        raise ValidationError("Only submitted return orders can be posted to the GL.")
    source = return_order.return_against
    if source is None:
        raise ValidationError("The return order has no source order.")
    if GLEntry.objects.filter(
        voucher_type="Order", voucher_no=return_order.invoice_number, is_cancelled=False
    ).exists():
        return  # idempotent

    if not GLEntry.objects.filter(voucher_type="Order", voucher_no=source.invoice_number, is_cancelled=False).exists():
        return

    settings = Restaurant.load()
    if settings is None:
        raise ValidationError("Restaurant settings are not configured.")
    lines = list(return_order.items.select_related("item__item_group", "item").all())
    if not lines:
        raise ValidationError("The return has no refundable value.")

    from apps.orders.services import settle_time_rate

    rows = _sales_returns_legs(return_order, settings)

    for payment in return_order.payments.select_related("mode_of_payment").all():
        amount = abs(payment.amount)
        if amount:
            rows.append(
                {
                    "account": _resolve_payment_account(payment.mode_of_payment),
                    "credit": amount,
                }
            )

    default_expense = settings.default_expense_account if settings else None
    unit_expense = _expense_account_for("DRINKS")
    warehouse_account = None
    wastage_account = None
    drink_returns = [line for line in lines if _is_drink_line(line)]
    if drink_returns:
        warehouse_account = _resolve_required_account(
            return_order.stock_warehouse.account if return_order.stock_warehouse_id else None,
            label="The warehouse account",
        )
    if any(line.not_restockable for line in drink_returns):
        wastage_account = _resolve_required_account(
            settings.wastage_account if settings else None,
            label="The wastage account",
        )

    for line in drink_returns:
        if line.not_restockable:
            value = money(abs(line.qty) * settle_time_rate(source, line.item))
        else:
            # The GL mirrors the restore SLE's booked value so stock and COGS reverse exactly.
            value = money(_restore_value_for(return_order, line))
        if not value:
            continue
        expense = _resolve_required_account(unit_expense or default_expense, label="The default expense account")
        rows.append({"account": expense, "credit": value})
        if line.not_restockable:
            if wastage_account.pk == expense.pk:
                raise ValidationError("Configure a wastage account that is separate from the drink expense account.")
            rows.append({"account": wastage_account, "debit": value})
        else:
            rows.append({"account": warehouse_account, "debit": value})

    _ensure_disjoint_sides(rows)
    rows = _plug_round_off(_merge_rows(rows), settings)
    if not rows:
        return
    against = ", ".join(row["account"].name for row in rows if row.get("credit"))
    for row in rows:
        row.setdefault("against", against)
    GLEntry.post(
        posting_date=return_order.posting_date,
        rows=_merge_rows(rows),
        voucher_type="Order",
        voucher_no=return_order.invoice_number,
        remarks=f"Refund of {source.invoice_number}",
    )


@transaction.atomic
def post_cash_variance_gl(closing):
    """Post and submit the variance JournalEntry for a closing entry's per-mode differences."""
    from apps.staff.models import POSClosingEntry

    if closing.status != POSClosingEntry.SUBMITTED:
        return None
    settings = Restaurant.load()
    if settings is None:
        raise ValidationError("Restaurant settings are not configured.")
    mode_legs = []
    total_short = Decimal("0")
    total_over = Decimal("0")
    for cp in closing.closing_payments.select_related("mode_of_payment").all():
        difference = cp.difference
        if not difference:
            continue
        mode_account = _resolve_payment_account(cp.mode_of_payment)
        if difference > 0:
            mode_legs.append({"account": mode_account, "debit": difference, "remarks": "Cash excess"})
            total_over += difference
        else:
            mode_legs.append({"account": mode_account, "credit": -difference, "remarks": "Cash shortage"})
            total_short += -difference
    if not mode_legs:
        return None
    if total_short:
        shortage_account = _resolve_required_account(settings.cash_shortage_account, label="The cash shortage account")
    if total_over:
        over_account = _resolve_required_account(settings.cash_over_short_account, label="The cash over-short account")

    journal = JournalEntry.objects.create(
        voucher_type=JournalEntry.JOURNAL,
        posting_date=closing.posting_date,
        remark=f"Cash variance for closing entry #{closing.pk}",
    )
    if total_short:
        JournalEntryAccount.objects.create(
            journal_entry=journal,
            account=shortage_account,
            debit=total_short,
            remarks="Cash shortage",
        )
    for leg in mode_legs:
        JournalEntryAccount.objects.create(journal_entry=journal, **leg)
    if total_over:
        JournalEntryAccount.objects.create(
            journal_entry=journal,
            account=over_account,
            credit=total_over,
            remarks="Cash excess",
        )
    journal.submit()
    return journal


@transaction.atomic
def post_shift_cash_out_gl(cash_out):
    """Post a shift cash-out: Dr petty-cash/default expense, Cr cash-mode account. Idempotent."""
    from apps.staff.models import ShiftCashOut

    if cash_out.status != ShiftCashOut.SUBMITTED or not cash_out.amount:
        return None
    if GLEntry.objects.filter(voucher_type="Shift Cash-Out", voucher_no=str(cash_out.pk), is_cancelled=False).exists():
        return None
    settings = Restaurant.load()
    expense = None
    if settings is not None:
        expense = settings.petty_cash_expense_account or settings.default_expense_account
    expense = _resolve_required_account(expense, label="The petty cash expense account")
    cash_account = _resolve_payment_account(cash_out.mode_of_payment)
    GLEntry.post(
        posting_date=timezone.localdate(),
        rows=[
            {"account": expense, "debit": cash_out.amount, "against": cash_account.name},
            {"account": cash_account, "credit": cash_out.amount, "against": expense.name},
        ],
        voucher_type="Shift Cash-Out",
        voucher_no=str(cash_out.pk),
        remarks=f"Shift cash-out #{cash_out.pk} ({cash_out.get_reason_display()})",
    )


def _payable_account_for(supplier, settings, label="The default payable account"):
    """Resolve the payable account: per-supplier override → Restaurant default."""
    account = supplier.payable_account if supplier.payable_account_id else None
    if account is None:
        account = settings.default_payable_account if settings else None
    return _resolve_required_account(account, label=label)


def _reverse_gl(voucher_type, voucher_no, remarks="Reversal"):
    """Mark a voucher's GL rows cancelled and post mirrored negated rows dated today."""
    originals = list(GLEntry.objects.filter(voucher_type=voucher_type, voucher_no=voucher_no, is_cancelled=False))
    if not originals:
        return
    for gl in originals:
        gl.is_cancelled = True
        gl.save(update_fields=["is_cancelled", "updated_at"])
    GLEntry.post(
        posting_date=timezone.localdate(),
        rows=[
            {
                "account": gl.account,
                "debit": gl.credit,
                "credit": gl.debit,
                "against": gl.against,
            }
            for gl in originals
        ],
        voucher_type=voucher_type,
        voucher_no=voucher_no,
        remarks=remarks,
    )


@transaction.atomic
def build_supplier_invoice_stock_lines(invoice):
    """Create SupplierInvoiceItem rows from the linked receipt's unlinked lines."""
    if not invoice.purchase_receipt_id:
        return
    for receipt_line in invoice.purchase_receipt.items.all():
        already_linked = SupplierInvoiceItem.objects.filter(
            invoice=invoice, source_receipt_line_id=receipt_line.pk
        ).exists()
        if already_linked:
            continue
        SupplierInvoiceItem.objects.create(
            invoice=invoice,
            item=receipt_line.item,
            source_receipt_line=receipt_line,
            qty=receipt_line.received_qty,
            rate=receipt_line.rate,
        )


@transaction.atomic
def post_supplier_invoice_gl(invoice):
    """Post the supplier invoice: Dr GRNI/expense, Cr payable. Idempotent."""
    if GLEntry.objects.filter(
        voucher_type="Supplier Invoice", voucher_no=invoice.invoice_number, is_cancelled=False
    ).exists():
        return
    settings = Restaurant.load()
    if settings is None:
        raise ValidationError("Restaurant settings are not configured.")

    stock_total = Decimal("0")
    expense_total = Decimal("0")
    stock_rows = []
    expense_rows = []
    for line in invoice.items.select_related("item", "source_receipt_line").all():
        if not invoice.purchase_receipt_id:
            raise ValidationError("Supplier invoices with stock lines must link to a purchase receipt.")
        if line.source_receipt_line_id and (
            line.qty != line.source_receipt_line.received_qty or line.rate != line.source_receipt_line.rate
        ):
            raise ValidationError("Stock line rate/quantity must match the receipt line.")
        account = settings.stock_received_but_not_billed_account if settings else None
        account = _resolve_required_account(account, label="The stock received but not billed account")
        stock_total += line.amount
        stock_rows.append({"account": account, "debit": line.amount})
    for expense in invoice.expenses.all():
        account = _resolve_required_account(
            settings.default_supplier_expense_account if settings else None,
            label="The default supplier expense account",
        )
        expense_total += expense.amount
        expense_rows.append({"account": account, "debit": expense.amount})
    if not stock_rows and not expense_rows:
        raise ValidationError("Add at least one line before submitting.")

    payable = _payable_account_for(invoice.supplier, settings)
    legs = stock_rows + expense_rows + [{"account": payable, "credit": stock_total + expense_total}]
    against = payable.name
    for row in legs:
        row["against"] = against
    rows = _merge_rows(legs)

    GLEntry.post(
        posting_date=invoice.posting_date,
        rows=rows,
        voucher_type="Supplier Invoice",
        voucher_no=invoice.invoice_number,
        remarks=f"Supplier invoice {invoice.invoice_number}",
    )


@transaction.atomic
def cancel_supplier_invoice_gl(invoice):
    """Reverse the invoice's GL rows (mirrored negated rows, originals cancelled)."""
    _reverse_gl("Supplier Invoice", invoice.invoice_number)


@transaction.atomic
def post_supplier_payment_gl(payment):
    """Post the supplier payment: Dr payable, Cr cash/bank. Idempotent."""
    if GLEntry.objects.filter(
        voucher_type="Supplier Payment", voucher_no=payment.payment_number, is_cancelled=False
    ).exists():
        return
    settings = Restaurant.load()
    if settings is None:
        raise ValidationError("Restaurant settings are not configured.")
    payable = _payable_account_for(payment.supplier, settings)
    cash_account = _resolve_payment_account(payment.mode_of_payment)

    rows = [
        {"account": payable, "debit": payment.paid_amount, "against": cash_account.name},
        {
            "account": cash_account,
            "credit": payment.paid_amount,
            "against": payable.name,
        },
    ]
    GLEntry.post(
        posting_date=payment.posting_date,
        rows=rows,
        voucher_type="Supplier Payment",
        voucher_no=payment.payment_number,
        remarks=f"Payment to {payment.supplier.supplier_name}",
    )


@transaction.atomic
def cancel_supplier_payment_gl(payment):
    """Reverse the payment's GL rows (mirrored negated rows, originals cancelled)."""
    _reverse_gl("Supplier Payment", payment.payment_number)
