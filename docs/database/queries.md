# Important Queries

## POS Read Queries

- `views_pos._get_open_shift()` filters submitted openings with no closing entry and optionally locks them.
- `services.open_draft_orders()` uses `prefetch_related("items")`, `Exists(KOT...)`, `only()`, optional item/order-number search, and attaches preview metadata in Python.
- `_build_order_context()` uses `select_related("item", "item__item_group")` and prefetches add-on relationships for the active menu.
- `services.order_history_rows()` uses `select_related("cashier")`, `Count("items", distinct=True)`, status/payment/order-type/date filters, and `distinct()` for payment joins.
- `pos_order_history_detail()` prefetches items, payment modes, and production units for the detail surface.

## Order Aggregations

`Order.recalculate_totals()` aggregates `Sum("amount")` over order lines. `staff.services.submit_closing_entry()` uses a correlated `Subquery` to sum order quantities and aggregates net/grand totals over `submitted_in_shift()` rows. Payment totals use `Sum("amount")` grouped by mode.

## Inventory Queries

- Item details: `Exists(MenuItem...)` annotates whether variants are on a menu.
- Ledger pages filter item, warehouse, and posting date with `select_related`.
- FIFO reads the latest non-cancelled SLE ordered by posting time and primary key.
- Reservation/settlement locks Bin rows for the relevant item/warehouse set.

## Backoffice Queries

- Order list annotates item/ticket/pending-ticket counts and searches invoice, customer, or numeric order number.
- KOT list filters KOT fields and joins order invoice/order number.
- Staff list prefetches only Spicy groups, paginates 20 users, and derives roles from the cached group set.
- Menu list annotates item count; inventory item list supports flags, variants, active status, and name/code search.

## Query reports

Sales reports aggregate `Order` (`status=SUBMITTED`) by `posting_date` and join `OrderItem` for FOOD/DRINKS amounts so order-level rounding is not multiplied by line count. Item-wise, employee-wise, service-wise, and time-wise reports use `values()` + `Sum`/`Count` with `ExtractHour` / `ExtractMonth`. Cancelled invoices read `Order.status=CANCELLED` and `is_return=False`. POS register filters `POSClosingEntry.status=SUBMITTED` and prefetches `closing_payments`. GL/trial balance/simple P&L query `GLEntry` (cancelled originals included so reversals net to zero); trial balance groups leaf accounts with `Sum(debit)` / `Sum(credit)` and drops zero balances.

## Query Tracing

When a displayed value is wrong, begin with the view queryset and annotations, then check the template relation access. `select_related` and `prefetch_related` reduce N+1 queries but do not alter business values. For totals, follow the service aggregate rather than trusting a template calculation.
