# Execution Flow: Make Payment

## Dialog Load

```text
Pay link
  -> hx-get pos:pos_order_settle
  -> views_pos.pos_order_settle(GET)
  -> _get_settle_payment_modes()
  -> render pos/partials/payment/dialog.html
  -> swap #payment-dialog-container
  -> Alpine posModalDialog initializes focus/close state
```

The GET does not settle or mutate data. It lists enabled modes with a non-null mapping and non-empty mapping account.

## POST

The dialog sends fields named `payment_<mode_id>` and `reference_<mode_id>`. Non-cash modes render a reference input. The browser requires it only when a positive amount is entered and `Restaurant.require_payment_reference` is enabled. The view drops blank amounts and passes a list of dictionaries to `settle_order()`.

`_validate_payment_data()` resolves each mode. It checks enabled/opening declaration/GL mapping. It normalizes references. It enforces two-decimal finite amounts. It rejects a blank reference on non-cash rows when `Restaurant.require_payment_reference` is enabled. It discards zero rows. At least one positive row is required.

`settle_order()` locks the order and recalculates the rounded total. It validates active shift/stock and creates `OrderPayment` rows. It sets `paid_amount`, `change_amount`, `is_paid`, `status=SUBMITTED`, `submitted_at`, and `invoice_printed*` (the receipt event). It re-stamps `posting_date`/`posting_time` to the settlement moment. It then converts drink reservations to actual stock issues. The view prints the receipt after settlement, non-blockingly.

## Frontend Result

Success clears `pos_order_id` and that order's active-card session entry, shows a message, and redirects to `pos_home`. Validation shows a Django message and redirects back to the order screen. The payment dialog itself is not re-rendered with bound errors.

## Failure Tracing

If the mode is visible but settlement rejects it, compare the global enabled/mapped set used by `_get_settle_payment_modes()` with the opening mode IDs required by `_validate_payment_data()`. If payment exists but the order is still draft, inspect transaction rollback and any validation after payment creation.
