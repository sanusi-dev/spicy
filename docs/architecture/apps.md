# App Responsibilities

## App Map

| App | Owns | Important implementation | Depends on |
|---|---|---|---|
| `apps.users` | `CustomUser`, roles, profile/avatar, auth customization | `models.py`, `signals.py`, `forms.py`, `adapter.py` | allauth, Django auth |
| `apps.settings` | singleton restaurant configuration and production stations | `Restaurant`, `ProductionUnit`, `views.py` | inventory, menu, users, accounting (GL FKs) |
| `apps.inventory` | item master, warehouses, bins, PWAC ledger, stock documents | `models.py`, `services.py`, `views.py` | settings, menu through validation, accounting (GL FKs) |
| `apps.menu` | menus, priced menu lines, add-ons, variant links | `models.py`, `views.py`, `management/commands/seed_menu_catalog.py` | inventory |
| `apps.payments` | payment method master and GL mapping | `models.py`, `views.py`, `forms.py` | accounting (LedgerAccount FK) |
| `apps.staff` | opening/closing shift documents and drawer reconciliation | `models.py`, `services.py`, `views.py` | users, payments, orders, settings, accounting (variance JE) |
| `apps.orders` | orders, order lines/payments, KOT/BOT snapshots, POS orchestration | `models.py`, `services.py`, `views_pos.py`, `views.py` | inventory, menu, payments, settings, staff, users, accounting (order GL) |
| `apps.accounting` | chart of accounts, GL entries, journal entries, fiscal years, supplier payables | `models.py`, `services.py`, `views.py` | orders, payments, settings, inventory (read-side) |
| `apps.reports` | Daily P&L snapshot, P&L settings, sales reports, POS register, GL/trial balance/simple P&L | `models.py`, `services.py`, `views.py`, `sales_reports.py`, `sales_breakdown_reports.py`, `accounting_reports.py` | orders, inventory, staff, accounting (fiscal year), settings |
| `apps.web` | landing, role redirect, shared middleware/context/template tags | `views.py`, `middleware.py`, `context_processors.py` | users, inventory navigation |
| `apps.utils` | timestamp base model and styled forms | `models.py`, `forms.py` | Django only |

## Implementation Index by App

### `apps.users`

- URLs: `users/urls.py` mounts profile display and avatar upload under `/users/`.
- Views/forms: `users/views.py`, `CustomUserChangeForm`, `UploadAvatarForm`, and allauth form overrides in `users/forms.py`.
- Models/helpers: `CustomUser` role/avatar properties in `users/models.py`; image and email helpers in `users/helpers.py`.
- Signals: group-cache invalidation, signup admin notification, primary-email update, and avatar file cleanup in `users/signals.py`.
- Templates/frontend: `templates/account/*`, `templates/web/pending_approval.html`, and shared `logout.js` behavior.
- Side effects: `post_migrate` creates role groups; avatar replacement/deletion removes files.

### `apps.settings`

- URLs: `settings/urls.py` exposes Restaurant settings, staff role list/assignment, and ProductionUnit CRUD under `/backoffice/settings/`.
- Models/forms: `Restaurant` and `ProductionUnit` in `settings/models.py`; `RestaurantForm` and `ProductionUnitForm` in `settings/forms.py`.
- Services/signals: no dedicated service module or model signals; model `clean()` performs cross-app configuration checks.
- Templates/frontend: `templates/backoffice/settings/*`; staff rows and production-unit deletion use HTMX, while filters use Alpine or normal navigation.
- Side effects: Restaurant validation queries open orders and draft stock documents; role assignment modifies Django group memberships.

### `apps.inventory`

