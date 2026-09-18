# Execution Flow: Submit Order

In the current POS, "submit" is the successful payment settlement path. Sending an order to the kitchen does not submit it; it creates KOT/BOT snapshots while leaving the order `DRAFT`.

```text
Pay button
  -> templates/pos/partials/cart/totals.html
  -> GET pos:pos_order_settle
  -> templates/pos/partials/payment/dialog.html
  -> POST pos:pos_order_settle
  -> views_pos.pos_order_settle()
  -> extract payment_<mode> fields
  -> orders.services.settle_order()
  -> atomic lock Order and active shift
  -> validate current lines and rounded totals
  -> validate DRINKS bins and payment modes
  -> ticket guarantee: if no KOTs exist, create and dispatch them (see below)
  -> create OrderPayment rows
  -> set paid/change/is_paid/status/submitted_at
  -> re-stamp posting_date / posting_time to the settlement moment
  -> set invoice_printed / invoice_printed_at / invoice_printed_by
  -> convert DRINKS reservations to POS Order SLEs
  -> Order.audit("SUBMITTED")
  -> clear POS order/card session keys
  -> print_receipt(order) — failure only warns; sale stands
  -> success message and redirect to POS home
```

Settlement is the sale event, so it re-stamps `posting_date`/`posting_time` to the submission moment. The draft's creation stamps are provisional; the Daily P&L, GL, and sales reports then bucket a sale on the same day the shift close counted it.

## Validation Order

The service rejects non-draft/return/empty orders, rejects a settlement actor who neither created the draft nor is a Manager/Admin, revalidates current Item/MenuItem availability, confirms the linked shift is active, snapshots/validates the configured stock warehouse, rechecks locked drink bins, then validates payment rows. Existing payment rows are rejected.

## Totals and Payments

Line amounts are summed into `net_total` and `grand_total`; `rounded_total` is whole-unit half-up. Settlement changes `grand_total` to the rounded value. Total payment must cover it. Cash may create change; non-cash overpayment is rejected.

## Paid-Order Ticket Guarantee

A paid (SUBMITTED) order must always have a kitchen/bar ticket record for every production unit its items belong to. When the order has no KOTs at settlement, the service plans tickets with the same departmental routing as the send action (`_plan_tickets`: production-unit lookup, takeaway `block_takeaway_kot` skip), then builds immutable NEW_ORDER KOT/BOT snapshots (`_build_ticket_snapshots`) and audits `KOTS_CREATED`. An empty plan (e.g. takeaway where every unit blocks KOTs) settles silently without tickets; a department without a configured production unit raises the same `ValidationError` as the send button and blocks settlement. `dispatch_tickets` runs inside the settlement, but a print failure never blocks it — the ticket stays `PENDING` and is retried from order history.

## Atomicity

Payment inserts use nested savepoints to convert uniqueness errors to validation errors. Any later stock error rolls back payment rows, order status, totals, reservation conversion, and SLE creation. Audit event creation is inside the same transaction.

## Return Submission

A submitted return is a *separate* path (`orders.services.submit_return`), not a submission of the same document. It re-stamps `posting_date`/`posting_time` to the submission moment, restores drink stock with positive SLEs (`voucher_type="POS Return"`) unless the line is marked not restockable, writes negative `OrderPayment` rows proportional to the source net tenders, sets `paid_amount` to the negative refund total, keeps `is_paid=False`, transitions the return draft to `SUBMITTED`, and audits `RETURN_SUBMITTED`. Return documents stay out of paid-sales revenue queries (`is_paid=True` filters and `OrderQuerySet.submitted_in_shift` exclude them); at shift close their refund rows reduce the expected drawer per mode.

## Important Difference from Older Feature Text

Settlement does not create a separate food/drinks accounting split. The current code only submits one operational Order and its payment rows, and it auto-prints the receipt after settlement (non-blocking).
