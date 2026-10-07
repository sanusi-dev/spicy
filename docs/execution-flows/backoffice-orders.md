# Execution Flow: Backoffice Order Operations

## Register and Detail

```text
Backoffice order navigation
  -> apps/orders/urls.py
  -> orders.views.order_list()
  -> search/filter Order queryset with annotations
     (search, status, order type, posting-date from/to; same filters for ?export=csv)
  -> Paginator 50
  -> templates/backoffice/orders/order_list.html
```

Detail uses `select_related` and `prefetch_related` for cashier, opening entry, items/item groups, payments/modes, and KOTs. The detail template exposes cancellation and return controls depending on state and role.

## Manager Cancellation

```text
Cancel form
  -> POST orders:order_cancel
  -> orders.views.order_cancel()
  -> require manager/admin/superuser
  -> POSOrderCancelForm validation
  -> orders.services.cancel_sent_order()
  -> order lock, reservation release, cancellation KOTs
  -> commit
  -> services.dispatch_tickets()
  -> redirect order detail with pending-print warnings
```

Only sent draft orders are cancellable — cancel releases drink reservations and creates cancellation KOTs. Submitted orders are never cancelled. They leave the lifecycle only through the return flow (`make_return()` → `submit_return()`). An unsent draft is never cancelled either. The backoffice deletes it instead (`orders.views.order_delete`, manager-only), which abandons it as a `DISCARDED` tombstone with an `ORDER_DELETED` audit event rather than purging it.

## Return Creation and Submission

`orders.views.order_return()` checks manager/admin/superuser and calls `make_return()`. The service locks the paid submitted source and rejects an existing active return. It clones each line with negative quantity and `return_against_item`. It assigns an order number. It recalculates a negative total and creates `RETURN_CREATED`.

The resulting return is a draft. `settle_order()` rejects returns. Managers can reduce qty, drop lines, or mark drink lines as wastage on the draft (`order_return_line_update`). `orders.views.order_return_submit()` (manager-only) calls `submit_return()`. That service re-validates each line. It restores drink stock with `POS Return` SLEs (skipping wastage lines). It writes proportional negative refund payment rows. It marks the return `SUBMITTED` with the `RETURN_SUBMITTED` audit event. The detail page shows "Submit return" and "Delete draft" for a return draft.

## KOT Register

`kot_list()` filters KOT type, ticket type, lifecycle status, print status, and order/KOT number. `kot_detail()` loads the source order, production unit, creator, and item snapshots. Both views are gated by `@backoffice_required` in `apps/users/decorators.py`.
