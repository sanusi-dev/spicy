# Execution Flow: Cart Mutations

## Quantity, Remove, and Increment

```text
Cart +/-/remove button
  -> templates/pos/partials/cart/items.html
  -> POST pos:pos_order_update_item
  -> views_pos.pos_order_update_item()
  -> orders.services.update_order_item()
  -> lock Order and _ensure_editable()
  -> update_order_line_quantity() or remove_order_line()
  -> validate line and synchronize DRINKS reservation
  -> delete or save OrderItem
  -> recalculate totals and audit event
  -> replace #cart-panel
```

Actions are `increment`, `decrement`, `remove`, and default `update`. A quantity <= 0 deletes the line. Increment/decrement of a missing line raises. A set/update for a missing line is effectively a no-op.

## Guest Count and Order Type

Guest buttons and order-type buttons POST `pos_order_update_meta`. The view locks the order and calls `update_order_meta()`. `Order.change_guest_count()` allows 1..50 and refuses a decrease below the highest customer index with items. Active card state is reset to 1 if the effective count makes the old card invalid. The customer card activation endpoint changes session state only.

## Clear

The Alpine/SweetAlert clear control triggers a hidden HTMX button. `pos_order_clear()` locks the order, rejects printed/sent orders, calls `clear_order_lines()`, releases all DRINKS reservations, deletes lines, recalculates totals, and records `ITEMS_CLEARED`.

## Server Authority

Every mutation view loads its draft through `Order.objects.open_drafts_for(shift, request.user)`, so a non-owner cashier gets a 404 while Manager/Admin can act on any draft. Templates hide controls after KOT/receipt state, but all service/model paths repeat `_ensure_editable()` and current line validation. A stale or hand-built request cannot bypass the lock through the normal service API.