- URLs: `inventory/urls.py` exposes masters, stock documents, formset row endpoints (including item UOM conversions and purchase-receipt UOM meta/preview), ledger, and balance views under `/backoffice/inventory/`.
- Models/forms: all inventory entities are in `inventory/models.py` (including `ItemUOMConversion`); ModelForms and inline formsets are in `inventory/forms.py`.
- Services: `inventory/services.py` owns Stock Entry, Stock Reconciliation, Purchase Receipt submit/cancel and voucher reversal. Purchase-receipt submit converts as-bought qty into stock UOM and blends WAC on the as-bought amount.
- Templates/frontend: `templates/backoffice/inventory/*`; Alpine toggles purpose-specific fields and the UOM-conversion formset; HTMX adds/removes formset rows and refreshes receipt UOM/preview widgets.
- Signals/startup: `InventoryConfig.ready()` seeds UOMs and ItemGroups after migrations; no model signal receivers.
- Side effects: posting updates SLEs, Bins, and last purchase rates; cancellation posts reversals.

### `apps.menu`

- URLs: `menu/urls.py` exposes Menu, MenuItem, ItemAddOn, and ItemVariant CRUD under `/backoffice/menu/`.
- Models/forms: `menu/models.py` and `menu/forms.py`; model validation reaches into inventory Items and active menu membership.
- Services/signals: no service module or model signals; views save forms directly.
- Management: `menu/management/commands/seed_menu_catalog.py` atomically seeds master/menu data and links the active menu.
- Templates/frontend: `templates/backoffice/menu/*`; delete controls use HTMX `#app-content` swaps.
- Side effects: making an Item non-sales deletes its add-on relationships through `Item.save()` in inventory.

### `apps.payments`

- URLs: `payments/urls.py` exposes payment mode and GL mapping CRUD under `/backoffice/payments/`.
- Models/forms: `ModeOfPayment`, `PaymentGLMapping`, `ModeOfPaymentForm`, and `PaymentGLMappingForm`.
- Services/signals: no service module or signal receivers; `orders.services.settle_order()` consumes this app's master rows.
- Templates/frontend: `templates/backoffice/payments/*`; ordinary forms are full-page, while mapping deletion uses a confirmation-aware POST.
- Side effects: mode enable/default changes affect shift opening and POS checkout availability; mapping deletion can make a mode fail settlement.

### `apps.staff`

- URLs: `staff/urls.py` exposes opening/closing lists, drafts, details, submit, and cancel under `/backoffice/staff/`.
- Models/forms: opening/closing documents and child payment rows are in `staff/models.py`; forms are in `staff/forms.py`.
- Services: `staff/services.py` owns opening, closing-draft creation, expected totals, payment aggregation, and close submission.
- Templates/frontend: `templates/backoffice/staff/*` and `templates/pos/close_shift.html`; POS close uses Alpine previews and HTMX submission.
- Signals: no staff model signals.
- Side effects: close submission aggregates orders/payments and links the opening to the closing; canceling a close does not reopen the opening. Since Phase 6, a short/excess variance posts a linked JournalEntry atomically with the close (when the matching account is configured), and canceling a close reverses that journal.

### `apps.orders`

- URLs: `orders/urls.py` exposes backoffice orders/KOTs; `orders/pos_urls.py` exposes the complete POS route set under `/pos/`.
- Models/forms: orders, lines, payments, KOT snapshots, audit events, and sequence counter are in `orders/models.py`; cancellation form is in `orders/forms.py`.
- Services: `orders/services.py` owns draft/cart/settlement/cancel/delete/return (including `submit_return`), reservations, KOT creation/dispatch, and history query construction.
- Printing: `orders/printing.py` is the current success-only print interface.
- Templates/frontend: `templates/pos/*`, `templates/pos/partials/*`, and `templates/backoffice/orders/*`; `views_pos.py` selects inline fragments and `order-details-drawer.js` owns history drawer presentation.
- Signals/startup: no order model signals or AppConfig startup behavior.
- Side effects: order services write payment, stock, KOT, audit, reservation, receipt-print, and session-related state. Since Phase 6, settlement also posts GL (income, payment, round-off, COGS) via `accounting.services.post_order_gl`, and returns post refund GL rebuilt from the returned lines via `post_refund_gl`.

### `apps.accounting`

