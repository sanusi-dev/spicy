# Hidden Side Effects

## Request and Middleware Effects

| Trigger | Code | Effect |
|---|---|---|
| Any request without a session | `LoginRequiredMiddleware` | Redirects to `settings.LOGIN_URL?next=...` unless the view is `@login_not_required`. |
| HTMX response with Django messages | `apps/web/middleware.py:15-40` | Adds `showMessages` JSON to `HX-Trigger`; for a redirect with queued messages, sets `HX-Redirect` so the toast survives the full page navigation. |
| Backoffice template context | `apps/web/context_processors.py:31-35` | Counts `ItemGroup` rows for navigation. |
| Every template context | `apps/web/context_processors.py:10-28` | Adds metadata, page URLs, and CSRF cookie name. |

## App Startup and Signals

- `apps/users/apps.py:9-22` registers a `post_migrate` callback that creates the three Spicy groups and imports user signal receivers.
- `apps/inventory/apps.py:9-62` registers a `post_migrate` callback that seeds standard UOMs and item groups.
- `apps/users/signals.py:26-45` sends an admin email after allauth signup and promotes a confirmed email address to primary.
- `apps/users/signals.py:47-63` deletes old avatar files before user save and the current avatar after user deletion.

## Model Save/Delete Effects

- `Order.save()` fills `arrived_time` on insert, then creates `invoice_number` using `Restaurant.invoice_series_prefix`. It rejects bypassed lifecycle and historical edits.
- `delete_unsent_draft()` locks the persisted row, checks draft/unprinted/unsent state, releases drink reservations, keeps the order row and item lines, stamps `discarded_by`/`discarded_at`, and appends an `ORDER_DELETED` audit event with the actor and an item snapshot. `Order.delete()` refuses hard deletion, so audit events can never be purged.
- `OrderItem.save()` snapshots name, department, and stock flag and calculates `amount = qty * rate`.
- `OrderPayment.save()` normalizes references, checks amount precision, allows negative amounts only on return orders, rejects duplicate non-cash references, rejects blank non-cash references when `Restaurant.require_payment_reference` is enabled, and enforces draft/KOT editability.
- `KOT.save()` and `KOTItem.save/delete()` protect ticket snapshots while allowing print/status updates through the service path.
- `Item.save()` generates `ITEM-####` codes under a lock, converts variant templates to non-sellable/non-stock, and deletes add-on relationships when an item becomes non-sales.
- `StockLedgerEntry._create_entry_locked()` both inserts the movement and updates the matching `Bin` snapshot (actual qty and WAC). Purchase-receipt submit may pass an explicit inbound value so WAC blends on as-bought money rather than `qty × unit_rate`.

## Service Side Effects

- Order line add/update/clear/cancel/discard/delete paths adjust `Bin.reserved_qty` for DRINKS.
- Settlement creates `OrderPayment`, updates order totals/status, sets `invoice_printed*` (the receipt event), releases reservations, and creates negative POS SLE rows for drinks; the view then calls `printing.print_receipt(order)` non-blockingly.
- Cancelling submitted orders creates positive stock reversal SLE rows and cancellation KOTs.
- Return submission restores drink stock with positive `POS Return` SLEs at the source sale's settle-time WAC (unless `not_restockable`), posts refund GL (per-department Sales Returns debit, payment credit, COGS reversal at the same WAC), creates negative refund `OrderPayment` rows proportional to the source net tenders, sets a negative `paid_amount`, and the refund rows reduce the shift-close expected drawer.
- Creating tickets snapshots current order lines; printing changes only KOT print status after the print interface returns.
- Shift close calculates payment totals from order payment rows (excluding returns) minus refunds, and links the opening entry to the closing entry.
- Inventory submission creates SLE rows, updates Bins, and updates `Item.last_purchase_rate` where applicable.

## Frontend Event Effects

- `assets/javascript/toast.js` consumes initial `#django-messages` JSON and the HTMX `showMessages` event.
- `assets/javascript/order-details-drawer.js` removes the drawer DOM node after the leave transition and restores focus to its source row.
- `assets/javascript/searchable-select.js` reinitializes Tom Select after HTMX swaps.
- `templates/pos/index.html` returns header/footer navigation with `hx-swap-oob` because those elements sit outside `#pos-main`.
- `templates/pos/partials/cart/totals.html` uses a hidden HTMX button triggered from inline SweetAlert code to clear the order.

## Non-Transactional Side Effects

Settlement commits before `printing.print_receipt()`, and print failures never roll the sale back. Ticket creation commits before each physical ticket attempt. Therefore database state can say "printed" or "pending" independently of a real device outcome. The current stub always succeeds, so real printer failure behavior is only represented by tests and the abstraction contract.
