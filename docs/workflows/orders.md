# Order Workflow

## Order as the Operational Document

`Order` in `apps/orders/models.py` is the source of truth for order lines, payment rows, totals, lifecycle, shift ownership, receipt-printed state, stock warehouse snapshot, returns, and audit events. It is not an accounting invoice model and does not have a separate receipt or customer record.

## Creation and Editing

`create_draft_order()` checks Restaurant settings, locks the active shift, enforces `max_open_drafts`, creates a draft stamped with its creator (`created_by`), assigns a human order number, and records `CREATED`. Draft access is owner-scoped: a cashier can only open, edit, send, settle, cancel, or delete their own drafts; Manager/Admin can access any draft. `Order.save()` assigns `arrived_time` and an invoice number such as `REST-42` on insert.

Line addition uses menu pricing and snapshots item data. Identical item/customer/comment lines merge. Add-ons become separate order lines. `recalculate_totals()` sums line amounts, sets net and grand totals, and calculates whole-unit half-up rounding.

Order edits are rejected when the order is submitted, cancelled, discarded, or has KOTs. A printed receipt is no longer a draft lock — only a kitchen/bar ticket (KOT) freezes draft edits. Guest count can rise to 50 but cannot drop below the highest customer index containing items.

## Customer Grouping

Grouping is presentation-only. `guest_count` lives on the order; `customer_index` lives on each line and is copied into KOT lines. The session's active card selects where the next line is added. No lines are silently re-tagged when guest count changes.

## KOT/BOT Snapshot

`create_tickets()` takes a single-send snapshot of current lines, routes FOOD and DRINKS to their configured production units, and creates KOT/BOT rows. A takeaway station can suppress its ticket with `block_takeaway_kot`. The order remains a draft after sending, but its lines become locked.

## Submission

`settle_order()` is the only normal path from draft to submitted. It locks the order and shift, revalidates current lines and stock, creates payment rows, applies rounded total/payment/change values, re-stamps `posting_date`/`posting_time` to the settlement moment (a draft's creation stamps are provisional), converts drink reservations into stock issues, marks the order paid/submitted, sets `invoice_printed`/`invoice_printed_at`/`invoice_printed_by` (settlement *is* the receipt event), and adds `SUBMITTED` audit data. The view calls `printing.print_receipt(order)` after settlement; a printer failure shows a warning but never rolls back the sale. Receipts are reprinted from order history (`pos_order_history_print`, scoped to the caller's history visibility rules).

## Stage Exits — Delete, Cancel, Return

Each order stage has exactly one exit (FEATURES.md A6):

1. **Draft, nothing sent** (no KOT) — **delete** (abandon with a tombstone). `delete_unsent_draft()` releases drink reservations, keeps the order row and its item lines, marks the order `DISCARDED` with `discarded_by`/`discarded_at`, and appends an `ORDER_DELETED` audit event with the actor and an item snapshot in its metadata. `Order.delete()` refuses hard deletion, so no code path can purge audit events. The POS "Delete order" button posts to `pos_order_delete`; the backoffice "Delete draft" button posts to `order_delete` (manager-only). There is no cancellation path for unsent drafts — `cancel_sent_order()` rejects them with "delete it instead".
2. **Sent to kitchen/bar** (KOT exists) — **cancel only**, never delete. `cancel_sent_order()` (POS and backoffice — the backoffice cancel view calls the same service) requires a reason, creates cancellation KOTs for each station, releases drink reservations, lands on the per-cashier cancel report, and preserves items/payments for audit.
3. **Paid (submitted)** — **return only**, never cancel. `make_return()` creates a negative-item return draft linked to the source, mirroring only the *remaining returnable* quantity per line (already-submitted returns reduce it, so partial returns are supported). The return draft can be edited in backoffice: managers reduce qty, drop lines, or mark drink lines not restockable (wastage) before submit. `submit_return()` submits it: re-validates each return line, restores drink stock via positive SLEs (`voucher_type="POS Return"`) at the source sale's settle-time WAC and skipping `not_restockable` lines, writes negative `OrderPayment` rows proportional to the source net tenders (`reference_no=""` so the payment-reference unique constraint cannot collide), sets `paid_amount` to the negative refund total (immutable `SUBMITTED` status, `is_paid` stays `False`), posts refund GL via `accounting.services.post_refund_gl` rebuilt from the returned lines (dated on the return's `posting_date`, re-stamped to the submission moment: refunds debit the per-department Sales Returns account — `ProductionUnit.sales_returns_account` → `Restaurant.default_sales_returns_account`, failing closed when neither is set — while restocked drinks debit the warehouse and credit COGS at the source sale's settle-time WAC with no variance leg; non-restockable drink lines credit COGS and debit the wastage account at the same rate, with no warehouse movement; the wastage account must differ from the drink expense account), and appends `RETURN_SUBMITTED` audit data. The backoffice "Submit return" button is manager-only. One active return *draft* per order at a time; once a return is SUBMITTED, a new draft may be created for a further partial refund.

`discard_order()` remains the legacy empty-draft path used by seed data. Both abandon paths land on `DISCARDED`; deleted drafts appear in the manager history "discarded" tab with their items and audit trail.

## Audit Events

The service layer appends events such as `CREATED`, `ITEM_ADDED`, `ITEM_REMOVED`, `ITEM_QUANTITY_CHANGED`, `ITEMS_CLEARED`, `ORDER_TYPE_CHANGED`, `GUEST_COUNT_CHANGED`, `KOTS_CREATED`, `SUBMITTED`, `CANCELLED`, `DISCARDED`, `ORDER_DELETED`, `RETURN_CREATED`, and `RETURN_SUBMITTED`. `OrderAuditEvent` cannot be updated or deleted, and nothing bypasses that guard — orders are never hard-deleted.

## Important Invariants

- Normal order line quantity is positive; return line quantity is negative and references an original line.
- Rates are non-negative Decimal values and amounts are quantized to two decimal places.
- Historical item name/department/stock flags are snapshots, so later Item changes do not rewrite old lines.
- Human order numbers are assigned through a locked `OrderSequence` counter.
- Direct `QuerySet.update()` can bypass `save()` guards; service and model paths are the intended behavior.
