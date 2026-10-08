# Receipts and Printing

## Current Implementation

Printing is represented by a narrow interface in `apps/orders/printing.py`:

- `print_ticket(ticket) -> PrintResult`
- `print_receipt(order) -> PrintResult`

Both functions currently return `PrintResult(success=True, ...)` without formatting or communicating with a device. `ProductionUnit` stores `printer_ip`, paper width, and cut mode, but those values are not consumed by the current print functions.

## Receipt Print

There is no pre-payment receipt print. `settle_order()` marks `invoice_printed`, `invoice_printed_at`, and `invoice_printed_by` on the settlement save. The view then calls `printing.print_receipt(order)` after the transaction commits. A failed physical print leaves the order settled and printed, and the cashier is told to reprint from order history. Because the current stub always succeeds, real failure behavior is not exercised outside tests.

## Ticket Dispatch

`create_tickets()` creates KOT/BOT rows with `print_status=PENDING`. `dispatch_tickets()` processes each ticket in its own transaction, locking the row, calling `print_ticket()`, and changing status to `PRINTED` only on success. Failure leaves the ticket pending for retry.

Food routes to the FOOD ProductionUnit, drinks to DRINKS; `_ticket_type_for_department` raises on a NULL or unknown department instead of silently defaulting to the bar station. Cancellation creates a new cancellation ticket per station, while original tickets become cancelled. Cancellation-ticket printing can fail independently after order cancellation has committed.

`settle_order()` guarantees ticket records. When a settling order has no KOTs, it plans tickets with the same departmental routing (`_plan_tickets`). It builds NEW_ORDER snapshots (`_build_ticket_snapshots`) inside the settlement transaction and returns them. `pos_order_settle()` calls `dispatch_tickets()` after the settlement commits, exactly as the Send flow does. Print failure never blocks settlement — the ticket stays `PENDING` for retry, and the cashier sees a warning naming the stations that failed. The seed command dispatches the tickets its settled orders create.

## Retry and Reprint

`pos_order_ticket_print()` accepts `retry` for pending tickets and `reprint` for printed tickets, on draft, cancelled, and submitted orders. Reprint is manager/admin/superuser-only. The selected ticket is locked and selected in a transaction. The physical call occurs afterward. Historical receipt reprint accepts submitted orders and does not update `invoice_printed` metadata.

On a submitted order, the history detail screen shows a "Retry kitchen/bar ticket" action for each pending NEW_ORDER ticket.

## Templates

Receipt/ticket controls live in `templates/pos/partials/cart/totals.html` (send-to-kitchen, ticket retry/reprint, delete/cancel) and `templates/pos/order_history_detail.html` (receipt reprint and ticket retry/reprint). There is no receipt template, ESC/POS formatter, print agent, printer client, or print job table in the current code.

⚠️ Requires verification: the intended production printer topology in `AGENTS.md` and `FEATURES.md` is not implemented by the current runtime. Treat those files as design context. They do not describe an available integration.
