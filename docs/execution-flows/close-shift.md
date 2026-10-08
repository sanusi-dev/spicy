# Execution Flow: Close Shift

## POS GET Preview

```text
Close shift navigation (rendered only for the shift opener or a Manager/Admin)
  -> templates/pos/base.html
  -> GET pos:pos_close_shift (/pos/close-shift/)
  -> views_pos.pos_close_shift()
  -> _get_open_shift()
  -> POSOpeningEntry.can_be_closed_by()
  -> Order.objects.open_drafts()
  -> Order.objects.cancelled_in_shift()
  -> staff.services.expected_closing_amounts()
  -> templates/pos/close_shift.html#surface
```

GET never creates closing rows. It renders a client-side count preview. If drafts remain, it returns the blocking "Finish open orders first" surface. When the shift has cancelled-after-send orders, the surface carries a blocking review block listing them with a confirmation checkbox.

## POS POST Submission

```text
Counted amount form
  -> hx-post pos:pos_close_shift
  -> views_pos.pos_close_shift()
  -> atomic block and lock POSOpeningEntry
  -> recheck open drafts
  -> staff.services.ensure_closing_draft()
  -> ClosingPaymentForm per opening mode (server-side: counted >= 0)
  -> reject unticked review of cancelled-after-send orders (typed amounts stay on the re-rendered form)
  -> save counted values, variance_note, and closing period
  -> staff.services.submit_closing_entry()
  -> lock POSClosingEntry and POSOpeningEntry
  -> re-check can_be_closed_by(actor)
  -> aggregate orders/payments
  -> recompute expected and calculate differences
  -> reject non-cash counted above expected
  -> apply the variance note/threshold gate
  -> submit closing and link opening
  -> HX home surface or redirect
```

`submit_closing_entry()` recalculates the authoritative period end at submission. It includes submitted non-return orders in the period. It computes expected values from opening balances plus payments, minus cash change, minus submitted-return refunds, and minus submitted shift cash-outs in the period. It writes the frozen sales fields (`bill_count`, `total_quantity`, `net_total`, `grand_total`, `refunded_total`) and `total_short_excess`. It marks the close submitted and sets `opening_entry.closing_entry`.

## Rollback and Error Behavior

The POST transaction rolls back model changes from the current request if an exception escapes. Invalid forms can nevertheless leave a committed draft closing entry, because `ensure_closing_draft()` runs before bound-form validation finishes. A later retry reuses that draft.

## Backoffice Variant

All `staff.views` shift pages are `@backoffice_required` (Manager/Admin only). Cashiers use the POS route. `staff.views.closing_entry_create()` locks the open shift and creates/reuses a draft. `closing_entry_detail()` edits counted values. `closing_entry_submit()` calls `full_clean()` and the same closing service. `POSClosingEntry.cancel()` only cancels the close. It does not reopen the shift.