- URLs: `accounting/urls.py` exposes the dashboard, chart of accounts, journal entries, read-only GL entries, and fiscal years under `/backoffice/accounting/`, all behind the manager gate.
- Models/forms: `LedgerAccount`, `FiscalYear`, `GLEntry`, `JournalEntry`, `JournalEntryAccount` in `accounting/models.py`; forms in `accounting/forms.py` (journal rows use an inline formset).
- Services: `accounting/services.py` owns order settle GL (`post_order_gl`), cancellation reversal (`reverse_order_gl`), refund GL (`post_refund_gl`), and shift-close cash variance posting (`post_cash_variance_gl`).
- Templates/frontend: `templates/backoffice/accounting/*`; the chart of accounts is a recursive tree with expand/collapse, opening journals go through a read-only review screen before submit, journal entries use the standard formset add/remove pattern, and GL entries are a filtered read-only table.
- Side effects: `GLEntry` is immutable — reversal postings mark originals cancelled and write mirror rows dated the cancellation day. `JournalEntry.submit()` posts to the GL; `cancel()` posts reversals dated today; `amend()` copies a cancelled entry into a new draft dated today, once per cancelled entry.
- Management: `accounting/management/commands/seed_chart_of_accounts.py` idempotently seeds the chart and current fiscal year, and fills Restaurant/warehouse/production-unit/payment GL FKs only when they are currently null.

### `apps.reports`

- URLs: `reports/urls.py` exposes the Daily P&L register, draft/detail/submit/cancel/amend, HTMX preview and formset row endpoints, P&L settings, and query reports (sales, POS register, GL, trial balance, simple P&L) under `/backoffice/reports/`, all behind the manager gate.
- Models/forms: `PnLConfiguration` singleton, `PnLMaterial`, `PnLRecurringExpense` in `reports/models.py`; `DailyPnL` and snapshot/input children in `reports/pnl_models.py`. Query reports have no models.
- Services: `reports/services.py` builds the three-column Daily P&L statement (`compute_daily_pnl`) and freezes it on submit (`submit_daily_pnl`). Submit does not post GL. Daily P&L source queries live in `reports/sources.py`. Sales aggregations live in `reports/sales_reports.py` (period, average bill, cancelled) and `reports/sales_breakdown_reports.py` (item, employee, service, time); GL/trial balance/simple P&L in `reports/accounting_reports.py`; POS register in `reports/register_reports.py`. Views are `views.py` (Daily P&L) and `report_views.py` (query reports).
- Templates/frontend: `templates/backoffice/reports/*`; the Daily P&L statement partial is shared by draft preview and submitted detail. Query reports are GET filter forms plus tables.
- Side effects: none on other apps. Recurring rates are snapshotted onto the submitted Daily P&L so later settings edits do not rewrite history.

### `apps.web`

- URLs: `web/urls.py` exposes landing, role redirect, backoffice dashboard, pending approval, and the shadowed `web:pos_index` route.
- Views/utilities: `web/views.py`, `middleware.py`, `context_processors.py`, `meta.py`, and template tags under `web/templatetags/`.
- Services/forms/signals: no business service or model form; middleware and context processors are the cross-cutting layer.
- Templates/frontend: `templates/web/*`, `templates/web/app/app_base.html`, and global `site.js` imports.
- Side effects: middleware applies route gates and serializes Django messages into HTMX triggers; context processors query metadata and item-group count.

### `apps.utils`

- URLs/views/templates: none; it is a shared code package, not a separately installed app.
- Models/forms: `BaseModel`, `StyledModelForm`, `active_choices`, `add_formset_row`/`remove_formset_row` (HTMX formset row endpoints rebuild bound formsets from posted data), and Tailwind widget constants in `utils/models.py` and `utils/forms.py`.
- Rounding: `utils/rounding.py` owns every money mode — `money()` (2 dp, half-even), `cash_round()` (whole naira, half-up), `percent()` (3 dp, half-even), and the shared `TWO_PLACES` quantity constant.
- Side effects: form initialization styles widgets and preserves current disabled choices in select querysets.

## Management Commands and Background Work

