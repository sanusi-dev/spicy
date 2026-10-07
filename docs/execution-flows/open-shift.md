# Execution Flow: Open Shift

## POS Path

```text
Open shift button
  -> templates/pos/partials/gates/no_shift.html
  -> POST pos:pos_open_shift (/pos/open-shift/)
  -> views_pos.pos_open_shift()
  -> staff.forms.OpeningFloatForm
  -> staff.services.open_shift()
  -> Restaurant lock and active-shift check
  -> POSOpeningEntry + OpeningPayment rows
  -> POSOpeningEntry.full_clean() and submit()
  -> HX target #pos-main or redirect pos:pos_home
```

`OpeningFloatForm` dynamically discovers enabled modes and treats missing fields as zero. The view rejects an empty enabled-mode set, but zero total opening balances are allowed.

## Database and Side Effects

`open_shift()` is atomic. It locks the singleton Restaurant row. It checks for `status=SUBMITTED` with no closing link. It creates the opening row and one child row per supplied mode. It validates, then calls `POSOpeningEntry.submit()`. `submit()` repeats the one-open-shift check while holding locks to close the concurrent POST race.

The shift is globally shared. The opening cashier is recorded, but later POS operations do not require that same user.

## Failure Cases

- Missing Restaurant settings: validation error and POS home gate.
- No enabled payment modes: the view shows an error and does not call the service.
- Existing open shift: service validation rejects the request.
- Invalid or negative Decimal inputs: form validation rejects them.
- Concurrent open requests: Restaurant/open-shift locks serialize them.

## Backoffice Variant

All `staff.views` shift pages are `@backoffice_required` (Manager/Admin only). Cashiers use the POS route. `staff.views.opening_entry_create()` directly creates a draft and child rows through `_save_opening_entry()`. The detail page can edit draft rows. `opening_entry_submit()` calls `full_clean()` then `entry.submit()`. This is a separate path from `open_shift()` and does not share its explicit Restaurant-exists check.
