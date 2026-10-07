# Troubleshooting

## General Reverse Trace

```text
Observed symptom
  -> identify affected row and current status
  -> find every service/model method that writes that field
  -> find the view/URL that calls the writer
  -> inspect template/HTMX trigger or backoffice form
  -> inspect transaction and lock boundary
  -> inspect signals/model save/delete side effects
  -> compare the relevant test coverage
```

Always distinguish database state from physical side effects. A receipt can be marked printed before the print interface returns. A KOT can be committed before ticket dispatch.

## Symptom Table

| Symptom | Start here | Trace |
|---|---|---|
| POS shows no shift | `orders.views_pos.pos_home`, `staff.POSOpeningEntry` | Restaurant exists -> `status=SUBMITTED` -> `closing_entry IS NULL` -> `@staff_required` gate |
| Shift will not open | `pos_open_shift`, `staff.services.open_shift` | enabled modes -> Restaurant lock -> existing open shift -> opening form decimals |
| Shift will not close | `pos_close_shift`, `submit_closing_entry` | open draft count -> closing rows -> active opening -> expected amounts |
| Item missing from catalog | `_build_order_context`, active Menu | Restaurant.active_menu -> menu enabled -> MenuItem disabled -> filters -> Item flags |
| Item visible but cannot add | `pos_order_add_item`, `Item._validate_pos_item` | item disabled/sales flag -> KOT lock -> active card -> stock reservation |
| Drink says unavailable | `drink_stock_available`, `reserve_drink_stock` | default warehouse -> Bin actual/reserved -> disabled warehouse -> reservation race |
| Food sale changed stock | order audit and SLE voucher | search SLE voucher type `POS Order`. Current code should only create it for DRINKS |
| Quantity is wrong | `pos_order_update_item`, `update_order_item` | action/qty -> line lookup -> merge/delete -> reservation -> recalculate totals |
| Incorrect total | `OrderItem.save`, `Order.recalculate_totals`, settlement | Decimal amount -> sum -> rounded_total -> grand_total overwritten at settlement |
| Payment not reflected | `pos_order_settle`, `settle_order` | POST field names -> mode enabled/opening declaration -> GL mapping -> payment row -> status |
| Payment mode visible but rejected | `_get_settle_payment_modes` vs `_validate_payment_data` | global mapped set may differ from shift opening modes |
| Order missing from history | `order_history_rows` | status/is_paid/is_return -> posting_date -> full-history permission -> filters/pagination |
| Receipt printed but no paper | `settle_order`, `apps/orders/printing.py` | Settlement marks printed. Inspect print implementation/result, then reprint from history |
| Ticket remains pending | `dispatch_tickets`, KOT row | ticket status -> print_status -> printer result/exception -> retry action |
| Cancellation did not restore stock | `cancel_sent_order`, `_restore_stock` | draft sent status -> `stock_warehouse` snapshot -> reservation release, cancellation KOTs |
| Shift totals are wrong | `expected_closing_amounts`, `submit_closing_entry` | submitted period rows -> payment sums -> cash change subtraction -> refund subtraction -> closing rows |
| Return cannot complete | `make_return`, `submit_return` | return submission revalidates lines, restores stock, writes proportional refund rows |
| Backoffice route unexpectedly accessible | view decorator | `apps/users/decorators.py` on the view -> role properties in `CustomUser` |
| Role appears stale | `CustomUser` role properties | plain `@property` group checks — no cache. Changes apply next request |
| HTMX response does not update | template target and view fragment | `HX-Target` -> partial name -> swap mode -> target ID |
| Toast missing | response `HX-Trigger` header | MessagesMiddleware -> JSON merge -> `toast.js` `showMessages` listener. On a redirect with queued messages, check for `HX-Redirect` instead (headers on a followed 3xx are dropped) |

## Database Investigation Anchors

Use voucher types and relationships to find side effects:

- POS drink issue: `StockLedgerEntry.voucher_type="POS Order"`, `voucher_no=Order.pk`.
- POS cancellation reversal: `"POS Order Cancellation"`.
- POS return reversal: `"POS Return"`.
- Stock document: `"Stock Entry"` or `"Stock Entry Cancellation"`.
- Reconciliation: `"Stock Reconciliation"` or cancellation.
- Purchase receipt: `"Purchase Receipt"` or cancellation.
- Human order number: `Order.order_number` and `OrderSequence.current_value`.
- Shift membership: `Order.opening_entry_id`.
- Receipt-printed state: `invoice_printed`, timestamp, and user (written at settlement).

## Test Starting Points

- POS and HTMX behavior: `apps/orders/tests/test_pos_views.py`.
- Order service/model behavior: `apps/orders/tests/test_services.py` and `test_order.py`.
- KOT routing: `apps/orders/tests/test_kot.py`.
- Regression behavior: `apps/orders/tests/test_review_fixes.py`.
- Inventory ledger: `apps/inventory/tests/test_stock_ledger_entry.py`.
- Inventory document posting: `test_stock_entry.py`, `test_stock_reconciliation.py`, `test_purchase_receipt.py`.
- Shift lifecycle: `apps/staff/tests/test_pos_opening_entry.py`, `test_pos_closing_entry.py`, `test_services.py`.

## Verification Markers

Use `⚠️ Requires verification` when source tracing cannot establish the behavior. Do not infer a physical printer, external payment, report, or refund result from `FEATURES.md` alone.