- `users`: `promote_user_to_superuser` changes a named user's Django superuser/staff flags.
- `menu`: `seed_menu_catalog [--force]` seeds items, variants, menu lines, add-ons, and the active menu in one transaction.
- `inventory`: `backfill_item_images` assigns the default media image to items with no image.
- `orders`: `seed_pos_setup` creates the Restaurant/warehouse/payment/production-unit configuration chain, invokes the chart-of-accounts seed first, and seeds the menu when needed.
- `accounting`: `seed_chart_of_accounts` seeds the chart, current fiscal year, and GL wiring (idempotent).
- `web`: `bootstrap_celery_tasks [--remove-stale]` synchronizes `settings.SCHEDULED_TASKS` to django-celery-beat; `send_test_email` exercises the configured email backend.
- Celery is initialized in `spicy/celery.py` and points at Redis, but no project `tasks.py` module was found and `SCHEDULED_TASKS` is empty. There is no active periodic ticket or notification worker.

## Users

`apps/users/models.py` extends `AbstractUser` with avatar storage and plain role properties derived from Django groups via `groups.filter(...).exists()`. `apps/users/decorators.py` turns them into `backoffice_required`, `manager_required`, `staff_required`, and `admin_required` view decorators. `UserConfig.ready()` seeds three role groups after migrations and imports `apps/users/signals.py`. Profile editing is in `apps/users/views.py`; allauth owns login, signup, and logout routes.

## Settings

`Restaurant` is the single installation record. It points to the active menu, central Store warehouse, and Bar/POS warehouse and controls the open-draft cap and full-history permission. `ProductionUnit` represents FOOD/Kitchen or DRINKS/Bar routing and stores printer metadata. Staff role assignment is also hosted here in `apps/settings/views.py`.

## Inventory

Inventory is both a master-data app and a posting engine. `Item` is shared by menu and order lines. `Bin` is the current item/warehouse snapshot; `StockLedgerEntry` is the movement history. Stock Entry, Stock Reconciliation, and Purchase Receipt are draft documents whose service functions create immutable-by-convention ledger movements and reversal rows.

## Menu

`MenuItem.rate` is the POS selling price. Menu lines snapshot the displayed name and link to `inventory.Item`. `ItemAddOn` and `ItemVariant` are relationship models. Add-ons are resolved in the active menu at POS time. The current POS has an add-on dialog but no parent-item variant selection flow.

## Payments

The payments app does not own sale payment rows. It owns `ModeOfPayment` and one-to-one `PaymentGLMapping`. `OrderPayment` is in `apps/orders/models.py` and is created during `settle_order()`.

## Staff

`POSOpeningEntry` and `POSClosingEntry` define the global shift window. Child opening/closing rows snapshot each payment mode. `staff.services` computes expected drawer values from submitted orders and order payment rows.

## Orders

Orders are the integration hub. `Order` references the shift and optional stock warehouse; `OrderItem` references inventory and menu snapshots; `OrderPayment` references payment modes; `KOT` references production units; and order services call inventory and staff models directly. The POS is implemented in `views_pos.py` and uses the same order services as backoffice actions.

## Web and Utilities

`apps/web` is the shell rather than a business domain. Its middleware applies route-level access gates and forwards Django messages into HTMX headers. `apps/utils` is a shared base/form package and is not a separate installed Django app. Most domain models inherit `BaseModel`; `users.CustomUser` inherits Django's `AbstractUser` instead.

## Views, Forms, Templates, and Services

The normal pattern is function-based views plus ModelForms. Inventory document creation uses inline formsets and HTMX row add/remove endpoints. Complex cross-record mutations are service functions, generally decorated with `transaction.atomic`. POS responses use inline partials from `templates/pos/*.html`; backoffice pages mostly use full-page templates with selected HTMX fragments.

## Missing or Distributed Domains

- Sales reporting for operations lives in `apps.reports` query reports. The order dashboard, POS history, stock reports, and shift totals remain as their own surfaces.
- Receipts are represented by order fields and a print interface. There is no receipt model or renderer.
- Customers are represented by `customer_name` and customer indices. There is no customer master.
- Printing configuration is in `ProductionUnit`, while print calls are in `apps/orders/printing.py`.
