# Execution Flow: Order History and Details

## History Query

```text
History navigation
  -> templates/pos/order_history.html
  -> GET pos:pos_order_history
  -> views_pos.pos_order_history()
  -> parse date/status/payment/type/search
  -> enforce full-history permission
  -> orders.services.order_history_rows()
  -> Paginator 50 rows
  -> render order_history.html#surface
  -> swap #pos-main and push URL
```

The default is today's submitted, paid, non-return sales. Search checks invoice number and numeric order number. Payment filters are cash or electronic (`BANK`/`PHONE`). Full history includes submitted returns, cancelled non-returns, and discarded non-returns. Cashiers without access are forced back to sales.

## Detail Drawer

```text
View icon
  -> hx-get pos:pos_order_history_detail
  -> views_pos.pos_order_history_detail()
  -> select_related/prefetch order, items, payments, KOTs
  -> render order_history_detail.html#drawer
  -> swap #order-details-drawer
  -> Alpine orderDetailsDrawer opens, traps focus, restores focus on close
```

The endpoint applies the same visibility rules as the history list. Without full history it accepts only submitted, paid, non-return orders (any other primary key returns 404). Managers and users with `Restaurant.pos_allow_full_history` can also open cancelled and discarded orders. It shows items, payments, totals, customer grouping, and ticket status.

## Reprints and Ticket Retry

Submitted order receipt reprint POSTs `pos_order_history_print`. Reprints follow the history-list visibility rules. Without full history, only submitted, paid, non-return orders can be reprinted. Managers and `Restaurant.pos_allow_full_history` users can reprint any submitted receipt. Pending cancellation tickets and pending NEW_ORDER tickets on submitted orders use `pos_order_ticket_print` retry, with retry forms rendered in the detail header for each pending ticket. Ticket retry still requires an active shift, so a closed-shift history user can be redirected to the POS gate.
