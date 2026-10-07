# Execution Flow: Create Order

```text
New order button
  -> templates/pos/draft_orders.html
  -> POST pos:pos_order_new (/pos/order/new/)
  -> views_pos.pos_order_new()
  -> validate/default order type and clamp guest count 1..50
  -> _get_open_shift()
  -> orders.services.create_draft_order()
  -> lock Restaurant and active shift
  -> enforce Restaurant.max_open_drafts
  -> Order.objects.create() with created_by=user
  -> Order.save(): arrived_time and invoice_number
  -> Order.assign_order_number(): lock OrderSequence
  -> Order.audit("CREATED")
  -> session pos_order_id and pos_active_cards updated
  -> HX-Push-Url order screen or HTTP redirect
```

The created row starts `DRAFT`, belongs to the active `POSOpeningEntry`, is stamped with its creator (`created_by`), and has one guest by default. Direct `Order.objects.create()` does not itself validate an active shift. The production POS path relies on `create_draft_order()`.

## Concurrency

Restaurant and shift locks serialize the open-draft-cap check. `Order.assign_order_number()` locks `OrderSequence`. A new sequence initializes from the maximum existing order number before incrementing.

## Failure Cases

- No Restaurant row: setup validation.
- No open shift: POS error message and home surface.
- Draft cap reached: service validation and home surface.
- Invalid order type: view defaults to `DINE_IN`. Invalid guest input defaults to 1, and valid values clamp to 1..50.
