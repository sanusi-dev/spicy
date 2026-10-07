# Shift Workflow

## Domain Objects

`POSOpeningEntry` is the global shift parent. `OpeningPayment` stores one opening balance per enabled payment mode. `POSClosingEntry` is a one-to-one close document. `ClosingPayment` stores counted, expected, and difference values. The opening remains `SUBMITTED` after close. `closing_entry_id` determines whether it is open or closed.

## Opening

### POS path

`templates/pos/partials/gates/no_shift.html` renders one amount input per enabled `ModeOfPayment`. `pos_open_shift()` binds `OpeningFloatForm`, requires at least one enabled mode, and calls `staff.services.open_shift()`. `POSOpeningEntry.submit()` enforces at most one cash-type mode per shift — a single drawer backs change and the close variance, so multi-drawer change attribution cannot arise.

`open_shift()` (`apps/staff/services.py:99-124`) runs atomically, locks `Restaurant`, checks for an existing open shift, creates the opening and child rows, validates the entry, and calls `POSOpeningEntry.submit()`. The model method repeats the one-open-shift check while holding the Restaurant and open-shift locks.

### Backoffice path

All backoffice shift pages (`apps/staff/views.py`) are `@backoffice_required`. Only superusers, Spicy Admins, and Spicy Managers pass. Cashiers get 403. The POS open/close routes remain the cashier-facing path.

`staff.views.opening_entry_create()` and `_save_opening_entry()` create a draft and bulk-create `OpeningPayment` rows. `opening_entry_detail()` lets a draft be edited by replacing its child rows. `opening_entry_submit()` calls `full_clean()` and then `entry.submit()`.

⚠️ Requires verification: the backoffice create path does not call `staff.services.open_shift()` and can therefore bypass that service's explicit "Restaurant settings exist" check. The model submit path still enforces the one-open-shift rule.

## Open Shift Rules

- Exactly one global open shift is intended. It is never one per cashier.
- Any staff-role user can use the active shift for orders. Closing is restricted to the cashier who opened it, or any Manager/Admin. The POS hides the Close Shift action from other cashiers. Both the view and `submit_closing_entry()` enforce the rule through `POSOpeningEntry.can_be_closed_by()`.
- New orders, settlement, and close all validate `status=SUBMITTED` and `closing_entry IS NULL`.
- Opening cancellation is blocked once any order row exists, including cancelled or discarded rows.
- A closed opening cannot be cancelled. Cancel the closing entry instead.

## Closing

POS GET `/pos/close-shift/` computes expected values without creating database rows. The view first checks that the requester opened the shift or is a Manager/Admin. Other cashiers are sent back to POS home with an error. If open drafts exist, it renders a blocking page. POS POST locks the opening row, rechecks drafts, and creates or reuses a closing draft. It saves counted amounts and updates the period end. It then calls `submit_closing_entry()`, which re-checks the same ownership rule against the actor.

Backoffice `closing_entry_create()` locks the open shift to prevent duplicate closing drafts. The detail page edits draft counted amounts. Both POS and backoffice ultimately call the same closing service. The backoffice pages are manager/admin-only.

`submit_closing_entry()`:

1. Locks the closing and opening rows.
2. Sets the authoritative period end to submit time.
3. Blocks any open draft orders.
4. Aggregates submitted non-return orders in the period.
5. Stores the frozen shift sales: bill count, item qty, net total, grand total, plus refunded total (abs sum of submitted returns in the same period).
6. Computes expected per-mode amounts as opening float plus order payments, less cash change, less submitted-return refunds, and less submitted cash-outs per mode.
7. Validates counted amounts. Each must be non-negative. A non-cash mode's counted amount may not exceed its expected amount. An electronic total above what was processed is a bad count, and it is not drawer money. Cash surpluses are allowed and flow into the variance gate.
8. Stores closing differences as `closing_amount - expected_amount`.
9. Applies the variance approval gate: when the absolute `total_short_excess` exceeds `Restaurant.variance_approval_threshold`, a non-empty `variance_note` and a Manager/Admin actor are required.
10. Submits the closing and links it to the opening.
11. Posts the cash variance. When any `ClosingPayment.difference` is non-zero, `accounting.services.post_cash_variance_gl` creates and submits a balanced JournalEntry. One leg per affected payment mode. Each mode's own mapped account (cash or bank) takes its drawer's difference, netting against the shortage/over-short accounts. The journal links via `POSClosingEntry.variance_journal_entry`. The close fails closed. When the account matching an existing variance sign (`cash_shortage_account` / `cash_over_short_account`) is unconfigured, submission is rejected with an error. The close does not finish with the variance unposted.

Returns are excluded from drawer totals. Cancelled orders are excluded through `submitted_in_shift()`.

The closing detail page shows the five stored sales figures (Bills, Item qty, Net total, Grand total, Refunded total). The list shows Net sales (`grand_total`). Cancelling a close does not touch the stored sales fields. A re-submit recomputes them.

## Shift cash-outs

`ShiftCashOut` records cash leaving the drawer mid-shift for non-stock reasons (transport, ice, petty repairs). There is no draft state: recording creates a SUBMITTED voucher immediately, because the shift close itself is the review point. Cancellation is manager/admin only and is refused once the shift is closed.

- Validation: cash modes only, enabled, and declared in the shift's `opening_payments`. Amount `> 0`. Reason OTHER requires a note. The shift must be open.
- Expected drawer: submitted cash-outs subtract per mode alongside collected-minus-refunded maths, so `submit_closing_entry` picks them up automatically with no stored-field change. Cancelling the close leaves vouchers intact. A re-submit recomputes.
- GL (`voucher_type="Shift Cash-Out"`, `voucher_no` = row pk): Dr `Restaurant.petty_cash_expense_account` (falling back to `default_expense_account`, failing closed when neither is set) / Cr the cash mode's GL mapping. Cancel posts mirrored negated legs via the standard reversal helper.
- POS: record dialog plus per-row cancel on the shift screens. The backoffice closing detail lists the shift's vouchers read-only with cancel-while-open. Daily P&L does not read cash-outs.

## Closing Cancellation

`POSClosingEntry.cancel()` marks only the closing row cancelled. It does not reopen the opening. It is blocked if a newer open shift exists, because reopening an older period would overlap the live shift. When the close posted a variance JournalEntry, cancelling first reverses that journal (mirrored negated GL rows).

## Failure Cases and Risks

- POST close can create and commit a draft closing entry before invalid form data is rendered.
- The service validates that closing modes were declared at opening but does not require exactly one closing row for every opening row.
- Closing is serialized by opening/closing row locks, but model-level cancel methods do not explicitly lock before their checks.
