# Transactions and Database Behavior

## Atomic Service Boundaries

These order functions are atomic: draft creation, metadata update, cart item update, settlement, cancellation, return creation, return submission, unsent-draft delete (tombstone), and line add/update/remove/clear. Ticket creation is also atomic. Inventory document submit/cancel services, staff open/close services, and setup seed commands are also atomic.

## Locking Map

| Resource | Locking purpose | Main code |
|---|---|---|
| Restaurant | one global mutex for open shift and draft-cap/config changes | `staff.services.open_shift`, `Order.create_draft_order`, `POSOpeningEntry.submit` |
| POSOpeningEntry | active shift ownership, settlement, close, draft creation | order/staff views/services |
| Order | serialize cart, settlement, KOT, cancellation, return submission, deletion | `orders.services` |
| OrderSequence | unique sequential human order numbers | `Order.assign_order_number` |
| Bin | reservations, WAC, actual stock and valuation | order/inventory services |
| KOT | independent print status dispatch/retry | `dispatch_tickets`, POS ticket view |
| Closing entry/opening entry | one close and authoritative shift cutoff | `submit_closing_entry` |
| SLE and Bin | deterministic voucher reversal and ledger tail updates | inventory `_reverse_voucher` |

## Rollback Boundaries

- Settlement rolls back payment rows, order status, stock deduction, and reservation conversion together.
- Add-on parent/add-on lines are one operation.
- Inventory document posting rolls back all SLE and Bin changes if a later line fails.
- Settlement commits before `print_receipt()`. A failed print does not roll back the settled, printed order.
- Return submission rolls back stock restoration and refund rows together if any line fails.
- KOT creation commits before printing. One ticket print transaction is independent of another.
- POS close GET is read-only. POS close POST can commit a draft closing entry before invalid form rendering.

## Bulk Operations

`OpeningPayment`, `ClosingPayment`, and KOT items use `bulk_create`. Inventory services use `bulk_update` for item last-purchase-rate changes. Bulk methods bypass model `save()` and `clean()`. Callers provide the needed validation before bulk writes.

## Concurrency Protections

Tests cover order sequence concurrency, drink reservation concurrency, one-open-shift enforcement, and close double-click protection. Locks are usually acquired in stable item/warehouse order for inventory bins.

## Concurrency Gaps

- `update_order_meta()` assumes the POS view already locked the order. Direct service callers do not get that lock.
- `ensure_closing_draft()` assumes its caller holds the opening lock.
- Shift cancel methods do not explicitly lock before related-state checks.
- `_cancel_kots()` relies on the order lock rather than independently locking source KOT rows.
- First creation of an absent `OrderSequence` is less explicit than later increments, although the normal test pre-creates it.

## Bypass Points

Direct `QuerySet.update()` can still bypass model `save()` guards. `StockLedgerEntry.objects.create` and unflagged `save()` raise; posting goes through `create_entry()` / `_create_entry_locked`. Inventory document status changes require `_allow_submit` or `_allow_cancel`. `editable=False` affects forms/admin presentation only. When tracing corrupted state, search for management commands, admin actions, data migrations, and queryset updates in addition to service calls. The `Bin`/`StockLedgerEntry` admin pages are view-only.
